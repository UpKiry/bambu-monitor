# 数据契约、13 项映射与质量规则

当前云采集、离线回放共用解析器和状态机；真实账号/固件、宿主投影与实机仍 pending。云协议边界见[云协议](CLOUD-PROTOCOL.md)，组件/宿主职责见[架构](ARCHITECTURE.md)。
[内部快照 Schema](../schemas/printer-snapshot.schema.json)是 PC 提供器契约，支持云/LAN/HA 来源命名，不表示这些提供器均已实现；它不等于固件或自定义 surface 变量传输协议。
[合成快照](../examples/snapshot.cloud.example.json)和[回放样例](../examples/replay.synthetic.ndjson)仅供离线验证，不得冒充实时数据。包内 `vars` 的 type/init 只定义初值，不能产生持续采集。

## 统一快照 v1

| 字段 | 当前含义 |
| --- | --- |
| `schema_version` / `source` | 固定 1；来源为 `bambu-cloud/bambu-lan/home-assistant` |
| `printer.id` / `printer.model` | 明确选定设备与已识别型号；型号未知为 null，不从名称/ID/UI 猜测 |
| `connection.state` | `unconfigured/auth_required/connecting/online/offline/error` |
| `connection.last_message_at` | 最近有效目标遥测的接收观察时间；未收到为 null，不是 broker ping 时间 |
| `generated_at` | 快照生成时间，不意味着所有字段刚更新 |
| `job.state` / `job.name` | 状态白名单为 `IDLE/PREPARE/RUNNING/PAUSE/FINISH/FAILED/SLICING/INIT`；当前名称始终 null |
| `metrics` | 九项基础指标，各含 `value/quality/updated_at` |
| `ams[]` | 最多 16 个单元、每单元最多 4 槽；按明确 ID 表示，槽余量同样带质量与时间 |

快照拒绝额外字段；不包含账户、UID、token、LAN code、相机 URL、原始报文或任务/耗材身份 token。允许的字符串也可能装入秘密，因此 Schema 不能代替提供器白名单与脱敏。

| quality | value / updated_at | 语义 |
| --- | --- | --- |
| `valid` | 数值 / 带时区观察时间 | 当前字段、代次及任务/耗材作用域有有效证据；真实 0 是正常数值 |
| `stale` | 最后数值 / 原观察时间 | 曾获得值，当前有效性依据已失去；保留历史而非伪造新值 |
| `unknown` | null / null | 未可靠获得、身份尚不明确或无可用历史值 |
| `unsupported` | null / null | 明确能力映射确认不支持；未知能力不能直接归为不支持 |

合法明确上报的 0 分钟、0 层、0% 可为 valid；不能把缺失、−1、空串或解析失败替换为 0。零总层数也不能自行推导任务完成。

## 原始遥测 → 九项指标与四槽 UI

下表路径相对根 `print`，仅适用于候选旧版 `command: push_status`。13 项为九项基础值加选定 AMS 的四槽；这是映射约定，当前宿主实时投影尚待验证。

| 原始字段 | 内部字段 | UI 变量 | 校验 / 展示约定 |
| --- | --- | --- | --- |
| `mc_remaining_time` | `remaining_minutes` | `remain` | 整数 0–2³¹−1 分钟；展示 HH:MM，不自行倒计时 |
| `mc_percent` | `progress_percent` | `prog` | 整数 0–100 |
| `layer_num` | `current_layer` | `layer` | 整数 0–2³¹−1 |
| `total_layer_num` | `total_layers` | `layers` | 同上，不由任务状态补值 |
| `nozzle_temper` | `nozzle_celsius` | `nozzle` | 有限数 −50–500°C，模型保留小数，显示时才取整 |
| `bed_temper` | `bed_celsius` | `bed` | 有限数 −50–200°C |
| `chamber_temper` | `chamber_celsius` | `chamber` | 有限数 −50–200°C；须有能力依据 |
| `cooling_fan_speed` | `part_fan_percent` | `fan_part` | 原始整数档 0–15，当前固定 `round(raw*100/15)` |
| `big_fan1_speed` | `aux_fan_percent` | `fan_aux` | 同上；不能拿 `big_fan2_speed` 腔体风扇替代 |
| `ams.ams[].tray[].remain` | `ams[].trays[].remaining_percent` | `tray1`～`tray4` | 整数 0–100；明确选择 AMS 后按槽 ID 0–3 对应，不能取数组位置 |

