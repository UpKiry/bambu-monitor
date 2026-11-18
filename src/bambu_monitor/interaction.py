"""Local controlling-TTY interactions; never fall back to stdin or echoed secrets."""
import getpass
import io
import os
import warnings

from .auth import Secret


class InteractionError(ValueError):
    pass


class PrivateTTY:
    def __init__(self):
        self._stream = None

    def __enter__(self):
        # POSIX controlling tty also works when stdout is redirected to NDJSON.
        # Windows is left explicit pending a matching private-console implementation.
        if os.name != "posix":
            raise InteractionError("private_tty_unavailable")
        try:
            descriptor = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
            self._stream = io.TextIOWrapper(io.FileIO(descriptor, mode="r+", closefd=True),
                                           encoding="utf-8", line_buffering=True)
            if not self._stream.isatty():
                raise OSError
        except OSError:
            if self._stream is not None:
                self._stream.close()
            raise InteractionError("private_tty_required") from None
        return self

    def __exit__(self, *_):
        if self._stream is not None:
            self._stream.close()

    def tell(self, message):
        self._stream.write(message + "\n")
        self._stream.flush()

    def secret(self, prompt):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass(prompt, stream=self._stream)
        except (getpass.GetPassWarning, OSError, EOFError, io.UnsupportedOperation):
            raise InteractionError("private_input_unavailable") from None
        if not value or len(value) > 16384 or any(char in value for char in ("\x00", "\r", "\n")):
            raise InteractionError("invalid_private_input")
        return Secret(value)

    def choice(self, prompt, allowed):
        self._stream.write(prompt)
        self._stream.flush()
        value = self._stream.readline(129)
        if not value.endswith("\n") or value.strip() not in allowed:
            raise InteractionError("invalid_selection")
        return value.strip()


def authenticate(auth, ui, *, method="password", allow_restore=True):
    """At most one login attempt per stage and one explicitly requested code send."""
    if allow_restore and auth.restore():
        return True
    account = ui.secret("Bambu 账号（国区可填 11 位手机号或邮箱，隐藏输入）：")
    if method in ("email-code", "sms-code"):
        channel = "短信" if method == "sms-code" else "邮箱"
        send = auth.send_sms_code if method == "sms-code" else auth.send_email_code
        if ui.choice(f"发送一次{channel}验证码？输入 send 或 cancel：", ("send", "cancel")) != "send":
            raise InteractionError("login_cancelled")
        if not send(account):
            raise InteractionError("code_send_failed")
        return auth.login(account, ui.secret("验证码（隐藏输入）："), method="code")
    if method != "password":
        raise InteractionError("unsupported_login_method")
    if auth.login(account, ui.secret("密码（隐藏输入）：")):
        return True
    if auth.reason == "code_required":
        ui.tell("账号需要验证码；手机号请查看短信，邮箱账号请查看邮件。若已经收到可直接输入。")
        choice = ui.choice("输入 received、send（发一次）或 cancel：", ("received", "send", "cancel"))
        if choice == "cancel":
            raise InteractionError("login_cancelled")
        if choice == "send" and not auth.send_code(account):
            raise InteractionError("code_send_failed")
        return auth.login(account, ui.secret("验证码（隐藏输入）："), method="code")
    return False


def select_device(auth, devices, configured_id, ui):
    if configured_id:
        device = auth.select(devices, configured_id)
        if device is None:
            raise InteractionError("selected_device_unavailable")
        return device
    if not devices:
        raise InteractionError("no_bound_devices")
    ui.tell("请选择本次监控的打印机：")
    for index, device in enumerate(devices, 1):
        # Never render account names, raw cloud device names, or full device IDs.
        ui.tell(f"{index}. {device.model or '机型未知'} · 设备尾号 {device.id[-4:]}")
    value = ui.choice("输入编号，或 cancel：", tuple(str(i) for i in range(1, len(devices) + 1)) + ("cancel",))
    if value == "cancel":
        raise InteractionError("selection_cancelled")
    device = auth.select(devices, devices[int(value) - 1].id)
    if device is None:
        raise InteractionError("selected_device_unavailable")
    return device
