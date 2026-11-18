# macOS 后台采集服务与浏览器设置

2026-11-18：在现有 CloudAuth、SetupController、Session 和采集核心上新增 macOS 托盘宿主与本地网页设置。支持后台运行、显示采集/目标遥测状态、浏览器完整登录与设备选择。真实 macOS 托盘、独立后台进程及 Chromium 合成流程已测试；真实云账号、系统钥匙串实际读写、launchd 登录重启、USB 与设备显示仍待验证。

## 启动

Python 3.11+，macOS 图形登录会话：

```sh
.venv-dev/bin/python -m pip install -e '.[dev,cloud,tray]'
.venv-dev/bin/python -m bambu_monitor service --background
```

本机也可双击 [start-monitor.command](../start-monitor.command)。启动器查找项目内已安装 cloud、tray 依赖的虚拟环境，启动独立进程；收到原生托盘首次显示成功的握手后返回，终端可以关闭。首次握手等待上限 20 秒；`service_startup_pending` 表示结果仍待确定，不能据此反复启动副本。`service_started` 表示托盘与本机 HTTP 服务已就绪，不表示 Bambu 账号或打印机在线。

默认打开 `http://127.0.0.1:8765` 对应的设置页面。请通过托盘“打开本机设置…”进入：打开链接含仅本次进程有效的本机控制会话，页面读取后从地址栏清除，并只保留在页面内存。直接输入裸地址、刷新网页或重新启动服务后，需从托盘重新打开。浏览器不保存云密码、验证码、令牌或本机控制会话到 localStorage/cookie。

前台调试及其他启动选项：

```sh
# 前台托盘进程，终端要保持打开；Ctrl+C/SIGTERM 请求清理
.venv-dev/bin/python -m bambu_monitor service

# 自定义本机端口，不自动恢复已配置的采集
.venv-dev/bin/python -m bambu_monitor service --background --port 8766 --no-auto-collect

# 不自动打开浏览器，稍后从托盘打开
.venv-dev/bin/python -m bambu_monitor service --background --no-browser

# 使用指定非秘密设置
.venv-dev/bin/python -m bambu_monitor service --config /absolute/private/provider.toml
```

端口被占用时启动失败；不自动跳转到未知服务，也不自动挑选其他端口。只允许 IPv4 loopback，不接受局域网地址。`localhost` 的使用目的由 `127.0.0.1` 实现；精确 Host 校验要求使用服务提供的 URL。

## 浏览器流程

1. 选择账号区域 `cn/global`，不根据电脑位置推断。
2. 登录：国区手机号/邮箱、全球邮箱；支持现有密码/短信/邮件验证码分支。必须点击“发送一次验证码”才会发码，不自动重发；网站 TOTP 等未支持 MFA 会明确报错。密码登录需要验证码时保留账号并切换方式。
3. 登录后读取绑定列表，按设备 ID 明确选择一台；即使只有一台也不会自动选中。绑定列表不表示在线。
4. 确认设备并保存设置，选择系统钥匙串或本次内存。新登录在确认前仅使用内存，取消设置保留旧配置。
5. 点击“开始监控”；日常服务启动时，如果已有普通设置，会默认尝试恢复令牌、核实绑定设备并启动采集。凭据失效则停止并提示重新登录，不自动发码/密码登录，也不会切到别台设备。

普通配置沿用旧向导的默认位置与私有原子保存策略：macOS 为 `~/Library/Application Support/bambu-monitor/provider.toml`。只存区域、设备 ID、随机凭据引用、目标遥测阈值和重连策略。云令牌使用系统钥匙串；钥匙串不可用时明确显示本次内存，重启服务需重新登录。不会保存密码、验证码、邮箱、手机号或原始账户响应。

“停止监控”先请求停止，立即撤下当前遥测；连接清理完成后才能更改账号/设备、保存高级设置或退出登录。“退出本机登录”删除对应本机令牌，不全局撤销云账号。普通退出保留已保存令牌。关闭网页不会停止采集；退出托盘服务会请求清理 HTTP、MQTT 和内存凭据。

托盘显示未配置、需登录、处理设置、启动、采集、停止和退出等状态；采集中用颜色区分等待/无目标证据与有效目标遥测。MQTT 连接中不表示打印机在线；“暂无目标遥测证据”也不等于设备断电。读数区分有效 0、等待、不支持、旧值。后台服务与设备屏幕是否接入是两件独立状态。

## 可选登录时启动

