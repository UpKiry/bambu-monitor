# 架构与实现边界

本项目采用自研 Bambu Cloud HTTP + 云 MQTT 只读适配器。当前交付为电脑采集器、macOS 后台托盘/浏览器配置、旧 CLI/Tk 入口和 PetUI 静态组件。实际完成范围见[验证状态](VERIFICATION.md)。LAN MQTT、HA、打印控制、相机和文件传输不在当前实现范围。

## 数据流与模块

```text
浏览器 / macOS 托盘 → ServiceRuntime 单 owner → SetupController
                                                  ├→ CloudAuth → HTTP / 凭据库
                                                  └→ Collector → Session → 云 MQTT
                                                                         ↓
                                                          parser → TelemetryState
                                                                         ↓
                                                规范化快照 → 本机状态页 / CLI NDJSON
                                                                         ⋮ 待实现
                                                    Pet Manager 宿主投影 → USB → 设备
```

| 模块 | 职责 |
| --- | --- |
| [config.py](../src/bambu_monitor/config.py)、[settings.py](../src/bambu_monitor/settings.py) | 非秘密配置、区域和设备身份、私有权限、路径校验、原子保存及冲突检测 |
| [auth.py](../src/bambu_monitor/auth.py)、[credentials.py](../src/bambu_monitor/credentials.py) | 认证状态机、令牌恢复、UID/设备发现、显式选择、系统凭据库或会话内存 |
| [cloud_http.py](../src/bambu_monitor/cloud_http.py)、[cloud_mqtt.py](../src/bambu_monitor/cloud_mqtt.py) | 固定云端路由、TLS、有限输入、白名单只读初始化；协议见[云协议](CLOUD-PROTOCOL.md) |
| [session.py](../src/bambu_monitor/session.py)、[collector.py](../src/bambu_monitor/collector.py) | 有界回调队列、连接/订阅、generation、退避重连、握手/目标遥测期限与资源清理 |
| [telemetry/](../src/bambu_monitor/telemetry/state.py)、[replay.py](../src/bambu_monitor/replay.py) | 解析、字段/AMS槽增量合并、任务/耗材身份、质量、快照与离线回放 |
| [setup_controller.py](../src/bambu_monitor/setup_controller.py) | 新登录草稿、设备确认、凭据迁移与普通设置保存的事务边界 |
| [service_runtime.py](../src/bambu_monitor/service_runtime.py) | 永久单 owner 串行执行设置与采集，拒绝并行操作，只保留最新快照和公开状态副本 |
| [local_service.py](../src/bambu_monitor/local_service.py)、[webui/](../src/bambu_monitor/webui/index.html) | loopback HTTP 与浏览器配置；HTTP线程只提交操作/读取副本 |
| [macos_service.py](../src/bambu_monitor/macos_service.py)、[service_launcher.py](../src/bambu_monitor/service_launcher.py) | 原生托盘主线程、NSTimer、signal、独立进程就绪握手和显式用户 LaunchAgent |
| [cli.py](../src/bambu_monitor/cli.py)、[setup_gui.py](../src/bambu_monitor/setup_gui.py) | CLI与旧Tk前端，复用同一认证/设置/采集逻辑；命令见[使用指南](LOCAL-SERVICE.md) |

## 身份、质量与生命周期

区域和设备必须明确选择，不按电脑位置推断、不默认第一台设备、不自动回退 LAN。账号引用、区域、设备 ID 组成隔离身份；切换/重连撤销旧 generation，旧回调不能恢复新设备或新任务的数据。

单进程只运行一个当前设备订阅。MQTT 回调入有界队列，owner 串行合并并驱动手动 pump；Paho 不启动自动网络/重连线程。停止/关闭先置 Event，立即撤下本机当前遥测，再等待 owner 清理，未清理完成不开放新操作。HTTP/钥匙串阻塞时不声称硬5秒退出成功。

新登录令牌在确认前仅留内存。设置保存、凭据迁移、取消与删除失败都有明确结果；保存结果不明时停止后续操作，不删除可能已被配置引用的令牌。系统后端不可用时明确回退本次内存，不用明文文件。

MQTT broker 连接、目标设备有活跃证据、完整基线就绪是不同事实。`online` 必须有有效目标遥测；60秒默认阈值是目标活跃证据期限，不是所有字段 TTL。稳定字段的持续有效性取决于 profile、基线和任务/耗材身份；观测时间不能由页面刷新或心跳伪造。当前候选 profile 与完整规则见[数据契约](DATA-ADAPTER.md)。

