"""Local Tk setup window. No browser server, secret logging or network on Tk's thread."""
from dataclasses import replace
from queue import Empty, Queue
from threading import Event, Lock, Thread

from .auth import Secret


MESSAGES = {
    "invalid_request": "请检查账号格式：国区使用 11 位大陆手机号或邮箱，全球区使用邮箱。验证码发码渠道需与账号类型一致。",
    "code_required": "账号需要验证码。手机号请查看短信，邮箱账号请查看邮件；也可以手动发送一次。",
    "credential_rejected": "登录未通过，请检查账号、密码或验证码后重试。",
    "mfa_unsupported": "此账号需要当前尚不支持的 MFA 验证。",
    "rate_limited": "云服务限制了请求频率，请稍后手动重试。",
    "permission_denied": "云服务拒绝访问，请检查账号区域和权限。",
    "stored_token_unavailable": "本机没有可用的登录，请重新登录。",
    "selected_device_unavailable": "已选打印机不在当前绑定列表，请重新选择。",
    "credential_store_unavailable": "系统凭据库不可用，本次只能使用内存登录。",
    "keyring_revoke_failed": "系统凭据删除失败，不能确认本机登录已移除。",
    "no_bound_devices": "账号没有可选打印机，请在 Bambu 应用中检查绑定后刷新。",
    "gui_operation_failed": "操作未完成，请检查网络或设置后重试。",
    "settings_conflict": "设置已被其他程序更改，请关闭窗口并重新打开。",
    "settings_save_failed": "设置未保存，请检查文件权限或是否被其他程序更改。原设置仍保留。",
    "settings_save_failed_cleanup_failed": "设置未保存，且新登录令牌清理失败。请检查系统密钥库。",
    "settings_saved_old_credentials_cleanup_failed": "新设置已保存，但旧登录令牌清理失败。请检查系统密钥库后重新打开窗口。",
    "settings_saved_verification_failed": "设置可能已写入，但保存核验失败。请关闭后重新打开窗口检查。",
    "settings_save_outcome_unknown": "无法确认设置是否保存；为避免覆盖已停止后续操作，请关闭窗口检查设置文件。",
    "credential_revoke_failed": "本机凭据删除失败，不能确认已退出登录。请检查系统密钥库。",
    "mqtt_dependency_unavailable": "MQTT 依赖不可用，请安装项目 cloud 依赖后重试。",
    "collector_failed": "采集发生错误，已停止。请检查网络后重试。",
    "cleanup_failed": "资源清理未完成，请关闭窗口重试；不会启动新连接。",
    "settings_unsafe_path": "设置路径不安全，请使用自己的普通目录与文件，避免符号链接。",
    "settings_unsupported_platform": "当前安全设置保存仅支持 macOS 与 Linux。",
}


def friendly(reason):
    # Never expose arbitrary upstream/OS exception strings.
    return MESSAGES.get(reason, "操作未完成，请检查网络、账号区域或设置后重试。")


def metric_text(metric, suffix=""):
    quality = metric.get("quality", "unknown")
    if quality == "unsupported":
        return "不支持"
    if quality == "unknown" or metric.get("value") is None:
        return "等待数据"
    value = str(metric["value"]) + suffix
    return value if quality == "valid" else value + "（旧值）"


class Worker:
    """One owner thread per operation; bounded result and latest-snapshot mailboxes.

    A new operation cannot start until the previous owner has finished. Secrets
    live in the submitted closure only for that operation and are never queued
    as results. Collection uses the same owner to construct/pump/stop Session.
    """
    def __init__(self):
        self.results = Queue(maxsize=1)
        self.stop = Event()
        self.thread = None
        self._latest = None
        self._lock = Lock()

    @property
    def busy(self):
        return self.thread is not None

    def submit(self, action):
        if self.busy:
            return False
        self.stop.clear()

        def execute():
            try:
                value = action()
                self.results.put((True, value))
            except Exception:
                self.results.put((False, None))
        self.thread = Thread(target=execute, name="bambu-setup-owner", daemon=False)
        self.thread.start()
        return True

    def result(self):
        try:
            result = self.results.get_nowait()
        except Empty:
            return None
        # The thread has completed all resource work before publishing its result.
        self.thread.join()
        self.thread = None
        return result

    def emit(self, envelope):
        with self._lock:
            self._latest = envelope

    def snapshot(self):
        with self._lock:
            value, self._latest = self._latest, None
        return value


