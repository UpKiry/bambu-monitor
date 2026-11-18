# 遥测协议 profile 与离线证据

实现入口是 [TelemetryState](../src/bambu_monitor/telemetry/state.py) 与[有界解析器](../src/bambu_monitor/telemetry/parser.py)，公开导出为 `from bambu_monitor.telemetry import TelemetryState`。它们仅产生内部 [v1 快照](../schemas/printer-snapshot.schema.json)与 PC metadata，不实现投影、宿主或设备控制。

## 证据等级与完整基线

`legacy-cloud-v1` 是依据[云协议研究](CLOUD-PROTOCOL.md)中社区旧版字段整理的**离线候选 profile**。当前没有真实账号、区域、机型、固件或云 MQTT 验证记录，不能称为已实机验证 profile；单元及 fake 回放证明实现的条件行为，不证明实际固件满足这些条件。

`apply(..., baseline: bool = False)` 的 `baseline=True` 是**调用方在离线 fixture/人工审阅中明确提供的完整基线证据标记**，metadata 记录为 `offline-manual-assertion`。它不是云报文字段、自动探测器，也不是“首帧”“字段较多”“pushall 已发送”或 `print.command == push_status` 的推论。解析器不声称能从候选云 wire 自动认出完整基线。生产真实传输必须保持 `baseline=False`，直到取得目标 profile 的完整性与遗漏语义证据并单独实现经验证的判定；真实层目前 pending。字段缺失本身不能证明数据完整。

人工完整基线只建立当前 generation 的省略保留条件；从未观察的字段继续 unknown。`baseline_fields` 记录本代已明确观察且未局部失效的基础指标；AMS 信息通过 `field_last_observed`、`field_basis` 与 `tray_epoch` 单独记录。基线不会刷新字段时间，也不能让旧 generation 的字段变成 valid。未经基线确认时，本代明确有效字段在当前消息观测时可以 valid；随后的其他消息不能无限续用它，必须重新明确观察。任务与槽字段另须有效身份。

## API 与调用责任

```python
state = TelemetryState(printer_id, model=None,
                       profile="legacy-cloud-v1", liveness_seconds=60,
                       offline_test_markers=False)
state.begin_generation(generation)
accepted = state.apply(payload_bytes, topic=target_report_topic,
                       generation=generation, observed_at=aware_datetime,
                       monotonic=monotonic_seconds, baseline=False)
snapshot = state.snapshot(now=aware_datetime, monotonic=monotonic_seconds)
metadata = state.metadata()
state.set_connection("offline", "network_error")
```

- 状态对象由一个所有者串行调用。账号/区域/设备切换由会话层新建相应 identity namespace 的状态对象；本类只持有明确选定的 printer ID，不接受原始账户对象。跨身份 HTTP 结果与网络回调由会话层在进入本类前检查 scope。
- generation 初值为 0；`begin_generation()` 接受严格递增整数。重连先换代，旧数值与观测时间保留为 stale，任务与槽身份需本代重新确认。旧 generation 回调和非精确 `device/<selected_id>/report` topic 被忽略。
- `apply()` 的 bool 表示接纳了有效目标证据或明确离线事件；不是基线完整标志。无效 JSON、未知命令、纯身份字段、未知字段及纯 broker ping 不刷新目标证据。`set_connection("online", ...)` 也不会制造目标证据。
- 接收时间必须带时区，单调时间必须是 0–10¹² 范围内的有限数（先范围检查，超大整数不触发浮点转换溢出）。快照是新建的独立 dict；消费者不能借 `generated_at` 或字段年龄重新定义质量。metadata 反映最近一次 apply/snapshot 的时点。
- `printer_id` 限定为 1–128 位 ASCII 字母、数字、下划线或连字符。model 只接受已列明机型名；其他值输出 null。不会从 ID、topic、任务名或 UI 推断机型。profile 切换应由调用方撤销当前 generation 并创建新状态对象。

## 候选旧版字段与单位

只接受根 `print` 对象且 `command` 精确为 `push_status`。新固件嵌套 `device.*`、packed 温度、双喷嘴与未知风扇单位均不解析；不猜喷嘴，也不将 `big_fan2_speed` 当辅助风扇。