当前本机服务另有内存遥测检查视图：九项规范化指标、AMS四槽材料/颜色、按路径观测缓存、最新脱敏目标 report 与安全 diagnostics。它不属于严格快照或 USB 协议，不改变有效性证据，也不保存账户响应。用户已将 AMS lite 需求从余量改为类型/颜色；既有静态组件保留，新的设备布局/投影仍待预览与接入。

## 设备投影尚未接通

[快照 Schema](../schemas/printer-snapshot.schema.json)是电脑内部契约，不是 USB 或 Pet Manager 的 IPC 协议。本机 HTTP 只用于配置和状态；不能因为存在 localhost API 就认定宿主可消费这些字段。分钟的 `HH:MM`、温度取整、AMS 按 ID 映射和逐字段不可用表示已由[展示模型](../src/bambu_monitor/display.py)实现，并用 `python tools/project.py display-preview` 渲染三页与降级状态；这属于离线前端，不改变组件包，也不等于投影已接通。

2026-11-18 本机 PetUI **0.1.232** 文档与 Python 校验器只读审计得到以下边界；它们不代表生产 Rust、目标固件或 USB 握手已验证：

| 通道 | 已核实限制 / 影响 |
| --- | --- |
| composition / service bridge | 总变量 / 标量绑定最多8，不能直接传当前13项 |
| PC fetch | 每来源最多8，拆8+5可通过声明；网络仅公开HTTPS GET、无Authorization、拒绝localhost/私网、轮询≥60秒，不能用于本机私有实时采集 |
| `widget/data` | 有界 rows 列表，不能据此推断任意 surface 变量可更新 |
| `app/snapshot` | 参考中有变量通道；真实serializer、实例/序号、大小限制、设备接收与失效仍缺生产证据 |
| 当前静态组件 | 13变量、48/48节点、8046/8191紧凑runtime字节；v2一般16变量不能证明传输也支持16 |

逐字段质量还需26位信息；即使压缩成整数，仍需可用的解码/显隐原语，不能只凭预算算术宣称支持。现有字模/进度条也没有完整未知/旧值显示方案。后续先审阅预览和预算，不静默删减13项或提高配额。

接入前需要与当前客户端对应的 **Pet Manager生产源码/commit、固件源码/构建标识和目标能力报告**。先追踪受控提供器、实例生命周期、序列化/传输/接收预算，再用合成单标量与13项快照测退出、迟到、乱序、断连，最后验证真实显示。USB复用宿主传输；外部修改先列具体范围并取得授权，不改安装应用/技能或直接写串口。

### 上屏仍缺的三个原语（2026-11-18 核对）

展示模型已覆盖 17 个字段，但当前 PetUI 表面表达不了确认稿，不能靠删字段或合并质量绕过：

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| 动态颜色 | 节点只有静态 `color`；`image_bind` 仅服务桥 | AMS 色块只能画固定颜色，无法随槽位颜色变化 |
| 质量文本 | `text`/`var_bind` 原样渲染变量，无“值 + 状态后缀”或按质量换色；dashboard 只有 `fmt_mmss`/`fmt_hms` | 无法同时显示读数与“旧值/超过上限”，也无法区分 `unknown` 与 `unsupported` |
| 投影配额 | 宿主组合绑定最多 8 个标量；`widget/data` 是固定行列表 | 17 个业务值加质量无法完整下发，拆成多个来源也不改变单实例绑定上限 |

因此本轮只交付展示模型、三页离线渲染与交互路径验证；组件保持 `bambu-monitor` 0.1.6 的 13 变量静态布局。要真正上屏，需要生产宿主与固件提供更宽的受控快照通道或上述原语之一，并重新协商能力；在此之前打包新布局会重演“静态占位包冒充实时组件”的问题。

## 安全与工程约束

- 本机服务只绑定 `127.0.0.1`，随机控制会话、精确 Host/Origin、无CORS、CSP/no-store；最多8请求线程、3秒读超时、64KiB body，不提供通用文件、打印控制或publish入口。
- 云秘密只经本机交互进入内存/系统密钥库，不进argv、环境示例、普通配置、日志、快照或组件包。公开状态不输出原始账户响应/Access Code；异常只用固定安全类别。
- 当前用户下的恶意本机进程/扩展不在隔离边界内；不将本地端口转发到网络。平台、协议、长时恢复和硬退出时限按实际证据声明。
- [组件生成器](../tools/build_package.py)维护 ID `bambu-monitor`、版本 `0.1.6` 和三页13变量；[构建入口](../tools/project.py)只打包7个明确资源。Python、配置、测试和凭据不入设备包。
- 代码检查、真实云、原生托盘、launchd、USB/实机验收与发布分别记录；不得为验收擅自改变打印任务。复现及未完成项见[验证状态](VERIFICATION.md)。
