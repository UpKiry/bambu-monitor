# M0 宿主、13 项投影与质量预算审计

审计日期：2026-11-18（Asia/Shanghai）。对应 FR-01、FR-10、AC-01 与 M0 专项门禁。执行者 `host-audit` 已先 get 再 claim 共享 `task-1`。仓库证据基点 `b4b237ab821d6e934adacbd13923773985b1dadb`；同轮其他代理的实现不属于本报告的宿主证据。

**结论：只读审计和本机离线契约探针已完成；13 项实时上屏通路、生产宿主实例生命周期与目标设备能力仍为 pending。** 本机 PetUI 是 `0.1.232 / protocol 2 / macos-arm64`。当前 composition 总变量最多 8，service bridge 标量绑定最多 8；PC fetch 是每来源最多 8，不是总变量最多 8。两个来源 8+5 的声明可通过，不能承接 Bambu 私有认证/MQTT 或证明设备更新。现有 v2 静态界面虽能声明 13 变量，却已用满 48 个节点，紧凑 runtime 为 8046/8191 字节。

本次外部目录只读；未改安装应用、技能、系统 profile、固件或正式组件库，未发布、未启动 Pet Manager GUI、未发现/连接/操作设备、未登录云。仅调用桌面二进制的 `--petui tool-info` CLI 查询版本。仓库持久交付只新增本文；离线生成脚本、日志与合成探针均在 [.build/m0](/Users/upkiry/Code/bambu-monitor/.build/m0)。

## 1. 本机定位与证据等级