| 原始字段 | 内部字段 | 校验与转换 |
| --- | --- | --- |
| `mc_remaining_time` | `remaining_minutes` | 0 至 2³¹−1 的整数分钟，不倒计时 |
| `mc_percent` | `progress_percent` | 整数 0–100 |
| `layer_num`, `total_layer_num` | `current_layer`, `total_layers` | 0 至 2³¹−1 的整数 |
| `nozzle_temper` | `nozzle_celsius` | 有限数 −50 至 500，保留小数 |
| `bed_temper`, `chamber_temper` | `bed_celsius`, `chamber_celsius` | 有限数 −50 至 200，保留小数 |
| `cooling_fan_speed` | `part_fan_percent` | 原始整数档 0–15，`round(raw * 100 / 15)` |
| `big_fan1_speed` | `aux_fan_percent` | 同上；此候选约定不能扩展到未知机型 |
| `ams.ams[].tray[].remain` | `ams[].trays[].remaining_percent` | 整数 0–100；−1、空串、非法值不是 0 |

数字字符串接受严格十进制格式，不接受布尔值、指数文本、NaN、Infinity 或越界值；非法值不夹紧。非法字段有历史值则 stale，否则 unknown，不撤销无关字段。对明确传入的 `A1` / `A1 mini` 能力映射，腔温和辅助风扇输出 unsupported（null/null）；未知型号所有未观察能力保持 unknown。其他机型名目前为 `P1P/P1S/X1/X1C/X1E`，识别名称不等于承诺每项能力或固件兼容。

## 任务、槽与拓扑作用域

任务身份候选字段是 `task_id/project_id/subtask_id` 的有界非空 token；token 只用于内存比较，不导出。有效任务作用域必须有本 generation 明确观察的 `task_id` 或 `subtask_id`，只重报 `project_id` 不能借旧代缓存恢复任务。已知字段身份变化或新旧身份字段完全无法比较，使任务 epoch 递增，旧任务进度、层数与剩余分钟清成 unknown；温度与风扇按自己的依据保留。运行态转入新的 `PREPARE/INIT` 也撤销旧任务作用域，包括任务 ID 被复用的情况。合法完成态仅保留设备明确上报的值，不推导 100%、0 分钟或自动完成。

AMS 只按数字 ID 合并：单元 0–15，槽 0–3，规范化为数字字符串并排序；不会取数组位置作为 ID。重复 ID 对应分支拒绝，其他有效分支仍可处理。重复/非法槽 ID、超量/非法槽数组会局部撤销对应槽/单元的有效性依据；无法定位的 AMS 顶层歧义保守撤销整个 AMS 依据，不影响温度等无关字段。历史数值保留 stale，后续无关 delta 不会恢复，必须重新明确观察有效身份与数值。部分数组缺席始终不删除任何单元/槽，即使 `baseline=True` 也不会从缺席猜完整列表的删除语义。

`tray_uuid` 是候选耗材身份；必须是有界非零 token，并在本 generation 明确观察。旧代缓存的 UUID 不能被新代仅含材料类型的 delta 恢复。`tray_info_idx` 作为材料信息可检测变化，**不能单独证明同类耗材未被替换**，因此没有有效 `tray_uuid` 不维持槽余量 valid。稳定已确认槽在完整基线与活跃证据条件下可以跨 180 秒保留 valid，`updated_at` 不动。UUID 或已知材料身份变化撤销旧余量，新余量未明确上报为 unknown。

为离线验收可在显式构造 `TelemetryState(..., offline_test_markers=True)` 后输入以下**合成回放约定**：槽对象 `empty: true` 清除耗材/余量；槽或单元对象 `removed: true` 移除相应出口，并保留本代槽 tombstone 拒绝旧身份复活。新 generation 可以通过新的明确观测重新确认已移除的槽。**生产默认 `offline_test_markers=False`，解析器忽略这些未验证字段；metadata 的 `offline_test_markers_enabled` 明示开关。**这些是测试输入约定，不是已核实的真实云字段。当前没有实现真实 AMS `tray_exist_bits`、完整列表删除、RFID 切换事件或硬件拓扑自动判定；真实空槽/删除语义仍须指定固件证据。不能将人工标记的通过当作真实云空槽已支持。