严格十进制数字字符串可解析；布尔值、指数文本、NaN/Infinity、越界或不合量纲的值不夹紧。字段非法时有历史值则 stale，否则 unknown，不清空其他有效字段。
当前显式型号 `A1/A1 mini` 的腔温和辅助风扇为 unsupported；其余已识别名称不构成全部指标或固件兼容保证。余量是设备估计值，不承诺每种耗材存在或准确。
当前 UI 单喷嘴、四槽的约束不能用于猜双喷嘴/多 AMS 选择；不可用值不能直接塞进现有 string/int 变量并显示成有效 0，质量显示与宿主通道仍待验收。

## 时间、代次与持续有效性

[TelemetryState](../src/bambu_monitor/telemetry/state.py)由一个 owner 串行调用；[Session](../src/bambu_monitor/session.py)隔离区域/账户引用/设备身份与 generation，切身份创建对应状态实例。旧代或非精确目标 topic 的回调不能更新新状态。
`updated_at` 是带时区 RFC 3339 接收观察时间。同值明确上报也更新；缺席字段、broker ping、重新生成快照、重连或人工 baseline 标记不更新时间。
PC metadata 单独记录 `session_generation/baseline_generation/baseline_complete/baseline_fields`、`last_target_evidence_monotonic`、`field_last_observed/field_basis`、`job_epoch/tray_epoch` 和 profile，不塞进严格快照 Schema。
默认 60 秒只衡量最近有效目标证据的 liveness，不是逐字段 TTL 或云 SLA。有效基础遥测、合法任务状态和槽余量可续期；纯身份、版本响应、`client.connected`、ping、HTTP 查询、无效值及其他设备不能续期。
`client.disconnected` 立即使旧值 stale；超时原因是 `telemetry_timeout`（目标遥测无证据），不宣称打印机断电。liveness 用单调时钟，不受墙钟跳变影响。
换代、失活或明确非 online 状态撤销基线保留依据，旧值/原时间保留 stale；部分恢复只恢复明确重观测字段，不能使整个缓存或基线重新有效。
仅在本代完整基线、目标活跃、字段局部有效及任务/槽身份均成立时，省略字段才可按 `delta-retained/task-scoped/tray-scoped` 保持 valid。因此离线已确认的稳定总层数/AMS 可超过 180 秒不变而有效，时间仍为原观察时间。
墙钟后跳时保留实际观察时间，以 `generated_at=max(now, latest_observed_wall)` 保证字段时间不晚于生成时间；metadata 标记 `logical-wall-max-observed/wall_clock_rollback`，不篡改观察时间掩盖异常。

## legacy-cloud-v1 候选与身份边界