以下命令才会修改当前用户系统登录项；普通启动、双击和安装 Python 依赖不会注册登录项：

```sh
.venv-dev/bin/python -m bambu_monitor service --install-launch-agent
.venv-dev/bin/python -m bambu_monitor service --uninstall-launch-agent
```

安装创建 `~/Library/LaunchAgents/com.upkiry.bambu-monitor.plist`，注册当前 `gui/<uid>` 域。它保留项目虚拟环境解释器路径，使用 `service --no-browser --port ...`，登录后恢复已有配置的采集，不每次打开浏览器。可同时传 `--config`、`--port`。这是当前用户 GUI LaunchAgent，需要用户登录；不是系统 root daemon，也不保证睡眠期间继续接收网络消息。退出托盘不会被强制重启（KeepAlive=false）。

安装拒绝覆盖已有文件及符号链接；重复安装报已存在。注册失败或超时会保留文件并以 `*_installed` 错误明确说明文件已写入、运行状态需检查；不要把报错当作未安装反复重试。卸载先验证自有标记和完整参数，bootout 成功或任务已不存在后，只删除确认过的确切文件，拒绝外来 plist。此轮未执行用户真实 LaunchAgent 安装或重登录验证。

## 实现与安全边界

- [service_runtime.py](../src/bambu_monitor/service_runtime.py)：一个永久 owner 串行执行登录、凭据库、设置和采集；只接受一个在途操作，拒绝排队堆积。HTTP/托盘只看脱离控制器的状态拷贝。快照只保留最新值；停止/关闭后拒绝旧发布。
- [local_service.py](../src/bambu_monitor/local_service.py)：标准库 HTTP，最多 8 个请求线程、3 秒请求读超时、64 KiB 请求体，严格路由/字段，拒绝重复 JSON 键、非有限数、重复长度头、分块请求和跨源请求。不提供打印控制、任意 MQTT publish 或 USB 路由。
- [macos_service.py](../src/bambu_monitor/macos_service.py)：pystray + AppKit，托盘/计时器/图标/菜单更新均在主线程；signal 只置事件，主线程计时器响应。登录项安装/卸载为单独的显式动作。
- [service_launcher.py](../src/bambu_monitor/service_launcher.py)：独立会话进程、关闭终端流、pipe 原生就绪握手。密码和云令牌不进 argv、环境、日志或握手。
- [网页](../src/bambu_monitor/webui/index.html)：同源静态 JS/CSS，没有第三方脚本或字体、内联脚本许可、浏览器凭据持久化。精确 Host、Origin、随机控制会话、无 CORS、no-store、CSP 与 HTML 转义共同保护入口。

账号输入经本机明文 HTTP body 到采集进程，再由既有适配器通过 TLS 连接 Bambu Cloud；不监听 LAN。不要对这个端口做网络转发。同一系统用户下的恶意本机进程/浏览器扩展不属于隔离边界；控制会话 URL 也不宜分享。该本机控制会话不是 Bambu token。

系统 DNS、HTTP 库内部调用或原生钥匙串可能阻塞；5 秒等待结束不能证明资源已回收。清理未完成返回固定错误，owner 保持真实存活直至操作结束，不强杀线程、不启动第二个 owner 假装恢复。当前尚无签名 .app 安装包、自动更新或跨平台托盘支持声明。

旧 Tk `python -m bambu_monitor setup` 和 CLI `collect/login/logout/replay` 保留，详见[旧向导](SETUP-WIZARD.md)与[云采集](CLOUD-COLLECTOR.md)。不要同时用多个入口采集同一设备。

## 参考与验证

参考 [Keralots/BambuHelper](https://github.com/Keralots/BambuHelper) 的“网页登录 → 绑定设备 → 保存配置 → 监控状态”流程与 Web portal 组织方式。它主要是 ESP32 固件，macOS 托盘与后台宿主由本项目独立实现；没有复制其实现、LAN 接口、密码保存或任务控制能力。阅读依据为其公开 README 的 Cloud、Setup 与 Web Interface 章节，不宣称审查过其完整代码。托盘参考 [pystray 0.19.5 文档](https://pystray.readthedocs.io/en/latest/usage.html)，macOS 必须主线程运行。

自动与实际验证记录见[服务验证](LOCAL-SERVICE-VERIFICATION.md)。浏览器测试使用明确的合成 HTTP/MQTT，不能替代真实云账号、系统钥匙串、固件字段或设备验收。