旧 generation、单调时间倒序帧、明确携带已退役任务/耗材身份的消息被丢弃；每个作用域最多保留 128 个退役身份以限制内存。**没有身份、可信源序列或上游 epoch 的同代迟到 delta，无法可靠区分于当前 delta**。在 128 次以上切换后也不承诺无限历史去重。实际源必须进一步验证身份完整性、顺序及基线机制；缺这些证据时不能宣称完全解决任意云乱序或保持所有任务/耗材字段持续有效。当前会话层的 generation 检查不能替代这一云侧证据。

## Liveness、质量与时间

活跃阈值默认 60 秒，仅检查最近有效目标证据的单调时间。基础合法遥测、合法任务状态、合法槽余量及离线合成拓扑事件可作为候选目标证据；`info/get_version`、`client.connected`、无效值、其他设备、未知 profile 和 broker ping 不续期。社区 `event.event == client.disconnected` 立即使旧数值 stale，不能由 broker 连接成功恢复。超时原因是 `telemetry_timeout`（目标遥测无证据），不宣称设备断电。

完整基线在当前 generation、目标 liveness、字段局部有效性及任务/槽身份都成立时，允许缺席字段按 `delta-retained/task-scoped/tray-scoped` 保留。liveness 丢失、换代或明确非 online 状态撤销基线保留依据。恢复部分 delta 仅恢复明确字段，不恢复整个缓存；即使先前没有请求 snapshot，超过阈值后的 delta 也会先撤销旧基线。非法字段不会因后续其他 delta 自动恢复。

`updated_at` 是接收端实际观察时间：同值明确上报也更新，缺席、ping、快照生成、完整性标记都不更新。保留输入时区；不使用未经验证的设备时间。墙钟前后跳不参与 liveness。墙钟后跳可能使历史实际观测时间晚于当前系统时间，快照用 `generated_at = max(now, latest_observed_wall)` 的逻辑墙钟保持字段时间不晚于 generated_at，metadata 标记 `logical-wall-max-observed` 与 `wall_clock_rollback`。不会篡改实际字段观察时间来掩盖时钟异常。

未知 profile 不使用旧版路径解码，不更新 liveness，不承诺任何 13 项指标支持。未观察字段 unknown，若已有缓存但 profile 依据消失则 stale。metadata 输出 `unsupported_protocol_profile` 与 `profile_verified_on_real_cloud: false`。

## 安全与资源边界

单报文最多 256 KiB、UTF-8 JSON 嵌套最多 32 层、AMS 最多 16 单元和每单元 4 槽；重复 JSON key、非 JSON NaN/Infinity 常量拒绝。未知字段不会进入状态或出口，异常/诊断只用固定类别，最多 32 个唯一诊断。不保存原始账户响应或原始 payload。内部身份 token 最长 128 字符，禁止导出；AMS/槽出口只接受规范化数字 ID。

`job.name` 始终 null：允许的自由文本字段同样可能承载账户数据、凭据或恶意内容，当前没有可信名称来源与脱敏证据。`job.state` 仅接受 `IDLE/PREPARE/RUNNING/PAUSE/FINISH/FAILED/SLICING/INIT`，无效原文不会导出。连接原因只接受固定类别，未知原因替换为 `transport_state`；model 与 profile 的未知原文也不会进入 metadata。后续若需要名称，必须设计可信来源与明确的安全策略。

## 离线验收证据

[独立遥测验收](../tests/test_telemetry.py)使用生产解析与状态对象，覆盖 AC-08a–l：首次 unknown/unsupported、190 秒稀疏 delta、同值观察、ping 不续期、超时后局部恢复、AMS 重连与乱序、任务/耗材 epoch、明确空槽/删除、未知 profile、局部非法值与 0、墙钟跳变、旧代/旧身份与 canary 出口。快照同时进行 JSON Schema 及观测时间不晚于 generated_at 的断言。另覆盖有界 payload、嵌套、重复 key、超量数组和风扇单位。

执行：`PYTHONPATH=src .venv-dev/bin/python -m unittest discover -s tests -p test_telemetry.py -v`。这些是 U 层合成证据；真实协议遗漏语义、目标活跃事件、baseline 自动识别、账户/固件兼容与 USB/HIL 全部保持 pending。
