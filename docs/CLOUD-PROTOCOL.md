# Bambu Cloud 协议与实现边界

核对日期：2026-11-18。当前已有独立 HTTP/MQTT 适配器、私有认证及本地采集代码；真实账号、云网络、目标固件、宿主投影和实机验证仍 **pending**。社区资料描述私有协议，不构成官方稳定 API 保证。
使用入口见[本地服务](LOCAL-SERVICE.md)，数据语义见[数据适配](DATA-ADAPTER.md)，验证分层见[验证记录](VERIFICATION.md)。

## 固定来源与许可

| 来源 | 固定 SHA / 读取范围 | 许可与采用边界 |
| --- | --- | --- |
| ha-bambulab | `e3c73139d443e9183ed2f804be30d2f2e53bbe66`；[HTTP](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)、[MQTT](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_client.py)、[常量](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/const.py)、[区域转换](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/utils.py)、[初始化命令](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/commands.py) | [MIT](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/LICENSE)；仅核对必要协议事实，未引入 HA/pybambu 实现 |
| OpenBambuAPI | `cc383a2c96576a9f53391c879acfb8dca5c534e4`；[HTTP](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/cloud-http.md)、[MQTT](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/mqtt.md) | [GFDL 1.3](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/LICENSE.md)；归纳事实，未整体复制文档或样例 |
| Eclipse Paho | v2.1.0，commit `af64a4365c6ac5a7a4d339e7b00f44df91353b35`；[client.py](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/src/paho/mqtt/client.py)、[README](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/README.rst) | [EPL-2.0 / EDL-1.0](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/LICENSE.txt)；唯一 MQTT 运行依赖，固定版本并测试私有 hook |
| coelacant1/Bambu-Lab-Cloud-API | `29458fdd5b7ef7f8f71781aadccfb917c3ebed45`；[README](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/README.md)、[认证](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/API_AUTHENTICATION.md)、[MQTT](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/API_MQTT.md) | [LICENSE](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/LICENSE) 为 GPLv3 正文，README 称 AGPL-v3；只交叉阅读，未复制或依赖其实现 |

