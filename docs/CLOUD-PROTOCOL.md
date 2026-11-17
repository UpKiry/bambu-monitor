# Bambu Cloud 协议核实记录

核查日期：2026-11-18（Asia/Shanghai）。依据是公开社区源码和文档，没有登录用户账号、调用账户接口、连接真实 MQTT 或修改打印任务。**本文不代表真实云链路、USB 或实机验收通过。**

用户已选择A自研云适配器，提供器尚未实现，实施入口见 [计划](IMPLEMENTATION-PLAN.md)，历史取舍见 [方案比较](CLOUD-OPTIONS.md)。本阶段排除打印机本地MQTT，使用云MQTT。以下是社区复现的私有协议，不是官方稳定开放API。

## 证据版本与许可证

本次读取 main Atom 的首条提交，并以固定 SHA 的 patch 交叉核对。时间是上游提交时间，不是接口验证或 release 时间。

| 来源 | 固定提交 SHA | 提交时间 UTC | 许可证 |
|---|---|---|---|
| ha-bambulab | `e3c73139d443e9183ed2f804be30d2f2e53bbe66` | `2026-11-16T20:45:18Z` | MIT |
| OpenBambuAPI | `cc383a2c96576a9f53391c879acfb8dca5c534e4` | `2026-08-07T20:33:28Z` | GNU Free Documentation License 1.3 |

