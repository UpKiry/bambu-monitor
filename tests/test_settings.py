"""Settings atomicity, user-content preservation and path admission tests."""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from bambu_monitor.config import Config, load_config
from bambu_monitor.settings import (Settings, SettingsConflict, SettingsError,
                                    default_settings_path, new_credential_ref)

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "anchored settings I/O is POSIX only")
class SettingsTests(unittest.TestCase):
    def setUp(self):
        # Keep test file mutations inside the authorized workspace. Do not use
        # /tmp aliases, which may themselves be symlinks on macOS.
        self.directory = tempfile.TemporaryDirectory(prefix=".settings-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.path = self.base / "private/provider.toml"
        self.config = Config("cn", "SYNTHETIC-P1", new_credential_ref(), 75, 2, 45, False)

    def save(self):
        value = Settings(self.path)
        value.save(self.config)
        return value

    def test_missing_load_has_no_side_effects_and_create_roundtrip_matches_config_loader(self):
        value = Settings(self.path)
        self.assertIsNone(value.load())
        self.assertFalse(self.path.parent.exists())
        value.save(self.config)
        self.assertEqual(Settings(self.path).load(), self.config)
        self.assertEqual(load_config(self.path), self.config)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_existing_config_must_be_explicitly_loaded_before_overwrite(self):
        self.save()
        value = Settings(self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SettingsError, "settings_load_required"):
            value.save(replace(self.config, region="global"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(value.load(), self.config)
        new = replace(self.config, region="global")
        value.save(new)
        self.assertEqual(value.load(), new)
        value.save(self.config)
        self.assertEqual(value.load(), self.config)

    def test_cancel_load_does_not_rewrite_user_format_comments_or_mtime(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text('# user formatting\n[provider]\nkind="bambu-cloud"\nregion="global"\n')
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        value = Settings(self.path)
        self.assertEqual(value.load(), Config("global"))
        del value
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_external_content_change_conflicts_and_does_not_replace(self):
        value = self.save()
        self.assertEqual(value.load(), self.config)
        self.path.write_text('# changed by another program\n')
        with self.assertRaises(SettingsConflict):
            value.save(replace(self.config, region="global"))
        self.assertEqual(self.path.read_text(), '# changed by another program\n')

    def test_external_same_bytes_replacement_still_conflicts_by_identity(self):
        value = self.save()
        replacement = self.path.parent / "external.toml"
        replacement.write_bytes(self.path.read_bytes())
        replacement.replace(self.path)
        with self.assertRaises(SettingsConflict):
            value.save(self.config)

    def test_file_created_after_missing_load_is_not_overwritten(self):
        value = Settings(self.path)
        self.assertIsNone(value.load())
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text("preserve-user-content")
        with self.assertRaises(SettingsConflict):
            value.save(self.config)
        self.assertEqual(self.path.read_text(), "preserve-user-content")

    def test_missing_after_existing_load_conflicts(self):
        value = self.save()
        # Test-owned target verified through self.path under the temp root.
        self.path.unlink()
        with self.assertRaises(SettingsConflict):
            value.save(self.config)
        self.assertFalse(self.path.exists())

    def test_symlink_file_or_parent_refused_and_external_contents_preserved(self):
        outside = self.base / "outside.toml"
        outside.write_text("untouched")
        self.path.parent.mkdir(mode=0o700)
        self.path.symlink_to(outside)
        for operation in (lambda: Settings(self.path).load(), lambda: Settings(self.path).save(self.config)):
            with self.assertRaises(SettingsError):
                operation()
        self.assertEqual(outside.read_text(), "untouched")
        alias = self.base / "alias"
        alias.symlink_to(self.path.parent, target_is_directory=True)
        for operation in (lambda: Settings(alias / "new.toml").load(),
                          lambda: Settings(alias / "new.toml").save(self.config)):
            with self.assertRaises(SettingsError):
                operation()
        self.assertFalse((self.path.parent / "new.toml").exists())

    def test_lock_symlink_and_hardlinked_target_refused(self):
        value = self.save()
        hardlink = self.base / "hardlink.toml"
        os.link(self.path, hardlink)
        with self.assertRaisesRegex(SettingsError, "settings_unsafe_path"):
            value.load()
        hardlink.unlink()
        lock = self.path.parent / ".provider.toml.lock"
        lock.unlink()
        outside = self.base / "outside-lock"
        outside.write_text("untouched")
        lock.symlink_to(outside)
        with self.assertRaises(SettingsError):
            value.save(self.config)
        self.assertEqual(outside.read_text(), "untouched")

    def test_unsafe_writable_directory_file_or_special_target_refused(self):
        self.save()
        self.path.chmod(0o666)
        with self.assertRaisesRegex(SettingsError, "settings_unsafe_path"):
            Settings(self.path).load()
        self.path.chmod(0o600)
        self.path.parent.chmod(0o777)
        try:
            with self.assertRaisesRegex(SettingsError, "settings_unsafe_path"):
                Settings(self.path).load()
        finally:
            self.path.parent.chmod(0o700)
        fifo = self.path.parent / "fifo.toml"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(SettingsError, "settings_unsafe_path"):
            Settings(fifo).load()

    def test_unknown_secret_invalid_and_oversized_config_never_becomes_loaded_baseline(self):
        self.path.parent.mkdir(mode=0o700)
        for text in ('[provider]\nkind="bambu-cloud"\nregion="cn"\npassword="SYNTHETIC-CANARY"\n',
                     '[provider]\nkind="bambu-cloud"\nregion="auto"\n',
                     '[provider]\nkind="bambu-cloud"\nregion="cn"\n[freshness]\nliveness_seconds=nan\n',
                     'x' * 16385):
            self.path.write_text(text)
            value = Settings(self.path)
            with self.assertRaises(SettingsError) as caught:
                value.load()
            self.assertNotIn("SYNTHETIC-CANARY", str(caught.exception))
            with self.assertRaisesRegex(SettingsError, "settings_load_required|settings_too_large"):
                value.save(self.config)
            self.assertEqual(self.path.read_text(), text)

    def test_write_failure_keeps_previous_complete_config_and_cleans_own_temp(self):
        value = self.save()
        before = self.path.read_bytes()
        with patch("bambu_monitor.settings.os.replace", side_effect=OSError("SYNTHETIC-CANARY")):
            with self.assertRaisesRegex(SettingsError, "settings_io_error") as caught:
                value.save(replace(self.config, region="global"))
        self.assertNotIn("SYNTHETIC-CANARY", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        value.save(replace(self.config, region="global"))
        self.assertEqual(value.load().region, "global")

    def test_external_change_during_temp_write_rejected_before_publish(self):
        value = self.save()
        actual_fsync = os.fsync
        mutated = False
        def fsync(descriptor):
            nonlocal mutated
            actual_fsync(descriptor)
            if not mutated:
                mutated = True
                self.path.write_text("external change during save")
        with patch("bambu_monitor.settings.os.fsync", side_effect=fsync):
            with self.assertRaises(SettingsConflict):
                value.save(replace(self.config, region="global"))
        self.assertEqual(self.path.read_text(), "external change during save")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_parent_replacement_during_save_is_detected_without_writing_new_directory(self):
        value = self.save()
        moved = self.base / "moved"
        actual_fsync = os.fsync
        mutated = False
        def fsync(descriptor):
            nonlocal mutated
            actual_fsync(descriptor)
            if not mutated:
                mutated = True
                self.path.parent.rename(moved)
                self.path.parent.mkdir(mode=0o700)
        with patch("bambu_monitor.settings.os.fsync", side_effect=fsync):
            with self.assertRaises(SettingsConflict):
                value.save(replace(self.config, region="global"))
        self.assertFalse(self.path.exists())
        self.assertEqual(Settings(moved / "provider.toml").load(), self.config)
        self.assertEqual(list(moved.glob("*.tmp")), [])

    def test_cooperating_writers_conflict_against_their_own_loaded_baselines(self):
        first = self.save()
        second = Settings(self.path)
        self.assertEqual(second.load(), self.config)
        first.save(replace(self.config, region="global"))
        with self.assertRaises(SettingsConflict):
            second.save(replace(self.config, printer_id="OTHER"))
        self.assertEqual(Settings(self.path).load().region, "global")

    def test_cooperating_writer_lock_contention_is_a_conflict_not_a_block(self):
        import fcntl
        value = self.save()
        lock = self.path.parent / ".provider.toml.lock"
        with lock.open("r+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SettingsConflict):
                value.save(self.config)
        value.save(self.config)

    def test_external_creation_at_new_file_publish_is_never_clobbered(self):
        value = Settings(self.path)
        self.assertIsNone(value.load())
        original_link = os.link
        def create_external(source, target, **kwargs):
            self.path.write_text("external created just before publication")
            return original_link(source, target, **kwargs)
        with patch("bambu_monitor.settings.os.link", side_effect=create_external):
            with self.assertRaises(SettingsConflict):
                value.save(self.config)
        self.assertEqual(self.path.read_text(), "external created just before publication")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_only_config_contract_can_be_saved_no_extra_payload(self):
        for bad in ({"region": "cn", "token": "SYNTHETIC-CANARY"}, None, "secret"):
            with self.assertRaisesRegex(SettingsError, "settings_invalid_config"):
                Settings(self.path).save(bad)
        self.assertFalse(self.path.parent.exists())
        self.save()
        text = self.path.read_text()
        self.assertNotIn("password", text)
        self.assertNotIn("accessToken", text)
        self.assertNotIn("storage", text)
        self.assertEqual(set(__import__("tomllib").loads(text)), {"provider", "credentials", "freshness", "reconnect"})


class SettingsPureTests(unittest.TestCase):
    def test_random_account_refs_are_unique_opaque_and_config_compatible(self):
        refs = {new_credential_ref() for _ in range(64)}
        self.assertEqual(len(refs), 64)
        for reference in refs:
            self.assertRegex(reference, r"^bambu-monitor/cloud/[0-9a-f]{32}$")
            self.assertEqual(Config("cn", credential_ref=reference).credential_ref, reference)

    def test_default_paths_do_not_guess_region_or_create_directories(self):
        with patch("bambu_monitor.settings.sys.platform", "darwin"), patch("bambu_monitor.settings.Path.home", return_value=Path("/example-home")):
            self.assertEqual(default_settings_path(), Path("/example-home/Library/Application Support/bambu-monitor/provider.toml"))
        with patch("bambu_monitor.settings.sys.platform", "linux"), patch.dict(os.environ, {"XDG_CONFIG_HOME": "/explicit-config"}):
            self.assertEqual(default_settings_path(), Path("/explicit-config/bambu-monitor/provider.toml"))
        with patch("bambu_monitor.settings.sys.platform", "linux"), patch("bambu_monitor.settings.Path.home", return_value=Path("/example-home")), patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative-is-ignored"}):
            self.assertEqual(default_settings_path(), Path("/example-home/.config/bambu-monitor/provider.toml"))

    def test_platform_and_path_restrictions_are_explicit_value_free(self):
        for value in ("/", "../unsafe.toml", "contains\x00null"):
            with self.assertRaises(SettingsError):
                Settings(value)
        with patch("bambu_monitor.settings.os.name", "nt"):
            with self.assertRaisesRegex(SettingsError, "settings_unsupported_platform"):
                Settings._platform()


if __name__ == "__main__":
    unittest.main()
