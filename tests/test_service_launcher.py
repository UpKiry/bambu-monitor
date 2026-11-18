"""Detached startup handshake tests; do not start an app or write login items."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bambu_monitor.service_launcher import notify, start_background


class BackgroundTests(unittest.TestCase):
    def launch(self, payload, **kwargs):
        def spawn(args, **options):
            self.assertTrue(options['start_new_session'])
            self.assertNotIn('--background', args)
            self.assertIn('--no-browser', args)
            self.assertIn('--no-auto-collect', args)
            fd = int(args[args.index('--ready-fd')+1])
            self.assertEqual(options['pass_fds'], (fd,))
            if payload is not None:
                os.write(fd, payload)
            return SimpleNamespace(pid=123456)
        with patch('bambu_monitor.macos_service.check_tray_available'), patch('bambu_monitor.service_launcher.subprocess.Popen', side_effect=spawn):
            return start_background(open_browser=False, auto_collect=False, **kwargs)

    def test_detach_waits_for_native_ready(self):
        self.assertEqual(self.launch(b'{"event":"service_ready"}\n'),
                         {'event':'service_started','pid':123456,'port':8765})

    def test_failed_child_cannot_be_reported_started(self):
        self.assertEqual(self.launch(b'{"error":"service_start_failed"}\n'),{'error':'service_start_failed'})
        self.assertEqual(self.launch(b''),{'error':'service_start_failed'})

    def test_unknown_start_does_not_trigger_another_spawn(self):
        with patch('bambu_monitor.service_launcher.select.select', return_value=([],[],[])):
            self.assertEqual(self.launch(None),{'event':'service_startup_pending','pid':123456})

    def test_cli_rejects_incompatible_actions_without_installing(self):
        import contextlib
        import io
        from bambu_monitor.cli import main
        for args in (("--install-launch-agent", "--no-auto-collect"),
                     ("--install-launch-agent", "--background"), ("--port", "0")):
            with self.subTest(args=args), patch('bambu_monitor.macos_service.install_launch_agent') as install:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['service', *args]), 2)
                install.assert_not_called()

    def test_ready_notifier_closes_only_supplied_pipe(self):
        read_fd, write_fd = os.pipe()
        try:
            notify(write_fd, {'event':'service_ready'})
            self.assertEqual(os.read(read_fd,100),b'{"event": "service_ready"}\n')
        finally:
            os.close(read_fd)
