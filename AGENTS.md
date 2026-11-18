# 工程协作约定

本项目是 `bambu-monitor` PetUI 显示组件、构建工程和 PC 离线采集核心。**2026-11-18 用户已选择方案 A：自研 Bambu Cloud HTTP + 云 MQTT 适配器，并已授权本仓库实施。** 已有 M0 只读审计、M1核心及M2/M3真实HTTP/MQTT、私有TTY/凭据库和前台运行代码；另有已确认流程的[本地登录设置窗口](docs/SETUP-WIZARD.md)，[182项本机检查与Tk交互证据](docs/SETUP-WIZARD-VERIFICATION.md)。真实账号/云协议、系统密钥库实际读写、宿主投影和实机仍待验证。先读[交接](docs/HANDOFF.md)、[需求](docs/REQUIREMENTS.md)、[实施计划](docs/IMPLEMENTATION-PLAN.md)与[验收矩阵](docs/ACCEPTANCE.md)，再读[M0证据](docs/M0-HOST-AUDIT.md)、[云采集使用](docs/CLOUD-COLLECTOR.md)、[M2/M3验证](docs/M2-M3-VERIFICATION.md)与[公开参考对照](docs/CLOUD-REFERENCES.md)。M1历史见[离线操作](docs/OFFLINE-CORE.md)和[M1记录](docs/M1-VERIFICATION.md)。

- `package/` 是组件资源；布局和变量声明由 `tools/build_package.py` 生成。保持组件 ID `bambu-monitor`，未经明确要求不更改版本或 UI。
- 使用 `python tools/project.py check`、`build`、`preview`、`pack` 作为统一入口，具体依赖和参数见 `docs/DEVELOPMENT.md`。
- 默认生成内容进入 `.build/`；打包只使用明确清单中的资源。不要删除用户已有的重复文件，不要把它们打入包。
- `schemas/` 定义提供器的内部快照契约，`examples/` 仅供离线说明和检查，不作为实时数据上屏。
- 方案 A 已确定；B/pybambu 与 C/HA 只作历史比较，本地 MQTT 后续再做。仓库内代码与测试已获实施授权；外部修改/UI/真实账号按下述边界推进。不要把文档、契约、合成样例或离线测试称为云登录、云 MQTT、USB 或实机链路已通过。
- 先做 M0 宿主/13变量核实，可并行推进 M1 离线核心。composition/bridge 的8项限制与 PC fetch 的每来源8项限制须区别；不能据此假定目标设备能力或通过拆来源解决私网鉴权。
- 外部 Pet Manager/固件改动先列明源码与范围，不直接修改安装应用、技能或系统profile；UI调整先提供预览。字段有效性按会话/任务/槽身份与观测依据判断，不能把所有稳定字段一律按60秒未变判过期。
- 云账户凭据、LAN Access Code、访问令牌、验证码和原始账户响应不得进入源码、组件包、日志或提交。仅在用户决定接入方案后实现私有凭据存储。
- 云和 LAN 提供器应输出同一规范化结构；缺失、过期、不支持的数据与数值 0 必须区别处理。增量合并按字段和 AMS/槽 ID 进行。
- 先核对目标宿主与固件能力，再实现变量投影；`widget/data` 列表协议不自动等于自定义 surface 变量更新协议。
- 检查不自动发布、不自动同步、不触碰真实打印任务；发布和实机验收状态分别报告。
