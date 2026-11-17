# 数据契约与字段映射

本项目已选择A自研Bambu Cloud适配器，但当前只有占位读数，没有云或本地提供器。内部模型是待实现接口的契约；实施见 [需求](REQUIREMENTS.md)、[计划](IMPLEMENTATION-PLAN.md)，历史取舍见 [云端比较](CLOUD-OPTIONS.md)，职责见 [架构](ARCHITECTURE.md)。

## 两种结构不能混淆

[组件运行清单](../package/runtime/widget.json)的 `vars` 声明 `type/init`，属于包内静态定义。`var_bind` 让 UI 引用运行时变量；改 `init` 只改变初始化显示，不能实现持续采集。

[内部快照 Schema](../schemas/printer-snapshot.schema.json)用于计划中的 PC 提供器输出，保留数值、质量、时间和 AMS 层级。它不是直接发送到 ESP32 的协议；宿主投影能力仍待核验。完整合成示例见 [云快照样例](../examples/snapshot.cloud.example.json)，不得把该样例当成实时数据。

## 内部快照 v1

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前固定为 1；破坏性修改需显式升级 |
| `source` | `bambu-cloud` / `bambu-lan` / `home-assistant` |
| `printer.id` / `printer.model` | 目标设备 ID 和已识别机型；型号未知时为 null |
| `connection.state` | `unconfigured` / `auth_required` / `connecting` / `online` / `offline` / `error` |
| `connection.last_message_at` | 最近收到目标打印机有效遥测的时间，未收到为 null；不是 broker ping 时间 |
| `generated_at` | 当前快照生成时间，不代表每项读数都刚更新 |
| `job.state` / `job.name` | 原始任务状态和名称；未知为 null，后续 UI 自行本地化 |
| `metrics` | 九项基础指标，每项包含 value、quality、updated_at |
| `ams[]` | 已知 AMS 单元；其 trays 按 ID 标识，当前契约每单元最多四槽 |

一个指标的例子：

```json
{"value": 219.5, "quality": "valid", "updated_at": "2026-11-18T06:00:00+08:00"}
```

质量语义是契约的一部分：

| quality | value / updated_at | 意义 |
|---|---|---|
| `valid` | 数值 / 带时区时间 | 已获得，且当前协议/会话/任务或耗材作用域仍支持其有效性；0是正常数值，时间是实际观测时间 |
| `stale` | 保留最后数值 / 保留原时间 | 曾经有效，目前已过期 |
| `unknown` | null / null | 未获得可靠值、上游值无效或能力未确认 |
| `unsupported` | null / null | 已根据机型/设备能力确认没有该指标 |

所有时间使用 RFC 3339 带时区。状态和时间的业务一致性由未来提供器保证：不能把未来时间当作新鲜、同一帧中 AMS/槽 ID 必须唯一、字段时间不能晚于生成时间。JSON Schema 校验结构和字段约束，不替代这些跨字段逻辑、协议解析或 freshness 算法。

Schema 当前只支持四槽 AMS 单元，多 AMS 通过多个单元表示。不同形态的新硬件需要先验证并扩展契约，不能静默截断或按数组位置错配。

## 原始遥测 → 内部模型 → 当前 UI

以下为常见旧版 `print` 结构。完整消息根节点先取 `print`；新固件兼容见 [云协议研究](CLOUD-PROTOCOL.md)。

| 原始 print 字段 | 内部 metrics 字段 | 当前 UI 变量 | 转换 |
|---|---|---|---|
| `mc_remaining_time` | `remaining_minutes` | `remain` | 非负整数分钟 → HH:MM；0 需结合任务状态判断，不能一律认定未知 |
| `mc_percent` | `progress_percent` | `prog` | 整数 0–100 |
| `layer_num` | `current_layer` | `layer` | 非负整数 |
| `total_layer_num` | `total_layers` | `layers` | 非负整数；0 可代表尚未获知 |
| `nozzle_temper` | `nozzle_celsius` | `nozzle` | 模型保留小数，最后显示时取整 |
| `bed_temper` | `bed_celsius` | `bed` | 同上 |
| `chamber_temper` | `chamber_celsius` | `chamber` | 先确认机型支持 |
| `cooling_fan_speed` | `part_fan_percent` | `fan_part` | 确认是 0–15 原始档后换算 |
| `big_fan1_speed` | `aux_fan_percent` | `fan_aux` | 常见结构为辅助风扇；按机型确认 |
| `ams.ams[].tray[].remain` | `ams[].trays[].remaining_percent` | `tray1`～`tray4` | 用户选定 AMS，再按槽 ID 映射 |