[BambuHelper README](https://github.com/Keralots/BambuHelper/blob/main/README.md)仅用于参考“网页登录 → 绑定设备 → 保存配置 → 监控状态”及 Web portal 流程；未声称审查完整代码，未复制源码、素材、密码存储或控制实现。许可说明见[第三方声明](../THIRD_PARTY_NOTICES.md)。
上述来源中的 token 固定寿命、固定限流和固定推送周期均不作为本项目保证；分发依赖仍须保留其原许可材料。

## 区域、HTTP 路由与认证

区域由用户按账号服务区明确选择，不按电脑位置猜测，不跨区回退。

| 配置 | HTTP API 基址 | 固定云 broker |
| --- | --- | --- |
| `cn` | `https://api.bambulab.cn` | `cn.mqtt.bambulab.com` |
| `global` | `https://api.bambulab.com` | `us.mqtt.bambulab.com` |

[HTTP 实现](../src/bambu_monitor/cloud_http.py)只允许以下五条路由；除 SMS 外均使用所选 API 基址。

| 请求 | 路由 | 当前行为 |
| --- | --- | --- |
| POST | `/v1/user-service/user/login` | `account+password`（可带空 `apiError`）或 `account+code`，严格互斥 |
| POST | `/v1/user-service/user/sendemail/code` | `email` 与固定 `type: codeLogin`；用户显式发送一次 |
| POST，仅国区网站 | `https://bambulab.cn/api/v1/user-service/user/sendsmscode` | `phone` 与固定 `type: codeLogin`；不能改成 API 域或丢掉 `/api` 前缀 |
| GET | `/v1/iot-service/api/user/bind` | Bearer token、空请求体；只保留唯一 `dev_id` 与已知 model |
| GET | `/v1/design-user-service/my/preference` | Bearer token、空请求体；只保留 1–32 位数字 UID，统一为字符串 |

国区当前接受邮箱或大陆 11 位手机号；全球区仅邮箱。这是本地支持范围，真实云分支仍待验证。设备列表最多 128 台，非法/重复 ID 整批拒绝，不默认选择第一台；`dev_access_code`、设备自由名称和完整账户资料不出口。
登录只保留 `accessToken`、已知 `loginType`、有效 `expiresIn`；`verifyCode` 进入验证码交互，`tfa` 仅用于安全诊断 unsupported，不保留 `tfaKey`。
邮箱/SMS 成功响应允许 HTTP 200 空 body，不要求总有 `success: true`；不自动重发验证码。
网站 TOTP/MFA/CSRF、refresh、任务记录、文件/相机、上传/删除和打印控制均不在当前 HTTP 白名单内。社区网站 MFA 流程不应写成本项目已实现。
OpenBambuAPI 记录 refresh 持续返回 401 的情形；不依赖永久续期，不假定 token 是 JWT，也不从 token 猜 UID。HTTP 401/MQTT 认证拒绝进入 `auth_required`，等待用户重登。
所有非 200 只返回状态与空 body，不读错误正文、Cookie 或 Location；可区分 401/403/429，但不能进一步区分 HTTP 400 正文里的验证码过期/错误。

## HTTP 资源、超时与取消

导入/构造不联网；标准库直接 HTTPS，使用系统 CA 与 hostname 验证，不读取代理环境、不跟随重定向、不存 cookie、不自动重试。URL、method、body 均严格白名单化，不提供任意 header 注入。
单实例只允许一条在途请求；请求体最多 16 KiB、成功响应最多 64 KiB，分块读取每次最多 4096 字节；严格 UTF-8/object JSON，深度最多 32，拒绝重复 key、非有限数和压缩响应。
响应头后置检查最多 64 项/合计 16 KiB，Content-Length、chunked 与实际长度须一致；标准库先解析响应头，后置检查不能称为解析器硬内存上限。
默认超时 10 秒，可配置正有限预算 ≤60 秒；各网络阶段和每个读取块前后以单调时钟检查预算，对已有 socket 设置剩余 timeout，拒绝迟到结果。
**这不是任意阻塞的硬总 deadline。** 系统 DNS、连接工厂内部和标准库单次读头/chunk 行中的多次 recv 无法由本实现强制中断；返回后才检查超期，不创建隐蔽后台请求伪称已停止。
`close()` 先撤销请求资格，对已公开 active socket 执行 shutdown/close；关闭失败保留待清理连接、终止实例并报告 `close_failed`，后续请求不能新建 socket 绕过。DNS 尚未返回时只能撤销资格，不能承诺立即强取消。
异常仅输出固定 value-free 类别，不保留原 URL/body/异常链；私有 token/UID 响应也不得打印。

## 云 MQTT：只读初始化与会话

[MQTT 适配器](../src/bambu_monitor/cloud_mqtt.py)只接受区域表的 broker、8883、MQTT v3.1.1、TLS ≥1.2、系统 CA/主机名验证；固定依赖 `paho-mqtt==2.1.0`，导入模块不联网或创建线程。
鉴权为 username `u_<uid>`、password 完整 access token（不加 Bearer），不使用 LAN Access Code；client ID 为唯一 `bambu-monitor-<32hex>`，keepalive 30 秒是实现值。
只订阅选定 `device/<dev_id>/report`（请求 QoS1）；只在匹配 MID 的单项 SUBACK 授予 QoS0/1 后就绪。CONNECT 返回、CONNACK、SUBACK、目标在线、完整基线分别判断。
SUBACK 后只允许向选定 `device/<dev_id>/request` 发送两个精确初始化消息：

```json
{"info":{"sequence_id":"0","command":"get_version"}}
{"pushing":{"sequence_id":"0","command":"pushall"}}
```

每 client 每命令最多尝试一次，不自动重发未知结果；QoS0、不 retain，Paho 队列上限 2、inflight 1。[Session](../src/bambu_monitor/session.py)另限制跨代初始化间隔 300 秒；不定时 pushall 制造心跳，不开放暂停、加热等控制。
仅转发订阅就绪后的精确目标 report、bytes、payload ≤64 KiB；丢弃 retained 消息，不把 broker 旧缓存当新观测。`push_status`、版本响应与连接事件的语义由[数据适配](DATA-ADAPTER.md)区分。
Paho 在 callback 前会分配完整报文；固定 `_sock_recv` hook 在读 body 前拒绝 Remaining Length >64 KiB+512，callback 再检查 payload，不能只靠 callback 声称有界。

## MQTT 调度、队列与停止边界

一个 owner 线程同步 `connect()` 后手动 `pump(timeout≤1)` 并 drain；不用 `connect_async+loop` 代替首次连接，不启动 Paho 网络线程或自动重连。恢复由 Session/采集 runner 管理。
固定 Paho 的 TLS 握手原本用 keepalive 超时；本项目私有 hook 将握手 timeout 限为配置值 ≤2 秒，并保留验证及失败关闭。TCP timeout 与 TLS timeout 是不同阶段，DNS/多地址尝试不受单个 socket timeout 总括。
所以“参数 2 秒”不等于总连接/退出硬 2 秒；runner 的 CONNACK/SUBACK deadline 也不能中断已阻塞的同步 DNS。真实平台 DNS/黑洞/TLS 及停止 5 秒目标仍 pending。
Session 遥测队列默认 64，可配置 1–4096；单次 drain 最多处理该预算。队列溢出可能遗漏身份变化，因此撤销整代并清队列，不能丢一帧后继续把旧状态当有效。
旧 client/epoch 回调与旧 generation 报文隔离；断开/停止清控制动作及遥测队列。认证拒绝不重连，其他故障使用有上限的指数退避与 jitter。
关闭立即撤销 epoch/callback，清 socket/socketpair、凭据引用及待发送 CONNECT 字节，不等待远端 DISCONNECT 或 join。清理失败保留 pending-cleanup client、报告失败并拒绝新连接，可再次 close 清理。
三处私有 hook 涉及 TLS 握手、入站大小与 socketpair 清理；升级 Paho 必须复核。清引用不等于 Python 秘密内存物理归零。

## 代码与验证入口

| 入口 | 可验证范围 |
| --- | --- |
| [HTTP 测试](../tests/test_cloud_http.py) | 同一生产 transport 的路由/SMS、TLS、最小投影、资源限制、deadline、关闭及异常脱敏 |
| [MQTT 测试](../tests/test_cloud_mqtt.py) | client 参数、SUBACK、只读白名单、队列/报文 hook、旧回调、失败清理；真实 Paho 对象只接内存 fake socket |
| [会话测试](../tests/test_provider.py)、[采集测试](../tests/test_collector.py) | 共用认证/Session/状态链路、代次、恢复和停止边界 |

按[开发说明](DEVELOPMENT.md)安装依赖后使用统一 `python tools/project.py check`；无需预先读取或保留本机 `.build` 研究产物。
这些是合成/离线证据；当前没有真实云 HTTP、验证码、MQTT TLS/权限/SUBACK/首帧、目标初始化响应或持续断网恢复通过记录。
新固件 `device.*`、packed 温度、双喷嘴及真实 AMS 空槽/删除语义尚不支持当前候选解析；不能承诺全部机型提供完整 13 项，也不能把云可读推成 USB/宿主上屏或任务控制已通过。
