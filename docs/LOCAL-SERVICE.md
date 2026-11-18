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

### 机器读数与采集详情

本机网页展示全部九项规范化任务/温度/风扇指标，各自带质量和实际观测时间。AMS lite 按单元 ID、槽位 ID 0–3 展示四槽的耗材类型和 RGBA 颜色；不再将余量作为后台关注项。类型/颜色来自 `tray_type/tray_color`，不要求 RFID UUID；未知、旧值与有效值分开显示，不根据缺失字段推断空槽或未安装。色块表示设备上报的设定颜色，不证明物理耗材在位或实际外观。

“采集详情”可展开三部分：

- 全部已观测字段：保存本次连接按路径合并的脱敏观测，包含尚未规范化的设备字段；原始数组作为整体保存，避免按数组位置混合不同 AMS 槽。每行保留自己的时间，不因刷新网页而变新，也不承诺仍然有效。
- 最新脱敏原始遥测 JSON：选定设备最新一帧合法 JSON report，可查看机器实际提供的状态和版本等信息；不是 HTTP 账户响应，也不是完整历史日志。
- 安全诊断：连接阶段、断连/超时原因、收包计数、协议及规范化字段依据，便于区分连接失败和字段质量失效。

只读采集已订阅的目标 report，不增加账户/任务控制接口。敏感字段名、当前云令牌/UID/设备 ID、URL、身份 token 和常见凭据格式会被过滤；未经识别的遥测仅供本机排查，不直接成为有效指标。缓存只在内存中，停止/退出/切账号清空，重连撤销旧代展示；最多 512 个路径、字段缓存 256 KiB、单帧 64 KiB，结构/字符串另有预算，达到限制会明确提示截断。没有落盘报文功能。

页面每秒自动读取本机状态，也可点“刷新本机读数”立即重读。该按钮不会发送 MQTT 命令，不能强制云端推送；现有只读 `pushall` 仍仅用于受限连接初始化，不通过反复重连模拟手动刷新。

更新 Python 代码后需退出托盘服务，再重新启动，从托盘重新打开本机设置；只刷新网页无法让旧 Python 进程加载新采集代码。若此前使用本次内存登录，重启后需要重新登录。设备组件仍为既有静态版本，尚未改为类型/颜色布局或接通上屏。

托盘显示未配置、需登录、处理设置、启动、采集、停止和退出等状态；采集中用颜色区分等待/无目标证据与有效目标遥测。MQTT 连接中不表示打印机在线；“暂无目标遥测证据”也不等于设备断电。读数区分有效 0、等待、不支持、旧值。后台服务与设备屏幕是否接入是两件独立状态。

## 可选登录时启动

以下命令才会修改当前用户系统登录项；普通启动、双击和安装 Python 依赖不会注册登录项：

```sh
.venv-dev/bin/python -m bambu_monitor service --install-launch-agent
.venv-dev/bin/python -m bambu_monitor service --uninstall-launch-agent
```

安装创建 `~/Library/LaunchAgents/com.upkiry.bambu-monitor.plist`，注册当前 `gui/<uid>` 域。它保留项目虚拟环境解释器路径，使用 `service --no-browser --port ...`，登录后恢复已有配置的采集，不每次打开浏览器。可同时传 `--config`、`--port`。这是当前用户 GUI LaunchAgent，需要用户登录；不是系统 root daemon，也不保证睡眠期间继续接收网络消息。退出托盘不会被强制重启（KeepAlive=false）。

安装拒绝覆盖已有文件及符号链接；重复安装报已存在。注册失败或超时会保留文件并以 `*_installed` 错误明确说明文件已写入、运行状态需检查；不要把报错当作未安装反复重试。卸载先验证自有标记和完整参数，bootout 成功或任务已不存在后，只删除确认过的确切文件，拒绝外来 plist。此轮未执行用户真实 LaunchAgent 安装或重登录验证。

## 命令行采集与旧窗口

需要 NDJSON 导出时，使用 cloud 依赖即可，无需托盘或 Tk。参考[非秘密配置样例](../examples/provider.example.toml)保存自己的 `provider.toml`，明确区域、设备ID和凭据引用，然后执行：

```sh
python -m bambu_monitor check-config provider.toml
python -m bambu_monitor collect provider.toml --duration 60
python -m bambu_monitor collect provider.toml --login-method email-code
# 国区手机号短信验证码使用 --login-method sms-code
python -m bambu_monitor logout provider.toml
```