`legacy-cloud-v1` 是社区旧字段的**离线候选 profile**，`profile_verified_on_real_cloud: false`。生产没有完整基线自动检测；真实 Session 入站默认 `baseline=False`。
`apply(..., baseline=True)` 只是离线 fixture/人工审阅的 `offline-manual-assertion`。首帧、字段多、pushall 已发送或 `push_status` 都不证明完整；标记不会补齐未观察字段或刷新时间。
没有本代基线时，明确有效字段可在当前观测时 valid；后续其他 delta 不能无限续用它。未知 profile 不解旧路径、不续 liveness，未观察为 unknown、失去依据的旧值为 stale。
当前不解析新固件 `device.*`、packed 温度、双喷嘴或未知风扇单位，不将未验证来源当作 0–15 档或 0–100 百分比。
任务身份候选为 `task_id/project_id/subtask_id`；本代须明确见到 task_id 或 subtask_id，单独 project_id 不恢复任务。身份变化/无法比较、新 `PREPARE/INIT` 撤销旧任务作用域，进度/层数/剩余时间清为 unknown，温度/风扇按自身证据保留。
完成状态只保留明确上报值，不推导 100%/0 分钟。内部身份 token 有界且不出口；同代明确退役身份和倒序单调时间帧会丢弃，每作用域最多记 128 个退役身份。
AMS 单元 ID 0–15、槽 ID 0–3 规范化为数字字符串并排序；缺席数组分支不代表删除，即使人工 baseline 也不从遗漏推断删除。重复/非法 ID 或超量数组局部撤销槽/单元依据；无法定位的顶层歧义撤销 AMS 依据，不影响温度。
耗材须本代明确观察有效 `tray_uuid`；`tray_info_idx` 可检测材料变化，但不能证明同类耗材未替换。UUID/已知材料身份变化撤销旧余量，新余量未报为 unknown；旧代 UUID 不能由材料类型 delta 恢复。
生产默认 `offline_test_markers=False`，不接受测试 `empty/removed` 为真实空槽/删除证据（解析器忽略这些字段）。只有显式离线开关支持合成清槽、移除与 tombstone；metadata 明示开关。
真实 `tray_exist_bits`、完整列表删除、RFID/硬件拓扑判定尚未实现。没有身份/可信源序列的同代迟到 delta 无法可靠区分，超过 128 次切换也不保证无限历史去重；不能声称任意云乱序已解决。

## 本机后台材料与原始遥测视图（2026-11-18）

用户当前目标改为 AMS lite 四槽的类型与颜色，不依赖余量探测。既有设备静态组件及严格 v1 数值快照暂保留兼容，不能把其中旧余量变量当成已实现的新上屏方案。

本机服务额外提供 `ams_materials[]`：每单元按 ID，`trays[]` 按槽 ID 0–3 排序；`material` 和 `color` 各含 `value/quality/updated_at`。分别解析 `tray_type`（1–64 可打印字符）与 `tray_color`（8 位 RGBA hex，规范大写）。不要求 `tray_uuid` 才能展示明确观测，但描述不证明物理料盘身份或在位。持续目标证据可保留同代描述；缺席不删除，时间不变。身份或已知类型/颜色变化撤销旧配对，未重新上报的另一个字段为 unknown；非法拓扑、目标失活、断连及换代撤销有效性。超时后普通心跳不能复活旧描述。旧余量的严格身份规则不用于材料描述。

`telemetry.latest` 是最新选定目标 report 的有界、脱敏 JSON；可包括旧版解析器未识别的字段或版本响应。`telemetry.fields` 是按 JSON Pointer 路径缓存的观测值及时间/generation，数组整体替换，不能按下标混合增量槽位。它只代表接收记录，不属于严格快照，不建立 liveness、完整基线或实时有效性。规范化九项原有质量规则保持保守，不因展示更多未知字段就恢复 valid。

该视图仅本机服务显式开启，CLI/回放不输出原始遥测。递归过滤凭据键、身份/文件/URL 信息、当前已知秘密和常见凭据形式；派生材料字符串也走相同过滤。仅内存最新报文及最多512路径/256KiB字段缓存，报文≤64KiB、最多2048节点/16层、单字符串≤1024字符，超限明确截断；不会落盘或包含 HTTP 账户响应。停止立即撤下本机视图，owner清理缓存；generation撤销清除旧缓存，旧回调不能重建。

## 设备展示模型（bambu-display-v1）

[display.py](../src/bambu_monitor/display.py) 把严格快照 `metrics` 与本机 `ams_materials` 转成设备组件要显示的内容。它只做展示决定，不重新判定有效性；它是电脑内部约定，不是 USB 或宿主变量协议，能否上屏仍受[架构](ARCHITECTURE.md)所述的投影通道与运行时原语限制。