- ha-bambulab：[main feed](https://github.com/greghesp/ha-bambulab/commits/main.atom)、[固定提交](https://github.com/greghesp/ha-bambulab/commit/e3c73139d443e9183ed2f804be30d2f2e53bbe66)、[MIT LICENSE](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/LICENSE)。
- OpenBambuAPI：[main feed](https://github.com/Doridian/OpenBambuAPI/commits/main.atom)、[固定提交](https://github.com/Doridian/OpenBambuAPI/commit/cc383a2c96576a9f53391c879acfb8dca5c534e4)、[GFDL LICENSE](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/LICENSE.md)。不能误标为 MIT。

本文归纳接口事实并链接证据，不复制上游大段文档。后续复用代码时应针对实际选用文件保留版权和许可；协议参考不等于软件依赖。

## 1. 区域

| 本项目设计配置 | HTTP 基址 | 云 MQTT broker |
|---|---|---|
| `cn` | `https://api.bambulab.cn` | `cn.mqtt.bambulab.com` |
| `global` | `https://api.bambulab.com` | `us.mqtt.bambulab.com` |

中国区 MQTT 也使用 `.com` 域名。区域按账号所属服务区选择，不按电脑位置猜测。ha-bambulab 对 China 单独映射，其他地区统一使用全球 API 和 US broker。

证据：[常量](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/const.py)、[区域转换](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/utils.py)、[云实现](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)。[配置样例](../examples/provider.example.toml)的 `cn/global` 是本项目设计名，不是登录 API 字段，当前也没有加载器。

## 2. 登录、验证码与 MFA

下列 HTTP 路径使用所选 API 基址，网站 MFA 路径另列。分支来自参考实现，实际账号行为待实测。

| 步骤 | 请求 | 关键字段 / 行为 |
|---|---|---|
| 密码登录 | `POST /v1/user-service/user/login` | `account`、`password`、`apiError: ""` |
| 请求邮箱验证码 | `POST /v1/user-service/user/sendemail/code` | `email`、`type: "codeLogin"` |
| 验证码登录 | `POST /v1/user-service/user/login` | `account`、`code`，不同时发送密码 |
| MFA 前置 | 网站域 `GET /api/csrf` | 参考实现检查 204 和 `bbl_csrf_token` cookie |
| MFA 提交 | 网站域 `POST /api/sign-in/tfa` | `tfaKey`、`tfaCode`；成功 token 可能在 cookie `token` |

登录返回 `accessToken` 则成功；`loginType: "verifyCode"` 进入验证码；`loginType: "tfa"` 保存临时 `tfaKey` 后进入 MFA。不要假定所有响应都有 `success: true`。

网站 MFA 全球区使用 `https://bambulab.com`，中国区使用 `.cn`，不是 API 域。参考实现携带 CSRF cookie 和 `x-bbl-csrf-token`。网站路由/CSRF 会变化，ha-bambulab 已处理失败后改走邮箱/SMS 验证码；不能承诺所有账号的 TOTP 当前都可用。

中国手机号分支在参考实现使用 `https://bambulab.cn/api/v1/user-service/user/sendsmscode`，字段 `phone`、`type: "codeLogin"`。首版若仅支持邮箱须明确限制。Google/Apple/Facebook 注册用户可能需先设置 Bambu 账号密码。登录、验证码、MFA 和重登均应是用户在 PC 本地进行的交互，不自动反复发验证码。

证据：[认证代码](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)、[配置及 CSRF fallback](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/config_flow.py)、[上游配置说明](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/README.md)。

## 3. Token 生命周期

OpenBambuAPI 列有 `POST /v1/user-service/user/refreshtoken`，字段 `refreshToken`，但同时警告已观察为持续返回 401，已知情形中 refresh token 与 access token 相同且生命周期相同。**不能依赖它承诺永久自动续期。**

历史样例 `expiresIn: 7776000` 约 90 天，不是所有账号固定期限；token 也不保证是 JWT。当前参考云类没有可靠自动刷新实现。未来提供器应：

- 将 token 存在 PC 密钥库，秘密和原始账户响应不进仓库、包、设备或日志。
- 记录服务器明确提供的期限，以真实认证结果判断有效性。
- HTTP 401 或 MQTT 认证拒绝后进入 `auth_required`，由用户重登，停止无效重试。
- 区分 403 权限/CSRF/防护、429 限流和服务故障；临时错误退避。

证据：[固定 HTTP 文档 refresh 警告](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/cloud-http.md#post-v1user-serviceuserrefreshtoken)。

## 4. 设备发现和 UID

HTTP 鉴权：`Authorization: Bearer <accessToken>`。

| 用途 | 路径 | 确认字段 |
|---|---|---|
| 绑定设备 | `GET /v1/iot-service/api/user/bind` | 根 `devices[]` |
| 账号 UID | `GET /v1/design-user-service/my/preference` | 根 `uid` |
| 任务记录 | `GET /v1/user-service/my/tasks` | `hits[]`，任务关联与元数据 |

设备常见字段为 `dev_id`、`name`、`online`、`print_status`、`dev_model_name`、`dev_product_name`、`dev_access_code`、`nozzle_diameter`；新版文档还记录 `print_job`、`dev_structure`。字段存在性待实测。

`dev_id` 用于选设备及 MQTT topic。`dev_access_code` 是本地码，云 MQTT 不使用它，规范化出口应剔除。旧 JWT 可能带 `username`，新版未必有；通用获取 UID 的路径应是 preference API，不能靠拆 token 猜。

证据：[HTTP 协议](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/cloud-http.md)、[设备和 UID 实现](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)。

## 5. 云 MQTT

| 参数 | 核实值 |
|---|---|
| broker | 区域表中域名 |
| 端口 / TLS | `8883`，标准证书验证 |
| 协议参考 | MQTT v3.1.1 |
| username | `u_<uid>` |
| password | 完整 `accessToken`，不加 `Bearer` |
| 订阅 | `device/<dev_id>/report` |
| 请求 | `device/<dev_id>/request` |

参考客户端使用唯一 client ID、keepalive 30 秒和重连退避。这些是实现值，不是云服务保证。后续项目可自行定义退避上限和 jitter。

参考初始化：连接 → 订阅 report → 请求版本和完整状态。应等待订阅就绪并验证目标固件响应。

```json
{"info":{"sequence_id":"0","command":"get_version"}}
```

```json
{"pushing":{"sequence_id":"0","command":"pushall"}}
```

这不是本项目已发送成功的记录。不要高频请求 `pushall`；OpenBambuAPI 特别记录 P1P 高频完整状态请求的负担。

`print.command: "push_status"` 承载遥测，`info.command: "get_version"` 承载版本。云消息还可能含 `event.event: "client.connected" / "client.disconnected"`。broker 已连接、订阅成功、打印机在线、完整状态就绪必须分别判断。

证据：[MQTT 文档](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/mqtt.md)、[客户端实现](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_client.py)、[初始化命令](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/commands.py)。

## 6. 元数据、实时读数与固件差异

设备列表的 `online/print_status` 是摘要，任务记录包含名称、封面、耗材等元数据；文档还列 `/v1/iot-service/api/user/print?force=true` 摘要，但不能据此承诺 HTTP 补齐温度、风扇、层数、AMS 等全部实时数据。切片预计时间也不能当作任务实际已运行时间。

常见实时字段为 `print.mc_percent`、`mc_remaining_time`、`layer_num`、`total_layer_num`、`nozzle_temper`、`bed_temper`、`chamber_temper`、`cooling_fan_speed`、`big_fan1_speed` 和 `print.ams.ams[].tray[]`。具体路径和 UI 映射见 [数据契约](DATA-ADAPTER.md)。`big_fan2_speed` 通常为腔体风扇，不能当辅助风扇。

部分机型增量上报：缺席字段保留旧值和旧时间；AMS 按单元/槽 ID 合并；未知、不支持、过期、有效 0% 分开处理。A1 系列腔温、辅助风扇等不能因为 UI 预留了变量就假定存在。传统 raw 0–15 档、控制值 0–255、库 getter 百分比不可混用。

上游已包含以下新固件结构，**本项目仅作为未来验证项记录**：

- `print.device.bed.info.temp`、`print.device.ctc.info.temp`。
- `print.device.extruder.info[].temp`，按喷嘴 ID 区分。
- packed `temp` 的低 16 位为当前值，高 16 位为目标值。
- 双喷嘴旧顶层 `nozzle_temper` 可能代表激活喷嘴，不能固定当作某一侧。

新固件控制授权/签名限制不等于云传感器不可读；云传感器可读也不能推出暂停、加热等控制可用。本项目仅研究监控。

证据：[机型和模型解析](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/models.py)、[风扇换算](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/utils.py)、[固件授权说明](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/README.md)。

## 7. 社区项目范围

- **ha-bambulab**：本次认证与遥测主要依据；可作完整 HA 云桥，也可评估内部 pybambu，后者不承诺独立 SDK 稳定性。
- **OpenBambuAPI**：协议文档和示例，不是装上即具备完整认证/重连/数据服务的 SDK；历史内容应结合当前实现核对。
- **BambuTools/bambulabs_api**：当前公开主路径 `Printer(IP, ACCESS_CODE, SERIAL)` 面向本地 MQTT/FTP/相机；未核实到完整云账号登录/验证码/MFA和云设备发现链路，不推荐为即插即用云 SDK。[README](https://github.com/BambuTools/bambulabs_api)、[API 文档](https://bambutools.github.io/bambulabs_api/api.html)。这里只用于选型范围说明，未来采用时须重新固定版本。

## 8. 项目边界与待验证项

[内部 Schema](../schemas/printer-snapshot.schema.json)和[配置样例](../examples/provider.example.toml)都是未来提供器约定，没有可运行的配置加载、HTTP/SSE/WebSocket 路由或登录命令。现有 `widget/data` 列表协议不自动支持自定义 surface 变量；USB、宿主能力与实机渲染单独验收。

| 项目 | 已有证据 | 仍须验证 |
|---|---|---|
| 区域和端点 | 固定源码映射 | 实际账号区域和可达性 |
| 登录 | 密码/验证码/MFA 分支 | 实际账号、CSRF/TOTP 当前行为 |
| token | refresh 不可靠的社区记录 | 实际寿命和失效表现 |
| 云 MQTT | broker/鉴权/topic/客户端 | 选定打印机权限、首帧、重连 |
| 遥测 | 传统字段、增量实现 | 目标机型/固件、单位、推送频率 |
| AMS/新机型 | 上游分支 | 缺测、估计余量、多喷嘴语义 |
| USB/surface | 设计边界 | 宿主能力、通用变量投影、实机更新 |

没有证据支持承诺统一固定限流额度、可靠 refresh、全部机型完整 13 项数据或“云连通即上屏”。后续分别报告认证、HTTP、云 MQTT、规范化、USB和实机状态。