CLI 通过 POSIX 控制TTY隐藏输入账号/密码/验证码，明确询问发码与设备选择；未配置设备时即使只有一台也要选择，选择只用于本次运行，不自动改文件。stdout每行含 `input_class=live-cloud`、规范化`snapshot`与`diagnostics`；提示走TTY，诊断走stderr。该标签只表示真实网络入口，是否在线看快照连接/质量。未指定duration则持续运行，Ctrl+C/SIGTERM请求清理。退出码0为正常结束（不保证有遥测）、2为本机/配置错误、3为认证/发现无法继续，交互取消为130。

`login provider.toml`可先保存令牌再`collect`。系统存储不可用时，单独login退出不会保留令牌，应在collect内完成登录；`--session-only`可主动只用本次内存。默认仅使用明确原生系统后端，不自动加载第三方文件存储。不要将账号秘密放进命令参数或环境变量。

旧 Tk 窗口保留：使用带 tkinter 的 Python 安装 `.[cloud]`，运行 `python -m bambu_monitor setup`，可加 `--config`。流程与浏览器一致，但关闭Tk窗口会停止其采集。安全配置保存只支持POSIX；Linux配置默认`$XDG_CONFIG_HOME/bambu-monitor/provider.toml`，无有效绝对路径时用`~/.config/bambu-monitor/provider.toml`。Windows私有输入/安全设置入口尚未完成，不据系统凭据后端存在宣称全平台支持。

以上入口共用模型与设置格式，不要同时启动多个入口采集同一设备。离线 `replay` 不联网，格式见[数据契约](DATA-ADAPTER.md)。

## 实现与安全边界

- [service_runtime.py](../src/bambu_monitor/service_runtime.py)：一个永久 owner 串行执行登录、凭据库、设置和采集；只接受一个在途操作，拒绝排队堆积。HTTP/托盘只看脱离控制器的状态拷贝。快照只保留最新值；停止/关闭后拒绝旧发布。
- [local_service.py](../src/bambu_monitor/local_service.py)：标准库 HTTP，最多 8 个请求线程、3 秒请求读超时、64 KiB 请求体，严格路由/字段，拒绝重复 JSON 键、非有限数、重复长度头、分块请求和跨源请求。不提供打印控制、任意 MQTT publish 或 USB 路由。
- [macos_service.py](../src/bambu_monitor/macos_service.py)：pystray + AppKit，托盘/计时器/图标/菜单更新均在主线程；signal 只置事件，主线程计时器响应。登录项安装/卸载为单独的显式动作。
- [service_launcher.py](../src/bambu_monitor/service_launcher.py)：独立会话进程、关闭终端流、pipe 原生就绪握手。密码和云令牌不进 argv、环境、日志或握手。
- [网页](../src/bambu_monitor/webui/index.html)：同源静态 JS/CSS，没有第三方脚本或字体、内联脚本许可、浏览器凭据持久化。精确 Host、Origin、随机控制会话、无 CORS、no-store、CSP 与 HTML 转义共同保护入口。

账号输入经本机明文 HTTP body 到采集进程，再由既有适配器通过 TLS 连接 Bambu Cloud；不监听 LAN。不要对这个端口做网络转发。同一系统用户下的恶意本机进程/浏览器扩展不属于隔离边界；控制会话 URL 也不宜分享。该本机控制会话不是 Bambu token。

系统 DNS、HTTP 库内部调用或原生钥匙串可能阻塞；5 秒等待结束不能证明资源已回收。清理未完成返回固定错误，owner 保持真实存活直至操作结束，不强杀线程、不启动第二个 owner 假装恢复。当前尚无签名 .app 安装包、自动更新或跨平台托盘支持声明。

旧 CLI 与 Tk 入口的关闭/存储语义见上文，运行层与宿主边界见[架构](ARCHITECTURE.md)。

## 参考与验证

参考 [Keralots/BambuHelper](https://github.com/Keralots/BambuHelper) 的“网页登录 → 绑定设备 → 保存配置 → 监控状态”流程与 Web portal 组织方式。它主要是 ESP32 固件，macOS 托盘与后台宿主由本项目独立实现；没有复制其实现、LAN 接口、密码保存或任务控制能力。阅读依据为其公开 README 的 Cloud、Setup 与 Web Interface 章节，不宣称审查过其完整代码。托盘参考 [pystray 0.19.5 文档](https://pystray.readthedocs.io/en/latest/usage.html)，macOS 必须主线程运行。

自动与实际验证记录集中在[验证状态](VERIFICATION.md)。浏览器测试使用明确的合成 HTTP/MQTT，不能替代真实云账号、系统钥匙串、固件字段或设备验收。