- 覆盖九项指标加 AMS 四槽的类型/颜色，共 17 个字段；每项保留 `quality`、`tone` 和原始 `raw`。
- `valid` 正常显示，有效 `0` 与 `00:00` 照常显示；`stale` 保留读数并标注“旧值”；`unknown` 显示 `—`；`unsupported` 显示“不支持”。未知进度只画轨道，不显示 0%。
- 剩余分钟格式化为 `HH:MM`，不做倒计时；超过 99 小时显示 `99:59` 并标注“超过显示上限”。层数超过 6 位同样截断标注。温度按远离零的四舍五入取整，模型仍保留原精度。
- AMS 按单元 ID 与槽 ID 0–3 映射到槽 1–4，不按数组位置。只有一个单元时展示它；多个单元且没有明确选择时提示尚未选择，不擅自取第一个；缺席槽为未知。
- 类型与颜色各自独立质量。材料名去掉控制字符、按 20 个半宽格截断并标注。颜色要求 8 位 RGBA，色块先按卡面底色合成透明度；黑色、接近白色和明显半透明都加描边。解码失败按未知处理，不猜 6 位色值。
- 连接标签只反映连接状态；字段质量相互独立，连接恢复不会让所有字段自动变有效。
- 页面提示由字段质量生成：全部未知说明尚未收到，存在旧值提示重新确认，全部不支持说明机型不提供。

离线渲染使用 `python tools/project.py display-preview`，把六个场景乘以三页写到 `.build/display-preview/`。场景由生产状态机配合合成 `push_status`（含任务身份）生成，不连接账号或打印机；产物是设计预览，不是设备截图。

## 有界回放与验证入口

[解析器](../src/bambu_monitor/telemetry/parser.py)直接输入最多 256 KiB、JSON 深度 32，拒绝重复 key/非 JSON 数值，诊断最多 32 类；真实 MQTT/Session 入站另收紧为 64 KiB。规范化快照不保存原始 payload、不出口未知字段；本机显式开启的脱敏检查视图见上一节。
[回放实现](../src/bambu_monitor/replay.py)用 FakeMqtt → Session → 同一生产解析/状态路径，不另写 fixture 专用算法；显式启用离线 topology markers。
NDJSON 首行必须精确含 `kind: fixture`、`input_class: synthetic|redacted`、`profile`、`region`、`printer_id`。其后最多 10000 事件、每行最多 64 KiB，`at` 带 UTC 偏移，`monotonic` 有限、0–10¹² 且不递减。
事件为 `start/connack/suback/message/tick/disconnect/auth_rejected/reconnect/stop`；message 的 `payload` 为对象，可另有 `baseline/topic/generation`。重连必须先经历可恢复断开和退避；baseline 仍只属人工离线证据。
输出为 `{input_class, snapshot, diagnostics}`，停止后无新快照，不输出原输入或秘密。运行示例：

```sh
PYTHONPATH=src python -m bambu_monitor replay examples/replay.synthetic.ndjson
```

| 源码 / 测试入口 | 核心断言 |
| --- | --- |
| [快照契约测试](../tests/test_snapshot_contract.py) | Schema、质量/数值与时间结构 |
| [遥测测试](../tests/test_telemetry.py) | 首次 unknown、能力、真实 0、稀疏 delta、同值观察、超时局部恢复、墙钟、任务/耗材 epoch、AMS ID、候选限制与合成空槽 |
| [会话/回放测试](../tests/test_provider.py) | 共用链路、generation、旧回调、队列溢出、身份隔离、fixture 标记与无秘密出口 |

依赖与统一 `python tools/project.py check` 见[开发说明](DEVELOPMENT.md)。离线断言包括字段时间不晚于 generated_at；这些证据不代替真实云遗漏/基线/身份语义，也不证明系统凭据库、宿主投影、USB 或实机通过，状态见[验证记录](VERIFICATION.md)。
