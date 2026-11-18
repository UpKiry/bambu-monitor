# M1 离线核心操作说明

这是M1阶段的离线操作和实现边界记录；后续真实HTTP/MQTT、私有TTY/系统凭据库和前台调度代码已实现，当前使用入口见[云采集使用](CLOUD-COLLECTOR.md)，本轮验证见[M2/M3记录](M2-M3-VERIFICATION.md)。下文“没有网络/登录/密钥库”等描述限定M1交付当时，不覆盖后续代码；离线replay仍不联网。

2026-11-18 开始实施方案 A。当前交付为 **PC 离线核心**，提供共用遥测解析、身份/质量状态、fake HTTP/MQTT、配置检查及有标签的回放；没有实际 HTTP/MQTT 网络客户端、云登录入口、系统密钥库、自动重连调度器或 Pet Manager 投影。不要给这些命令传入真实凭据或原始账户响应。

## 安装和运行

Python 3.11+；本机验证环境为 macOS arm64 / Python 3.15.2。现有构建依赖不变，新增核心仅用标准库。重新安装 editable 后才有模块和 console entry point：

```sh
python -m pip install -e '.[dev]'
python -m bambu_monitor check-config examples/provider.example.toml
python -m bambu_monitor replay examples/replay.synthetic.ndjson
# 同等入口：bambu-monitor replay examples/replay.synthetic.ndjson
python tools/project.py check
python tools/project.py build
```

本机可将 `python` 替换为 `.venv-dev/bin/python`。导入模块不发网络请求、不启动线程，不访问账号、USB或组件库。`check-config` 校验有界 TOML，只输出区域、是否已选择与会话内存状态；空设备 ID 输出 `unconfigured`，有 ID 为 `auth_required`，不表示认证成功。只接受 `bambu-cloud`、明确 `cn/global`、非秘密凭据引用与时间预算；未知配置键（包含秘密键）、非法区域和时间值被拒绝。

`replay` 每行输出一个 JSON envelope：`input_class`、符合 v1 Schema 的 `snapshot`、PC `diagnostics`。标签在 Schema 外，不扩展严格快照。stdout 不混提示，错误走 stderr 的安全 JSON，失败退出2。它与未来真实传输复用 `Session → TelemetryState → parser`，不连接屏幕。回放可能先输出若干快照再遇到输入错误，消费者须同时检查退出状态。

## 回放格式和证据边界

[合成回放](../examples/replay.synthetic.ndjson)首行必须声明 `kind=fixture`、`input_class=synthetic/redacted`、`profile`、`region`、`printer_id`。随后每行一个事件，含带偏移 `at`、不下降的 `monotonic`；事件类型为 `start/connack/suback/message/tick/disconnect/reconnect/auth_rejected/stop`。`message.payload` 是供共用解析器处理的 print JSON；`topic/generation` 可选用于注入错误目标/迟到代次。每行≤64KiB，事件≤10000；不接受无标签的原始账户文件。

`baseline:true` 是**离线操作者明确断言的证据**，不是云消息字段或首帧检测。`legacy-cloud-v1` 仅为社区旧字段的离线候选 profile，未获任何真实区域/机型/固件验证；完整基线检测、真实任务/槽身份和删除协议是 M3 门禁。回放显式开启的 `empty/removed` 测试标记在默认生产状态禁用，详见 [协议 profile](PROTOCOL-PROFILES.md)。未知 profile 不解析旧字段、不续 liveness。

## 模块与生命周期

| 模块 | 已实现行为 | 当前边界 |
| --- | --- | --- |
| [配置](../src/bambu_monitor/config.py) | 明确区域/设备、非秘密加载、按账号引用/区域/设备隔离namespace | 不持久化秘密、不猜区域 |
| [认证边界](../src/bambu_monitor/auth.py) | 可替换 HTTP、密码/邮箱code状态机、401/403/429、安全异常、设备ID显式选择、关闭/请求epoch隔离 | 仅fake验证；短信/网站CSRF/TOTP诊断unsupported；没有私有TTY登录或真实网络 |
| [遥测核心](../src/bambu_monitor/telemetry/state.py) | 字段/AMS ID合并、generation、任务/耗材epoch、逐项质量、单调时间liveness、Schema输出 | 无可靠身份不推定持续有效；job.name保守null；不提供云profile实测保证 |
| [会话](../src/bambu_monitor/session.py) | TLS参数与唯一client ID契约、CONNACK→SUBACK、受控两条初始化、300秒跨代限频、队列/代次、恢复时间与清理 | 无网络线程、无自动retry；将来runner须泵owner，真实阻塞/停止预算仍pending |
| [回放](../src/bambu_monitor/replay.py)与[CLI](../src/bambu_monitor/cli.py) | 共用路径、合成标签、有界输入、安全输出 | 没有HTTP/IPC宿主服务或任意publish/打印控制命令 |

会话由创建它的 owner 线程调用 `start/drain/snapshot/stop`。网络 callback 仅在锁下入队或撤销generation；传输 `subscribe/publish/close` 由 owner 在锁外执行。原始delta队列默认64、可配1–4096，单次drain最多处理队列预算；控制队列按有限状态迁移最多两项。溢出不能悄悄丢一个身份变化后继续valid，而是撤销本代、清队列、标记 `queue_overflow` 并计算恢复时间。单消息在Session入口≤64KiB，独立parser≤256KiB/32层，AMS≤16单元×4槽，诊断最多32类，退休身份缓存每scope≤128。

断线/停用立即拒绝旧回调；重连必须先完成旧transport清理。清理异常置 `cleanup_failed`，持续阻止新连接，不能第二次start绕过。认证拒绝进入 `auth_required` 且没有retry_at；普通恢复为初值1s、上限60s的指数退避，并在0.5–1.0倍内jitter。没有后台线程替用户自动重登、发码或重连。HTTP请求epoch在发起/返回均核对，关闭后或新登录后旧401/成功结果均不能改新会话。

`MemoryCredentials` 只保留本次进程内Secret，repr脱敏、退出清空，不存在明文文件fallback。系统密钥库集成仍待M2；当前重启必须重登。Future transport若无法在预算内close，必须报告失败，不能把fake无阻塞测试当真实线程/网络5秒退出证明。

## 本轮后续所需输入

M0生产通路和设备能力缺口见 [审计](M0-HOST-AUDIT.md)。需要与当前客户端对应的 Pet Manager/固件源码工作树和commit，以及目标设备能力报告；目前无需密码/token。取得代码后先只读追踪受控入口，再列具体修改文件并在获外部授权后实施。

M2真实登录前才需确认账号区域、邮箱/手机号和MFA分支，并实现本地私有输入及密钥库；秘密不进入聊天、argv、配置、日志或组件。机型/固件、AMS/喷嘴选择与自然任务变化在真实联调时确认。UI保持现有版本，质量状态必须先预览确认。
