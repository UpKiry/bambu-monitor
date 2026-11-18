# M0 / M1 实施与分层验收记录

日期：2026-11-18（Asia/Shanghai）。用户已明确授权本仓库实施方案 A。基点 `b4b237ab821d6e934adacbd13923773985b1dadb`，本轮代码为其上未提交工作区；没有提交或推送。输入全部为 synthetic/fake，无真实账号、打印机、USB或宿主实例。候选 profile 为 `legacy-cloud-v1`，**尚无真实云/固件验证**。

**交付结论：M0可取得的本机只读证据审计完成，M1离线核心与回放可运行并通过本机回归。** 生产宿主可行性、真实认证/云MQTT和端到端显示仍pending；这不是完整MVP通过。M0判定与具体外部输入见 [宿主审计](M0-HOST-AUDIT.md)，运行方式与实际模块见 [操作说明](OFFLINE-CORE.md)，字段和作用域限制见 [候选profile](PROTOCOL-PROFILES.md)。

## 环境、命令与产物

本机 macOS arm64，Python 3.15.2，Pillow 12.3.0、jsonschema 4.26.0、rfc3339-validator 0.1.4；PetUI 0.1.232。核心标准库实现，不新增HTTP、MQTT或密钥库运行依赖。仅在上述本机运行；Python 3.11/12/13、其他OS及NAS未在本轮实际执行。CI配置沿用现有矩阵，不能从配置推断远程结果。

```sh
.venv-dev/bin/python -m pip install -e '.[dev]'
.venv-dev/bin/python tools/project.py check
.venv-dev/bin/python tools/project.py build
.venv-dev/bin/python -m bambu_monitor check-config examples/provider.example.toml
.venv-dev/bin/python -m bambu_monitor replay examples/replay.synthetic.ndjson
.venv-dev/bin/python .build/benchmark_m1.py
```

| 执行 | 实际结果 | 证据与限制 |
| --- | --- | --- |
| editable安装 | exit0 | 当前src模块与console entry point可导入；首次尝试`--no-build-isolation`因venv未装setuptools失败，改标准隔离构建成功，未修改系统Python或签名 |
| 全量check | exit0；62/62测试通过 | [check日志](../.build/m1-check.log)：26项提供器集成、26项遥测、10项既有Schema；五份JSON与基线一致，18翻页路径、PNG及Schema语法通过 |
| 统一build | exit0；7个清单文件 | [build日志](../.build/m1-build.log)；无UI、ID、版本或正式资源变动，无用户重复副本进入构建 |
| check-config | exit0；unconfigured/cn/selected=false | 空设备保持未配置，未登录；配置只含非秘密设置 |
| CLI回放 | exit0；16个有标签envelope | [完整输出](../.build/m1-replay.ndjson)；在线/超时/重连/真实0均经共用Session及解析/状态；stop事件后不再输出快照 |
| M0探针 | exit0；25/25断言 | [审计](M0-HOST-AUDIT.md)区分8项限制、静态预算与生产/HIL未知，不是生产宿主集成测试 |
| 本机时延 | 2000样本；P95 0.039583ms | [测量脚本](../.build/benchmark_m1.py)、[结果与源码SHA-256](../.build/m1-benchmark.json)；单owner，逻辑100消息/s，enqueue→drain→snapshot→JSON。P50 0.034958ms，最大0.150750ms；无云延迟/宿主/屏幕 |
| fake停止 | 0.010500ms | 仅fake transport资源；真实线程/阻塞close的5秒目标尚pending。close抛异常会报告失败并持续阻止重连 |

基准没有创建真实网络线程或100Hz实时发送器，只顺序推进合成单调时间，不能作为云吞吐或持续系统负载的证据。liveness在最后目标消息+61s的显式owner snapshot被判offline；真实runner的timer与“到期后≤5s可见”尚未实现。未改UI，故未重新生成预览或打包；未执行发布、库导入、保存并同步、固件写入或任何打印控制。

## 需求 / 验收追踪

表中passed均限定所列U/F子范围；含真实层的完整AC不据此整体passed。测试步骤、具体输入和断言在 [提供器测试](../tests/test_provider.py)与[遥测测试](../tests/test_telemetry.py)中可复跑，全部使用实际共用实现。