旧文档中的 `big_fan1_speed / big_fan2_speed` 不能互换：`big_fan2_speed` 通常是腔体风扇，不是辅助风扇。旧文档简写的 `ams.tray[]` 也遗漏了 AMS 单元数组。

风扇第一版可采用 `round(raw * 100 / 15)`。ha-bambulab 采用向上取整到 10% 桶，目的是减少原始档位抖动；两者有显示差异，方案实施时应选择并在测试中固定，不把原始档位直接当成百分比。源代码参考 [fan_percentage](https://github.com/greghesp/ha-bambulab/blob/main/custom_components/bambu_lab/pybambu/utils.py)。超范围或无法解析值应标 unknown，不静默夹到 0 或 100 后冒充有效值。

当前 UI 只有单个喷嘴和四个槽。双喷嘴需要明确选择目标喷嘴；多 AMS 需要明确选择目标单元，不能默认取数组第一项。喷嘴/AMS 选择与 UI 扩展在接入后续阶段确定。

## 增量和过期规则

未来实现应满足：

1. 第一次连接没有遥测时，所有指标 unknown；仅知道机型不支持的项目可设 unsupported。
2. 收到增量只更新出现且有效的字段及其时间；未出现字段保留旧值和旧时间。
3. AMS 按单元 ID 与槽 ID 合并。空槽/明确移除应清除缓存；缺席于本次增量不代表移除。完整快照与增量的删除语义需按机型验证。
4. 原始 `remain=-1`、空字符串、缺失或机型不支持都不能被处理成真实 0%。耗材余量是设备估计，不一定存在或可靠。
5. 过期按观测依据判断：字段缺席不更新时间，但已验证profile下的稳定字段，在当前generation基线、目标设备活跃和任务/耗材作用域仍有效时可以继续valid。不能因总层数或AMS余量超过60秒未变化就一律stale。失去设备活跃证据、重连换代、换任务/槽或未知profile时分别失效，旧值stale、从未获得值unknown；局部新字段不能假装完整基线恢复。详见 [FR-08](REQUIREMENTS.md)与 [AC-08](ACCEPTANCE.md)。
6. 60秒只是目标设备活跃证据超时的设计初值，见 [配置示例](../examples/provider.example.toml)，不是云推送SLA或统一逐字段TTL。broker ping不证明打印机在线；收到同值的有效字段才可更新该字段真实观测时间，重新生成快照或单纯重连不能刷新旧时间。
7. 凭据失效进入auth_required，不循环发送验证码。设备、账号、区域或任务/耗材身份变化须隔离对应缓存与有效性；不得串机。

## 设备显示的待办

当前 13 个变量都是 string/int，不能直接接收 null，也未实现 stale、unsupported 或真实状态标题。接入前需要单独设计“未知/不支持/过期”的可见状态，避免用初始化 0 误导。

机型标题和“等待数据”现在是静态文本。新增 gcode_state、连接状态和质量变量需同时核对变量配额、宿主投影与固件能力，不应只把状态硬写为“打印中”。

## 凭据隔离与测试范围

内部快照不包含账号、uid、access token、LAN code、相机 URL 或全量原始消息。严格 Schema 会拒绝额外字段，但防止把秘密写入允许的字符串字段仍需提供器代码脱敏和审查。

[契约测试](../tests/test_snapshot_contract.py)覆盖零值/缺测/不支持/过期、时间格式、范围、来源和额外凭据字段。这些测试不验证登录、增量合并实现、云订阅、USB 或设备能力；那些功能目前尚未实现。
