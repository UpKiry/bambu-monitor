# 工程协作约定

本项目包括自研 Bambu Cloud HTTP + 云 MQTT 只读采集器，以及 PetUI 三页显示组件。macOS 主入口为后台托盘和 localhost 浏览器配置；旧 CLI/Tk 保留。采集代码、本机界面和合成测试已实现，真实云账号、系统钥匙串、launchd 登录恢复、宿主投影与实机仍待验收。

先读 [README](README.md) 与 [架构边界](docs/ARCHITECTURE.md)，按任务查看 [使用](docs/LOCAL-SERVICE.md)、[开发](docs/DEVELOPMENT.md)、[数据契约](docs/DATA-ADAPTER.md)、[云协议](docs/CLOUD-PROTOCOL.md) 和 [验证状态](docs/VERIFICATION.md)。不再维护分阶段交接、重复计划或单次修复报告。

- 云端方案已确定为自研适配器，不引入完整 HA/pybambu；LAN MQTT 留待后续。不提供打印任务控制或通用 MQTT publish。
- `package/` 是组件资源，布局和变量由 `tools/build_package.py` 生成。保持组件 ID `bambu-monitor`；未经明确要求不改版本、13 项指标或 UI，UI 调整先提供预览。
- 统一使用 `python tools/project.py check/build/preview/pack`。默认生成内容进入 `.build/`，打包只用明确清单。不要删除用户原有重复文件，也不要将其纳入包。
- 凭据、LAN Access Code、云令牌、验证码和原始账户响应不得进入源码、普通配置、组件、日志或提交。只用本机私有交互入口与系统密钥库；不可用时明确退到本次内存，不落明文。
- 采集和设置复用单 owner；停止/切账号/换设备撤销旧会话，迟到消息不得更新新实例。浏览器、托盘和网络回调不并发修改控制器。
- `schemas/` 是内部快照契约，`examples/` 是离线说明。有效 0 与缺失/过期/不支持必须区别，增量按字段及 AMS/槽 ID 合并，质量结合 generation、任务/耗材身份与观测证据；不能把所有字段统一按 60 秒未变判过期。
- PetUI 0.1.232 的 composition 总变量/bridge 绑定≤8，与 PC fetch 每来源≤8 不同；fetch 还不支持私网鉴权、localhost 和实时轮询，不能靠拆来源证明13变量可上屏。`widget/data` 列表协议不等于自定义 surface 变量通道。
- 实现投影前核对生产宿主、固件和真实能力。外部 Pet Manager/固件修改先列路径和范围，不直接修改安装应用、技能或系统 profile，不抢占原始 USB 串口。
- 离线测试、原生托盘、本机网页、真实云、launchd、USB 和实机验收分开报告；缺账号/设备时继续可执行工作，不能把合成数据称为实时交付。
- 检查不自动发布、导入或同步，不操作真实打印任务；提交与推送按用户指令执行。