class SetupWindow:
    def __init__(self, root, controller, *, tk_module=None):
        if tk_module is None:
            import tkinter as tk_module
        self.tk = tk_module
        self.root, self.controller = root, controller
        self.worker = Worker()
        self.callback = None
        self.closing = False
        self.cleanup_started = False
        self.destroyed = False
        self.stage = "home" if controller.config else "region"
        self.method = "password"
        self.storage_choice = True
        self.controls = []
        self.collecting = False
        self.root.title("Bambu Monitor · 登录与设置")
        self.root.geometry("780x700")
        self.root.minsize(620, 600)
        self.root.configure(bg="#101b18")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = lambda *_: self.notice.set(friendly("gui_operation_failed"))
        self.shell = self.tk.Frame(root, bg="#101b18", padx=30, pady=22)
        self.shell.pack(fill="both", expand=True)
        self.notice = self.tk.StringVar(value="")
        self.status = self.tk.StringVar(value="电脑采集：未开始\n目标遥测：等待数据\n设备显示：尚未接入")
        self.render()
        self.root.after(80, self.poll)

    def label(self, text=None, *, variable=None, size=12, color="#cedbd5"):
        widget = self.tk.Label(self.shell, text=text, textvariable=variable, bg="#101b18",
                               fg=color, font=("TkDefaultFont", size), justify="left",
                               anchor="w", wraplength=690)
        widget.pack(fill="x", pady=6)
        return widget

    def button(self, text, command):
        widget = self.tk.Button(self.shell, text=text, command=command, padx=14, pady=7,
                                highlightbackground="#101b18")
        widget.pack(fill="x", pady=4)
        self.controls.append(widget)
        return widget

    def entry(self, label, value="", secret=False):
        self.label(label)
        widget = self.tk.Entry(self.shell, show="•" if secret else "", font=("TkDefaultFont", 13))
        widget.insert(0, value)
        widget.pack(fill="x", ipady=6, pady=2)
        self.controls.append(widget)
        return widget

    def render(self):
        for child in self.shell.winfo_children():
            child.destroy()
        self.controls = []
        self.label("BAMBU MONITOR", size=12, color="#7fe3b0")
        titles = {"region": "1 / 4  选择账号区域", "login": "2 / 4  登录 Bambu 账号",
                  "devices": "3 / 4  选择打印机", "confirm": "4 / 4  确认并保存",
                  "home": "监控首页", "settings": "设置"}
        self.label(titles[self.stage], size=24, color="#ffffff")
        self.label(variable=self.notice, color="#efcd8a")
        getattr(self, "render_" + self.stage)()
        if self.worker.busy:
            self.enable(False)

    def go(self, stage):
        self.notice.set("")
        self.stage = stage
        self.render()

    def enable(self, enabled):
        for control in self.controls:
            control.configure(state="normal" if enabled else "disabled")

    def submit(self, action, callback):
        if self.worker.submit(action):
            self.callback = callback
            self.enable(False)
            self.notice.set("正在处理，请稍候…")

    def poll(self):
        latest = self.worker.snapshot()
        if latest is not None and self.collecting and not self.closing and not self.worker.stop.is_set():
            snapshot = latest.get("snapshot") or {}
            state = snapshot.get("connection", {}).get("state", "unknown")
            states = {"online": "已收到目标遥测", "offline": "目标遥测暂无有效证据",
                      "connecting": "等待目标遥测", "auth_required": "登录已失效"}
            metrics = snapshot.get("metrics", {})
            self.status.set("电脑采集：运行中\n目标遥测：" + states.get(state, "等待数据")
                            + "\n设备显示：尚未接入\n\n喷嘴："
                            + metric_text(metrics.get("nozzle_celsius", {}), " °C")
                            + "    热床：" + metric_text(metrics.get("bed_celsius", {}), " °C")
                            + "\n进度：" + metric_text(metrics.get("progress_percent", {}), "%"))
        result = self.worker.result()
        if result is not None:
            callback, self.callback = self.callback, None
            self.enable(not self.closing)
            if self.closing:
                if self.cleanup_started:
                    if result[0] and result[1] is not False:
                        self.destroy(None)
                    else:
                        self.closing = self.cleanup_started = False
                        self.notice.set("资源清理未完成，请再次关闭窗口重试。")
                else:
                    self.finish_close()
            elif result[0]:
                self.notice.set("")
                callback(result[1])
            else:
                self.collecting = False
                self.status.set("电脑采集：操作失败，已停止\n目标遥测：没有当前采集结果\n设备显示：尚未接入")
                self.notice.set(friendly("gui_operation_failed"))
                self.render()
        if not self.destroyed:
            self.root.after(80, self.poll)

    def render_region(self):
        self.label("请选择 Bambu 账号所属区域，与电脑所在位置无关。切换区域需要重新登录。")
        self.button("中国大陆账号", lambda: self.begin("cn"))
        self.button("全球账号", lambda: self.begin("global"))
        if self.controller.config:
            self.button("取消，返回首页", self.cancel)

    def begin(self, region):
        self.method = "password"
        self.storage_choice = True
        self.submit(lambda: self.controller.begin(region),
                    lambda ok: self.go("login") if ok else self.notice.set(friendly(self.controller.reason)))

    def render_login(self):
        cn = self.controller.config.region == "cn"
        self.label("只在此本机窗口输入；密码和验证码不写入普通设置。"
                   + ("国区支持手机号或邮箱；手机号填写 11 位号码。" if cn else "全球区使用邮箱登录。"))
        self.account = self.entry("手机号或邮箱" if cn else "邮箱", secret=False)
        self.credential = self.entry("密码" if self.method == "password" else "验证码", secret=True)
        self.button("登录", self.login)
        if self.method == "code":
            self.button("发送一次验证码", self.send_code)
        self.button("改用验证码登录" if self.method == "password" else "改用密码登录", self.toggle_method)
        self.button("返回区域选择", lambda: self.go("region"))
        if self.controller.config:
            self.button("取消，返回首页", self.cancel)

    def toggle_method(self):
        self.method = "code" if self.method == "password" else "password"
        account = self.account.get()
        self.go("login")
        self.account.insert(0, account)

    def private_inputs(self, include_credential=True):
        account = self.account.get().strip()
        credential = self.credential.get() if include_credential else ""
        self.credential.delete(0, "end")
        values = (account, credential) if include_credential else (account,)
        if any(not value or len(value) > 16384 or any(c in value for c in "\x00\r\n") for value in values):
            self.notice.set("请填写有效的账号和密码／验证码。")
            return None
        return Secret(account), Secret(credential)

    def login(self):
        inputs = self.private_inputs()
        if inputs is None:
            return
        self.submit(lambda: self.controller.login(*inputs, method=self.method), self.logged_in)

    def logged_in(self, success):
        if not success:
            reason = self.controller.reason
            if reason == "code_required":
                self.method = "code"
                account = self.account.get()
                self.render()
                self.account.insert(0, account)
            self.notice.set(friendly(reason))
            return
        self.refresh_devices()

    def send_code(self):
        inputs = self.private_inputs(False)
        if inputs is not None:
            self.submit(lambda: self.controller.send_code(inputs[0]),
                        lambda ok: self.notice.set("验证码发送请求已确认，手机号请查看短信，邮箱账号请查看邮件。" if ok else friendly(self.controller.reason)))

    def refresh_devices(self):
        def refreshed(_):
            if self.controller.reason in ("select_printer", "no_bound_devices", "authenticated"):
                self.go("devices")
            else:
                self.notice.set(friendly(self.controller.reason))
        self.submit(self.controller.discover, refreshed)

    def render_devices(self):
        self.label("明确选择一台打印机；列表只表示账号绑定，不表示打印机在线。")
        self.device_choice = self.tk.StringVar(value="")
        listing = self.tk.Frame(self.shell, bg="#101b18")
        listing.pack(fill="x", pady=6)
        scrollbar = self.tk.Scrollbar(listing)
        scrollbar.pack(side="right", fill="y")
        self.device_list = self.tk.Listbox(listing, height=7, exportselection=False,
            bg="#182c24", fg="#cedbd5", selectbackground="#286347",
            font=("TkDefaultFont", 13), yscrollcommand=scrollbar.set)
        self.device_list.pack(side="left", fill="x", expand=True)
        scrollbar.configure(command=self.device_list.yview)
        devices = tuple(self.controller.devices)
        for index, device in enumerate(devices):
            self.device_list.insert("end", f"{index + 1}. {device.model or '机型未知'} · 设备尾号 {device.id[-4:]}")
        def chosen(_):
            selection = self.device_list.curselection()
            self.device_choice.set(devices[selection[0]].id if selection else "")
        self.device_list.bind("<<ListboxSelect>>", chosen)
        self.controls.append(self.device_list)
        if not self.controller.devices:
            self.label(friendly(self.controller.reason))
        self.button("继续", self.select)
        self.button("刷新绑定列表", self.refresh_devices)
        self.button("重新登录", lambda: self.go("region"))
        self.button("取消", self.cancel)

    def select(self):
        selected = self.device_choice.get()
        if not selected:
            self.notice.set("请选择一台打印机。")
            return
        self.submit(lambda: self.controller.select(selected),
                    lambda device: self.go("confirm") if device else self.notice.set(friendly(self.controller.reason)))

    def render_confirm(self):
        device = self.controller.selected
        self.label(f"打印机：{device.model or '机型未知'} · 尾号 {device.id[-4:]}")
        self.label("普通设置保存位置：\n" + str(self.controller.settings.path))
        self.label("只保存区域、设备与策略。密码、验证码和令牌不会进入此文件。")
        self.persistent = self.tk.BooleanVar(value=self.storage_choice)
        widget = self.tk.Checkbutton(self.shell, text="将登录令牌保存在系统密钥库（不可用时仅本次内存）",
            variable=self.persistent, bg="#101b18", fg="#cedbd5", selectcolor="#244a37",
            activebackground="#101b18", anchor="w")
        widget.pack(fill="x", pady=8)
        self.controls.append(widget)
        self.button("保存并进入首页", self.confirm)
        self.button("返回设备选择", lambda: self.go("devices"))
        self.button("取消", self.cancel)

    def confirm(self):
        persistent = self.persistent.get()
        self.storage_choice = persistent
        self.submit(lambda: self.controller.confirm(persistent=persistent), self.confirmed)

    def confirmed(self, success):
        if not success:
            self.notice.set(friendly(self.controller.reason))
            return
        self.go("home")
        if not self.controller.persistent:
            self.notice.set("已保存普通设置；当前登录仅留本次内存，关闭后需要重新登录。")

    def cancel(self):
        self.submit(self.controller.cancel,
                    lambda ok: self.go("home" if self.controller.config else "region") if ok else self.notice.set(friendly(self.controller.reason)))

    def render_home(self):
        config = self.controller.config
        self.label(f"区域：{'中国大陆' if config.region == 'cn' else '全球'} · 打印机尾号 {config.printer_id[-4:]}")
        self.label(variable=self.status, size=14)
        self.label("开始后读取云端遥测；不会操作打印任务。电脑采集与设备屏幕接入分别显示。")
        self.button("开始监控", self.start)
        self.button("切换打印机", self.change_device)
        self.button("重新登录 / 切换账号或区域", lambda: self.go("region"))
        self.button("设置", lambda: self.go("settings"))
        self.button("退出本机登录", self.logout)

    def ensure_restored(self):
        # Controller.restore revalidates the persisted selected ID; never selects another.
        return self.controller.restore()

    def start(self):
        def restored(ok):
            if not ok:
                self.notice.set("请重新登录或重新选择打印机。" + friendly(self.controller.reason))
                return
            self.collecting = True
            self.status.set("电脑采集：正在启动\n目标遥测：等待数据\n设备显示：尚未接入")
            self.submit(lambda: self.controller.run(self.worker.stop, self.worker.emit), self.stopped)
            # Only Stop remains actionable while the owner pumps the collector.
            stop = self.tk.Button(self.shell, text="停止监控", command=self.stop, padx=14, pady=7)
            stop.pack(fill="x", pady=4)
            self.stop_button = stop
        self.submit(self.ensure_restored, restored)

    def stop(self):
        self.worker.stop.set()
        self.status.set("电脑采集：正在停止\n目标遥测：先前读数不作当前状态\n设备显示：尚未接入")
        self.notice.set("正在停止采集并关闭连接…")
        if hasattr(self, "stop_button"):
            self.stop_button.configure(state="disabled")

    def stopped(self, result):
        self.collecting = False
        self.status.set("电脑采集：已停止\n目标遥测：未采集中，先前读数不作当前状态\n设备显示：尚未接入")
        self.render()
        if result == 3:
            self.notice.set("需要重新登录或重新选择设备。" + friendly(self.controller.reason))
        elif result:
            self.notice.set(friendly(self.controller.reason))

    def change_device(self):
        def ready(ok):
            if ok or self.controller.reason in ("selected_device_unavailable", "selection_required"):
                self.storage_choice = self.controller.persistent
                self.go("devices")
            else:
                self.notice.set(friendly(self.controller.reason))
        self.submit(self.ensure_restored, ready)

    def logout(self):
        from tkinter import messagebox
        if messagebox.askyesno("退出本机登录", "移除此电脑保存的登录令牌？不会撤销其他设备的云端登录。", parent=self.root):
            self.submit(self.controller.logout,
                        lambda ok: self.logged_out() if ok else self.notice.set(friendly(self.controller.reason)))

    def logged_out(self):
        self.status.set("电脑采集：未开始\n目标遥测：等待重新登录\n设备显示：尚未接入")
        self.go("home")
        self.notice.set("已移除本机登录。")

    def render_settings(self):
        config = self.controller.config
        self.label("普通设置位置：\n" + str(self.controller.settings.path))
        self.label("登录保存：" + ("系统密钥库" if self.controller.persistent else "未恢复或仅本次内存"))
        self.label("高级设置（一般无需修改）", size=16)
        self.liveness = self.entry("目标遥测证据等待阈值（秒，不是每个字段的有效期）", str(config.liveness_seconds))
        self.initial = self.entry("重连初始等待（秒）", str(config.initial_delay_seconds))
        self.maximum = self.entry("重连最大等待（秒）", str(config.max_delay_seconds))
        self.button("保存设置", self.save_settings)
        self.button("取消，返回首页", lambda: self.go("home"))

    def save_settings(self):
        try:
            config = replace(self.controller.config, liveness_seconds=float(self.liveness.get()),
                             initial_delay_seconds=float(self.initial.get()), max_delay_seconds=float(self.maximum.get()))
        except (ValueError, OverflowError):
            self.notice.set("请输入 0 到 3600 之间的秒数，最大等待不能小于初始等待。")
            return
        self.submit(lambda: self.controller.update_settings(config),
                    lambda ok: self.go("home") if ok else self.notice.set(friendly(self.controller.reason)))

    def close(self):
        self.closing = True
        self.worker.stop.set()
        self.enable(False)
        self.notice.set("正在关闭连接，请稍候；系统网络或密钥库阻塞时可能需要更久。")
        if not self.worker.busy:
            self.finish_close()

    def finish_close(self):
        # Keep cleanup off Tk's thread too, and wait for its real completion.
        if self.cleanup_started:
            return
        self.cleanup_started = True
        self.worker.submit(self.controller.close)
        self.callback = None

    def destroy(self, _):
        self.destroyed = True
        self.root.destroy()


def launch(settings_path=None):
    """Open the local window; failure is fixed text without account/OS details."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except (ImportError, RuntimeError):
        return 2
    except Exception:
        return 2
    from .settings import Settings
    from .setup_controller import SetupController
    try:
        controller = SetupController(Settings(settings_path))
    except Exception:
        from tkinter import messagebox
        messagebox.showerror("无法读取设置", "设置无效、路径不安全或已被其他程序修改。请检查非秘密设置文件。", parent=root)
        root.destroy()
        return 2
    SetupWindow(root, controller, tk_module=tk)
    root.mainloop()
    return 0