| 用例 / 需求 | 本轮状态 / 证据 | 待覆盖 |
| --- | --- | --- |
| AC-01 / FR-01 | D passed：实际工具/契约、25探针、13值与状态预算、外部范围已记录 | 生产Rust/固件入口、宿主单标量/实例F、真实握手及H均pending |
| AC-02 / FR-02 | U/F passed：严格cn/global、不猜区域/不回退LAN、空ID、不秘密配置、namespace隔离、停旧身份拒迟到回调 | 持久设置UI及真实登录后的设备选择集成pending |
| AC-03 / FR-03 | U/F passed（受支持子集）：opaque token、邮箱code、错误code、401/403/429、安全异常、旧HTTP epoch/关闭隔离、存储异常内存fallback | 真实HTTP、私有TTY/表单、系统密钥库、SMS/网站CSRF/TOTP pending；MFA目前明确诊断unsupported，未伪装支持 |
| AC-04 / FR-04 | U/F passed：ID选择、重排/重名、未选/消失不切第一台、禁止dev_access_code出口、401撤销 | 真实账户设备发现、UID和本地展示入口pending；fake仅导出必要设备ID |
| AC-05 / FR-05 | U/F passed：TLS参数/8883/唯一client ID契约、SUBACK失败、就绪顺序、受控get_version/pushall、300s跨代限频、重复触发不重发 | Paho/其他网络适配、TLS真实握手、broker权限、固件初始化响应pending |
| AC-06 / FR-06 | U passed：旧字段、数字字符串、小数、0、原始0–15风扇、越界/非法/未知profile、Schema | 首个实际机型/固件与遗漏语义、双喷嘴/新结构pending |
| AC-07 / FR-07 | U/F passed：字段缺席、AMS按ID乱序/部分/明确测试移除、任务/槽epoch、旧generation/退休身份、歧义拓扑局部失效 | 真实基线自动检测、空槽/删除wire、同代无身份乱序归属pending |
| AC-08 / FR-08 | U passed：下表全部离线条件序列、单调时钟与观测时间分离 | 候选profile真实证据、固件活跃事件/阈值pending |
| AC-09 / FR-09 | U/F passed：配置/认证/连接/在线/超时/局部解析/限流/清理诊断，broker成功不等于目标在线 | 实际错误映射、宿主失联与云状态分离的M4集成pending |
| AC-10 / FR-10 | pending | M0生产路径未闭合，不实现自造投影；13项、分钟/取整/质量/身份可见状态的UI预览与H需后续执行 |
| AC-11 / FR-11 | U/F passed：generation立即撤销、关闭后无输出、切身份、退避有界/jitter、认证拒绝无重试、旧close不能关新连接、清理失败不可绕过 | 网络runner/线程/在途HTTP强取消/实际5s停止预算及宿主实例pending |
| AC-12 / FR-12 | U/F passed：真实共用路径回放、synthetic标签、机器输出/安全错误、预算、canary出口 | 已脱敏真实遥测fixture尚无；现有合成输入不得当实时数据 |
| AC-N01 / NFR-01 | U/F passed：Secret repr、正常/错误/关闭/存储失败canary，未知原文及job.name不导出，无明文落盘 | 系统密钥库/真实HTTP库及真实认证日志、argv输入入口pending |
| AC-N02 / NFR-02 | F passed（本机解析链路子目标）：上述2000样本P95远小于1s | 真实云、自动到期调度、宿主≤2s和可见≤5s均pending |
| AC-N03 / NFR-03 | U/F passed：有界消息/嵌套/队列/诊断/历史身份、过载撤销generation，退避封顶，cleanup失败可观察 | 真实网络/线程长期压力、阻塞取消和退出预算pending |
| AC-N04 / NFR-04 | D/U/F passed（当前macOS）：Python标准库核心与GUI/串口隔离，src可安装，文档无硬编码项目依赖 | 其他Python/OS、NAS/ARM部署、宿主打包签名pending；M0报告中的绝对路径只是本机证据 |
| AC-N05 / NFR-05 | D/U passed：需求/测试/profile可追踪、62回归与原构建通过、正式资源/ID/版本/清单不变 | 远程CI、真实云和HIL pending |

## AC-08 子序列

| 子用例 | U结果 | 实际断言 |
| --- | --- | --- |
| a | passed | 首次unknown/已知A1能力unsupported；ping、info、其他目标和非法JSON不续liveness |
| b/c | passed | 10s进度delta持续190s；总层数/槽余量仍valid且T0时间不变；70s同值喷嘴更新独立观察时间 |
| d | passed | ping不停但目标超时→offline/stale；原因telemetry_timeout，非断电结论 |
| e | passed | AMS基线后重连保留stale；部分delta只恢复明确字段，不恢复整代baseline |
| f | passed | 任务A→B撤销旧总层/进度；温度独立；旧身份/新PREPARE拒沿用；本代project_id单独不确认任务 |
| g | passed | 稳定UUID槽保留；换料/显式测试空槽撤销；新代材料类别不借旧UUID恢复；0余量有效 |
| h | passed | 多AMS/槽乱序与缺席不删除；明确测试删除有墓碑；重复/非法拓扑局部stale，不污染其他槽/温度 |
| i | passed | 未知profile不解旧路径、不续liveness；未观察unknown、失去依据的缓存stale |
| j | passed | 局部负余量/非法字符串只失效相应字段，真实0有效，后续无关消息不能恢复invalid |
| k | passed | 墙钟前/后跳不改liveness；保留真实观察时间，逻辑generated_at不早于观察并标明异常 |
| l | passed（可证明边界） | 旧generation/旧身份/非目标topic/单调倒序拒绝；账号区域设备切换新建隔离状态。无身份/源序列的同代迟到delta不能可靠识别，真实层仍pending |

## 审查与修复记录

独立M0代理对核心作只读复审，集成代理检查完整实现与差异后运行统一回归。测试实际发现并修复：AMS换代嵌套遍历；HTTP关闭后迟到token、旧请求重绑新epoch；旧disconnect关闭新transport及持锁I/O；close异常一次消耗清理标志后可绕过；巨大整数OverflowError逃逸CLI；基线后重复槽ID仍保持旧valid；新代材料类型借旧UUID、project_id借旧任务确认。修复均有回归，不通过降低断言或放宽Schema处理。

M0最终只读复核的25项集成与2项遥测定向结果见 [复核日志](../.build/m0/final-recheck.log)；其更早的review结果用于缺陷复现，不能拿修复前结果描述当前实现。完整回归以本页最终62项与 [check日志](../.build/m1-check.log)为准。

当前最小外部输入：与0.1.232对应的Pet Manager生产工作树/commit、目标固件工作树/构建标识，以及实际设备能力报告。源码范围见 [M0输入表](M0-HOST-AUDIT.md#6-尚需取得的具体外部输入与修改范围)。真实账号区域/登录分支及机型/固件在M2/M3联调时再确认；密码、token、验证码只可进入未来本机私有入口，不在聊天提供。