| 编号 | 实际证据与定位 | 结果 / 可证明范围 |
| --- | --- | --- |
| E01 | [authoring/petui:3](/Users/upkiry/.claw-pet/authoring/petui#L3)、[desktop-path:1](/Users/upkiry/.claw-pet/authoring/desktop-path#L1) | 启动器读取明确路径，再 exec 桌面二进制的 `--petui` 模式；未从镜像复制启动器 |
| E02 | [Info.plist:19](/Applications/Hachimodock.app/Contents/Info.plist#L19)、[实际命令输出](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L35) | bundle 与 tool-info 均为 0.1.232；tool-info 另报 protocol=2、target=macos-arm64。protocol=2 是 CLI 信息，不能推成设备线协议版本 |
| E03 | [本机 contract:1](/Users/upkiry/.agents/skills/petui/references/contract.md#L1)（完整读取416行）、[contract:50](/Users/upkiry/.agents/skills/petui/references/contract.md#L50) | 契约与离线默认能力；明确默认值不能证明目标设备支持 |
| E04 | [composition:101](/Users/upkiry/.agents/skills/petui/references/composition.md#L101)、[Python校验器:85](/Users/upkiry/.agents/skills/petui/scripts/app_composition.py#L85)、[variable_owners:7](/Users/upkiry/.agents/skills/petui/scripts/variable_owners.py#L7) | 已取得实际 Python 创作校验源码；不是生产 Rust 宿主或固件接收源码 |
| E05 | [service bridge:57](/Users/upkiry/.agents/skills/petui/references/service-bridge.md#L57)、[Python桥校验器:76](/Users/upkiry/.agents/skills/petui/scripts/service_bridge.py#L76) | 候选服务桥的标量、类型与配对规则；参考本身声明完整构建/HIL 尚有门禁 |
| E06 | [D host:5](/Users/upkiry/.agents/skills/petui/references/d-host-and-surfaces.md#L5)、[Python fetch校验器:13](/Users/upkiry/.agents/skills/petui/scripts/pc_fetch.py#L13) | 公开 GET 声明和离线标量映射实现；生产网络、线程与 USB 仅有参考说明 |
| E07 | [quota:15](/Users/upkiry/.agents/skills/petui/references/quota.md#L15)、[实际 runtime:1](/Users/upkiry/Code/bambu-monitor/package/runtime/widget.json#L1)、[预算输出](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L239) | 当前包静态声明与实际紧凑字节、节点计数；不能推断宿主/设备有等额变量数组 |
| E08 | [最终探针脚本](/Users/upkiry/Code/bambu-monitor/.build/m0/probe_host_contracts.py)、[完整结果](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json)、[执行日志](/Users/upkiry/Code/bambu-monitor/.build/m0/probe.log) | 25/25 有预期断言通过，含正例、超限拒绝、null/0和能力为空的区别；没有真实 host session 或 transport |

运行环境为 macOS 27.2.1 arm64、Python 3.15.2。完整解释器字符串、命令退出码、桌面二进制及关键契约/校验器 SHA-256 见 [环境与哈希记录](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L1)。这些是本机观察，不声明 Python 3.11、其他系统或其他 PetUI 版本已测试。

只读定位依次从明确目录开始：技能目录、authoring 启动器、desktop-path 指向的应用 bundle。用 glob 在应用 Contents 下查 `**/*.rs`、`**/pet_p4_miniapp*`、Resources 下查 `**/component_app*`，均无匹配；Resources 中 `**/pc_fetch*` 仅找到安装的 Python 技能副本。随后仅在明确开发根 `/Users/upkiry/Code` 下按 `**/component_app.rs` 与 `**/pet_p4_miniapp.c` 文件名定位，亦无匹配。应用 Contents 的文件清单检索结果超过100项并被截断，因此没有依据声称“整台电脑不存在源码”；本报告只确认上述边界内未取得所需生产文件。没有泛搜 home 或 Library，没有读取凭据、账户响应、系统 profile 或原始串口；未遇到权限否决，未尝试绕过。

目标 Pet Manager Rust 工作树、固件 C 工作树、对应 commit/构建标识和真实 USB 握手均未取得。应用内的候选参考和镜像技能不是这些生产源码的替代品。

## 2. 各通路判定与实例边界

| 通路 | 实际边界 | 本次判定 |
| --- | --- | --- |
| 离线 runtime + surface | 当前 `p4-quota-v2`：16 vars、48节点、8191紧凑字节、字符串63 UTF-8字节、整数±10⁹。当前包没有 `host:true` 或 PC application 声明 | 静态基线可校验；没有实时提供器。不能靠改 init 更新遥测 |
| composition v1/v2/v3 | [校验器:94](/Users/upkiry/.agents/skills/petui/scripts/app_composition.py#L94)先调用所有权解析器；[总数检查:102](/Users/upkiry/.agents/skills/petui/scripts/app_composition.py#L102)再次要求1–8。所有权解析器也先要求总变量≤8。bindings精确覆盖host集合 | 1/8接受、13拒绝；改包quota不解除该限制。若选此入口，需要扩展，不能直接复用 |
| service bridge | [桥校验器:89](/Users/upkiry/.agents/skills/petui/scripts/service_bridge.py#L89)要求bindings≤8且键集合等于runtime.vars；提供器必须是已受控的原生声明 | 1/8接受、13拒绝；没有现成 Bambu 提供器注册证据。纯标量legacy模式也保留原配额 |
| PC fetch变量 | [fetch:56](/Users/upkiry/.agents/skills/petui/scripts/pc_fetch.py#L56)限制每来源1–8映射；最多4来源；[fetch:79](/Users/upkiry/.agents/skills/petui/scripts/pc_fetch.py#L79)要求合并绑定覆盖全部vars | 单来源9拒绝，8+5的13声明接受。只通过这一层的声明函数，不是完整host包、生产实例或设备执行 |
| PC fetch网络 | HTTPS443、公开主机、无秘密query、头白名单不含Authorization、GET、轮询≥60秒、响应≤64KiB；DNS/私网和用户主机授权另有生产门禁 | 不能用拆来源接私有认证、本机HTTP或私网服务；不能满足采集器事件驱动更新。未做网络请求，也未把Python声明通过当DNS/TLS通过 |
| app/snapshot | [D host:13](/Users/upkiry/.agents/skills/petui/references/d-host-and-surfaces.md#L13)描述 `component_app::request_validated`→fetch宿主→`publish_fetch`→实例检查→`app/snapshot`；[composition:107](/Users/upkiry/.agents/skills/petui/references/composition.md#L107)要求完整快照匹配组件、实例、序号，10秒无有效快照失效 | 只有参考路径；生产发包、排队、固件parse/应用、乱序拒绝均pending。未实现自造IPC或原始USB绕行 |
| widget/data | [实时列表:20](/Users/upkiry/.agents/skills/petui/references/live-data.md#L20)定义固定分页rows；[列表预算:63](/Users/upkiry/.agents/skills/petui/references/live-data.md#L63)定义最多20行及各字段字节 | 适用于列表，不是13个任意surface变量的证据；改source名字也不注册Bambu提供器 |

`app/snapshot` 的2048字节总包约束见 [host-collection:37](/Users/upkiry/.agents/skills/petui/references/host-collection.md#L37)；bridge参考也写 [快照2048字节](/Users/upkiry/.agents/skills/petui/references/service-bridge.md#L67)。未取得精确信封和接收器，不能据此判断全部13变量/身份/动作上下文能直接下发。

**生命周期仍需生产证据。** [composition:107](/Users/upkiry/.agents/skills/petui/references/composition.md#L107)的10秒是宿主快照年龄，不能替代打印机遥测liveness；[service bridge:80](/Users/upkiry/.agents/skills/petui/references/service-bridge.md#L80)记录nonce/generation/context和退出撤销；[fetch:13](/Users/upkiry/.agents/skills/petui/references/d-host-and-surfaces.md#L13)记录隐藏仍抓取缓存、重开绑定新epoch。没有生产实现就不能验证迟到结果、换组件、同名实例、断连、退出和超大快照的拒绝行为。探针刻意没有另写一个假会话检查器来替代它们。

## 3. 13 项逐项投影与质量要求

当前变量声明见 [runtime:5](/Users/upkiry/Code/bambu-monitor/package/runtime/widget.json#L5)，业务映射依据 [字段表:48](/Users/upkiry/Code/bambu-monitor/docs/DATA-ADAPTER.md#L48)，严格快照的value/quality/time约束见 [Schema:73](/Users/upkiry/Code/bambu-monitor/schemas/printer-snapshot.schema.json#L73)。以下为必须保留的13项，不是本轮已经实现的投影。

| # / 变量 | 类型、源指标与显示边界 | 独立质量/身份要求 |
| --- | --- | --- |
| 1 `remain` | string；remaining_minutes→HH:MM；不是秒，不按PC时钟自行倒计时；63字节和布局均须约束 | 当前job epoch；有效0表示00:00，不能和unknown合并 |
| 2 `prog` | int；progress_percent，0–100；同时绑定数字与meter | 当前job epoch；null不能投成有效0。不可用时meter也须隐藏或明确失效 |
| 3 `layer` | int；current_layer，非负且投影≤10⁹ | 当前job epoch；未知/旧任务层数不能留作当前valid |
| 4 `layers` | int；total_layers，非负且投影≤10⁹ | 当前job epoch；稳定总层数不因60秒未变化变stale |
| 5 `nozzle` | int；选定喷嘴nozzle_celsius；模型保留小数，显示取整 | 选定喷嘴身份及当前generation；未知机型不能假定字段可用 |
| 6 `bed` | int；bed_celsius；最终显示取整 | 当前设备/generation与profile遗漏语义；真实0保留 |
| 7 `chamber` | int；chamber_celsius；先确认设备支持 | 能力未知用unknown，确认不支持才unsupported；不能用0冒充不支持 |
| 8 `fan_part` | int；part_fan_percent，0–100；原始档位/单位按profile确认 | 不把原始0–15直接当百分比；局部非法不清空其他字段 |
| 9 `fan_aux` | int；aux_fan_percent，0–100 | 先确认辅助风扇能力和字段；不能借腔体风扇替代，未支持保留独立质量 |
| 10 `tray1` | int；选定AMS/槽ID对应remaining_percent，0–100 | 稳定AMS ID、槽ID、耗材epoch；空槽/移除/换料撤销旧读数 |
| 11 `tray2` | int；第二个明确槽ID的余量，0–100 | 独立槽身份和质量；数组重排不得换槽，增量缺席不等于移除 |
| 12 `tray3` | int；第三个明确槽ID的余量，0–100 | 不能复用其他槽的活跃证据恢复未知耗材；稳定旧观测时间保持原值 |
| 13 `tray4` | int；第四个明确槽ID的余量，0–100 | 缺失/-1/不支持均不能转有效0；换AMS须使旧四槽整体失去当前身份依据 |

13项每项都要区分 `valid/stale/unknown/unsupported`。已有旧值在依据失效时可stale保留；unknown/unsupported的内部value与updated_at为null。当前string/int变量无法直接接受null；应由已确认的投影协议携带质量和可见失效表示，不能把fallback=0当业务事实。温度/风扇delta-retained、任务和槽作用域规则以 [FR-08](/Users/upkiry/Code/bambu-monitor/docs/REQUIREMENTS.md#L46)为准；60秒是目标活跃证据设计初值，不是所有字段TTL。宿主重发快照不能刷新实际观测时间。

新增非读数预算至少还需考虑：连接六态、设备/机型身份、任务状态、等待/无基线、选择的喷嘴与AMS/槽身份、局部绑定失败、宿主快照过期。账号/区域/generation/job/tray epoch和逐字段观测时间应留在PC模型/诊断与可靠的实例信封；可见机型/状态另需投影。不能用一个全局online位替代13项各自质量，不能因为host仍新鲜就把打印机旧数据判valid。

## 4. 项数、节点、字节与显示预算

以下由探针对当前runtime深拷贝，使用 `json.dumps(..., ensure_ascii=False, separators=(',', ':')).encode()` 计算。只修改审计内存副本，不改包或生成基线。完整计算见 [预算脚本](/Users/upkiry/Code/bambu-monitor/.build/m0/probe_host_contracts.py)及 [结果](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L239)。

| 方案 / 测量 | vars | 紧凑runtime字节 | 节点 | 含义 |
| --- | ---: | ---: | ---: | --- |
| 当前静态基线 | 13 | 8046 | 48 | v2剩3变量、145字节、0节点；本机Python离线默认能力校验通过 |
| 每项单列质量+connection+identity | 28 | 8575 | 48 | 13值+13质量+2状态；即使未加质量UI也超v2项数和字节 |
| 质量压成quality_bits+connection+identity | 16 | 8165 | 48 | 只是项数算术，下表尚无新增host声明与状态节点；不是可运行方案 |
| 上项再加job_status | 17 | 8206 | 48 | 再次超过v2项数与字节 |
| 16项压缩方案再加host/context/input声明 | 16 | 8246 | 48 | 已超过8191，仍未加质量节点、PC声明和兼容动作改造；现有dashboard/transition还未经host适配 |

13个四态质量的信息量是26位，最大 `4^13-1=67108863`，在整数范围内；**数值可编码不等于设备可解码显示**。当前 [surface标量绑定:225](/Users/upkiry/.agents/skills/petui/scripts/app_surface.py#L225)只是名字绑定，未找到现成逐字段位提取契约。`host_mask`是宿主状态门禁，不能擅自当任意质量位掩码。将质量压成整数后仍需证实解码/显隐原语或提出通用扩展；不得把这一算式当作已经通过的投影。

其他有成本的替代都尚未选定：将所有读数预格式化为字符串会改变量类型，`prog`仍须独立int供meter；sentinel会改变显示语义；复用标题节点要改绑定与UI。当前定制字模仅含 `0123456789:%`，可见“—/未知/不支持/过期”的布局、字体与复合状态须做实际预览。现有 [progress meter:396](/Users/upkiry/Code/bambu-monitor/package/runtime/widget.json#L396)没有失效显隐，新质量节点会挤占已经用满的48节点。任何布局重排或状态视觉方案均需后续预览确认，本轮不静默改UI。

仅将13变量填成12个最坏整数（各-10⁹）和一个63字节字符串，裸values对象为322字节；这不含schema、组件ID、设备board、instance/epoch、序号、context、actions、错误、转义扩张或帧topic包装。**322不能作为2048总快照通过证据**。实际信封及最长身份/状态字符串须由生产序列化器生成并在接收边界验收，尤其要保留原子全量快照而非跨来源半帧拼接。

v3候选的32 vars/64节点/12287字节见 [quota:3](/Users/upkiry/.agents/skills/petui/references/quota.md#L3)，但未取得实际 `widgetQuota/widgetQuotas`，且composition/bridge仍独立限8；本轮不声明v3、不伪造capabilities、不以提高quota解决宿主瓶颈。若保留v2，需要在已确认通路上设计有界表示、重用节点/精简序列化；若选择产品扩展，必须同时检查能力协商、导入、宿主、传输、固件与预览。

## 5. 执行命令、结果与覆盖边界

所有Python运行设 `PYTHONDONTWRITEBYTECODE=1`，防止在外部技能或本仓库其他目录写pycache。构建使用本仓库统一入口并显式指定M0输出；只复制七个正式文件，未将用户重复副本纳入样本。命令在当前工作区执行，不安装依赖、不联网。

```sh
pwd
git status --short
/Users/upkiry/.claw-pet/authoring/petui tool-info
PYTHONDONTWRITEBYTECODE=1 .venv-dev/bin/python tools/project.py build --build-dir .build/m0/baseline
PYTHONDONTWRITEBYTECODE=1 .venv-dev/bin/python .build/m0/probe_host_contracts.py
```

| 命令 / 探针 | 结果 | 证据 |
| --- | --- | --- |
| pwd、初始git状态 | 工作区为 `/Users/upkiry/Code/bambu-monitor`；保留8个用户重复文件及同轮其他代理的src改动 | [命令记录](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L15) |
| tool-info、Python --version、git rev-parse、git status | 均exit 0；版本/基点见上文 | [完整argv及结果](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L5) |
| project.py build | exit 0，7个明确清单文件 | [build.log](/Users/upkiry/Code/bambu-monitor/.build/m0/build.log) |
| 最终探针 | exit 0，25/25预期断言成立 | [probe.log](/Users/upkiry/Code/bambu-monitor/.build/m0/probe.log) |
| composition v1/v2/v3 × 1/8/13 | 9断言：每版1、8接受，13拒绝 | [tests:58](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L58) |
| bridge 1/8/13 | 3断言：1、8接受，13拒绝；使用合成constant子服务 | [tests:113](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L113) |
| fetch单源8/9及8+5 | 3断言：接受/拒绝/接受 | [tests:131](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L131) |
| fetch Authorization、本机HTTP、私网IP、59秒、POST | 5断言均拒绝；仅调用声明函数，无网络 | [tests:149](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L149) |
| fetch map_vars有效0、null、缺失 | 3断言：0保留，后两者返回缺失/绑定错误而不造0 | [tests:179](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L179) |
| 正式清单静态基线、显式空能力表 | 2断言：Python默认离线创作通过，空能力被拒 | [tests:227](/Users/upkiry/Code/bambu-monitor/.build/m0/probe-results.json#L227) |

来源均标记为SYNTHETIC离线声明/标量测试。第二次运行仅补充host/context/input的8246字节测量，最终日志覆盖为最终脚本结果。没有运行Rust原生包校验/宿主构建、C接收器、云、USB或HIL；本次也没有执行打包/发布。本仓库全量单测由并行实现的集成代理统一记录，不能将本表25断言误称M1回放或生产宿主F层测试。

## 6. 尚需取得的具体外部输入与修改范围

这里列出后续所需范围，不请求密码/token，也不将外部缺口当作M1不能继续的理由。

| 所需输入 | 最小具体内容 | 用于闭合哪项pending |
| --- | --- | --- |
| 与0.1.232对应的Pet Manager生产工作树与commit | 包含 `pc/src-tauri/src/component_app.rs`、`pc_fetch.rs`、`pc_fetch_lifecycle.rs`（若版本中存在）、composition/bridge/服务注册模块、快照类型、USB传输管理器；相应测试与构建配置 | 找到真实受控提供器入口、变量数组、完整信封、2048准入、原子发布、组件/board/实例/序号检查和退出撤销；确认可否受控sidecar或原生消费PC快照 |
| 对应目标固件工作树与构建标识 | `pet_p4_miniapp.c`及相关app-host/surface parser、`app/snapshot`入站派发、变量结构/缓冲、能力报告代码、生命周期与测试 | 确认13+项接收、项数/字符串字节/JSON帧限制、旧实例/乱序拒绝、10秒失效、固件是否需升级；仅文件名按参考给出，须以实际树为准 |
| 真实目标设备能力报告（后续用户允许连接时） | board/机型与固件版本；`widgetRuntime(s)`、`widgetQuota(s)`与limits、app-surface/graphics、variable-bindings、app-host、host-context/input、typography/glyph等实际声明；bridge/fetch/v3仅在所选路线需要时核验 | H层协商与部署范围。目前没有报告，不能使用离线默认能力替代 |
| 生产离线宿主测试入口 | 对同一生产serializer/session/admission调用的fake transport：一个合成标量、全13+质量、旧epoch/乱序、停用、退出、隐藏→重开、断连、超大消息 | AC-01 F层、FR-11生命周期；不得以本报告的声明函数通过替代 |
| M5可见状态方案及预览确认 | 保留13读数，明确连接/机型/任务、四质量及meter不可用的表现；节点/字节和包配额一起预算 | 状态可见性与UI预算。无需现在提供真实账户凭据 |

若取得源码且后续明确授权产品改动，预期影响边界如下；以下是审计建议而非已实施补丁或未经查证的文件路径承诺。

- **本仓库**：提供器规范化出口与投影边界、宿主适配器/有界输出测试、实际状态UI生成逻辑、字段/预算验收。组件ID保持 `bambu-monitor`；不以每帧重打包更新变量。
- **Pet Manager源码**：新增受控Bambu提供器或经过确认的私有sidecar消费入口、注册/配置/生命周期、完整快照序列化与传输调度。若复用composition/bridge，还须查明所有8项限制并一致修改；不能只改Python创作校验器。
- **固件源码**：只有生产追踪证明接收/变量/失效/显隐原语不足时才列出对应补丁；若现有通道确能满足则不预设固件必改。当前无法承诺“无需升级固件”。
- **技能/校验器/协商**：若需要扩大既有受控能力，变更实际产品仓库的同源Python/Rust/C/JS契约、能力协商和回归向量；不得就地改安装技能或默认capabilities冒充目标支持。v3候选不能跳过这一步。

## 7. 分层状态与M0决策

| 层次 / 验收 | 状态 | 原因 |
| --- | --- | --- |
| D：本机工具、契约/校验源码、配额区别、13项及质量预算、外部范围记录 | passed | 本文及25断言、实际字节/节点证据可复核；生产代码空缺已限定记录 |
| F：生产宿主单标量、13+、实例切换/失效/拒绝 | pending | 未取得生产Rust会话/serializer/transport；离线Python映射只覆盖局部数据函数 |
| H：真实握手、USB、三页及状态显示、重连、时延/资源 | pending | 本轮未操作设备，尚无目标连接/握手/实机证据 |
| R：Bambu云认证/HTTP/MQTT | pending | 非本次M0范围；未使用任何真实凭据 |
| AC-01完整宿主可行性 / M4接入路径 | pending | 生产通道与目标能力未闭合，不能宣称13变量实时上屏成功 |

**决策：现有composition/bridge不能按当前契约直接承接13项；fetch声明的8+5不能作为鉴权实时方案。保留13项与质量语义，生产接入方式暂不定案，按上表取得最小源码/能力后闭合。** M0审计交付可完成并关闭 `task-1`；这不等于完整M0通路或MVP完成。M1来源无关的离线解析、增量、身份/质量与回放可继续，不需要等外部账户或设备。
