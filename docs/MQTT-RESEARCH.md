# 云 MQTT 适配器：公开资料核对与实现边界

核对日期：2026-11-18。方案 A 不变；本轮独立实现 [PahoMqtt](../src/bambu_monitor/cloud_mqtt.py)及[离线测试](../tests/test_cloud_mqtt.py)，没有连接真实 broker、使用真实账号或控制打印任务。模块只消费PC上的Secret、输出原始目标遥测到既有Session；不访问USB、不修改组件包，不把broker连接当打印机在线或完整基线。

## 固定资料、事实与许可

| 资料 | 固定版本 / 事实 | 许可与采用方式 |
| --- | --- | --- |
| [Paho 2.1 tag对象](https://api.github.com/repos/eclipse-paho/paho.mqtt.python/git/tags/a5855fb2a7e289912640d8a1cf9cb89558843ef9) | v2.1.0指向commit `af64a4365c6ac5a7a4d339e7b00f44df91353b35` | 固定源码核对，运行依赖建议pin `paho-mqtt==2.1.0`，升级需重验私有hook |
| [Paho固定client.py](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/src/paho/mqtt/client.py) | VERSION2回调；connect_async仅配置异步状态；connect调用reconnect创建socket并发送CONNECT；loop不负责首次异步连接/自动重连 | [双许可声明](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/LICENSE.txt)：EPL-2.0 / EDL-1.0；作为独立依赖使用，未复制其客户端源码 |
| [固定README网络loop](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/README.rst#network-loop)及[在线API](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html) | 手动loop需定期调用、调用者负责恢复；loop_start产生线程；loop_stop会join；新版文档建议external-event-loop或托管loop而非裸loop | 这里只用明确的owner pump封装手动loop，不混用三种调度方式，不调用loop_start/forever/stop |
| [ha-bambulab客户端](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_client.py) | MQTTv311、8883、唯一clientID、云TLS验证、report/request topic、keepalive30及两条初始化请求 | [MIT](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/LICENSE)，版权ha-bambulab contributors；只核对协议事实，独立精简实现，不引入HA/pybambu、相机、FTP或线程模型 |
| [ha-bambulab固定commands](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/commands.py) | `info/get_version`、`pushing/pushall`，均sequence_id字符串`0` | 仅采用必要协议字段事实，不引入同文件其他控制命令 |

研究时额外下载官方PyPI wheel，留在本机忽略目录`.build/mqtt-research`用于核查。`paho_mqtt-2.1.0-py3-none-any.whl` SHA-256 `6db9ba9b34ed5bc6b6e3812718c7e06e2fd7444540df2455d2c51bd58808feee`；其中`paho/mqtt/client.py` SHA-256 `5a3f0f73b344541828586a90f79a6beecb8e4396d8b81064bc2920c1a853618a`。公开源码与本机wheel逐点核对；临时下载不进入源码/组件包。

本仓库适配器为独立实现。依赖的[EDL-1.0正文](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/edl-v10)要求源/二进制再分发保留版权、条款及免责；整体分发依赖时应保留原发行物的许可材料，不能把Paho标为MIT。公开协议参考不构成官方稳定API保证。

## 核对后修正的两个关键点

**不能只调用connect_async后手动loop。** Paho 2.1 `connect_async`（源码1485附近）只写host/port/状态；首次`reconnect()`在`loop_forever`中驱动，`loop_start`实际上启动线程运行该函数。`loop()`（1606附近）只进入socket network处理，不驱动CONNECT_ASYNC。故本实现用同步`connect()`建立底层连接，再由owner每次`pump()`手动调用`loop(timeout)`；不自动重连，恢复由Session/runner统一管理。

**connect_timeout不等于TLS握手超时，更不是全流程deadline。** 固定Paho源码`_create_socket_connection`（4632–4640）将connect_timeout交给`socket.create_connection`；但`_ssl_wrap_socket`（4670–4671）把keepalive作为TLS握手timeout。直接设connect_timeout=2、keepalive=30仍可能TLS阻塞30秒。本实现用一个小型独立subclass hook替换`_ssl_wrap_socket`，保留系统CA、主机名验证、SNI和TLS>=1.2，把握手timeout固定为用户配置的≤2秒，并在异常时关闭包装socket。没有关闭证书验证，也不采用上游LAN的非安全context。

**尚不能保证总连接deadline≤2秒。** socket.create_connection的系统DNS解析和多地址尝试不受单个socket超时约束，TCP与TLS还是独立阶段；CA加载也不是硬实时操作。当前没有DNS worker/进程、异步resolver或系统强取消。外层runner可检测连接返回后CONNACK/SUBACK期限，却不能中断已经阻塞的同步DNS；不把“参数2秒”称为整个连接或退出的硬2秒保证。真实平台的DNS/黑洞/TLS停止时限仍pending。

## 实际API与owner调用

```python
adapter = PahoMqtt(printer_id, connect_timeout=2.0)
session = Session(config, adapter)
session.start(uid=Secret(uid), token=Secret(token), monotonic=time.monotonic())
# Owner loop; CONNACK/SUBACK deadline、信号、退出与Session恢复由runner管理。
adapter.pump(timeout=0.1)
session.drain()
snapshot = session.snapshot(now=aware_now, monotonic=time.monotonic())
session.stop()
```

`connect`完整兼容当前Session的关键字签名；它返回只表示底层CONNECT发送成功，不表示CONNACK、订阅、目标遥测或baseline已就绪。`pump(timeout=0.1)`返回None或仅含固定类别的`SafeMqttError`；范围0–1秒，runner须定期泵并在安全异常时撤销Session/清理transport。所有公开I/O方法要求创建对象的owner线程。库回调也只接受该owner及当前client/epoch，旧client即使保留回调函数也无法改变新会话。

Paho延迟导入；`PahoMqtt.check_available()`可在任何私有输入前预检import和发行版本，成功返回None，缺依赖/非2.1.0分别报告`mqtt_dependency_unavailable`/`mqtt_dependency_version_unsupported`，不构造client或socket。导入模块不联网、不产生线程。本实现使用`CallbackAPIVersion.VERSION2`、MQTTv311、clean_session=True、reconnect_on_failure=False；永不调用loop_start/loop_forever/loop_stop/reconnect。关闭不等待远端DISCONNECT或join线程，立即撤销epoch和callbacks、关闭socket/socketpair、清Paho用户名/密码及待发送CONNECT字节；仅全部成功才释放client引用。任一步清理失败保留私有pending-cleanup client以供下一次close重试，持续报告`mqtt_close_failed`并拒绝新connect；不能通过第二次close/stop绕过失败。repr只含active/cleanup_pending布尔状态。

## 有界和只读约束

- broker仅接受`cn.mqtt.bambulab.com` / `us.mqtt.bambulab.com`，仅8883+TLS；client ID使用Session生成的`bambu-monitor-<32hex>`；用户名/密码必须是Secret，不接受LAN身份。
- subscribe只允许选定`device/<id>/report`，请求QoS1。就绪只在匹配返回MID且单项SUBACK授予QoS0/1后确认；错误MID、重复SUBACK和失败码不能就绪。可处理注入client在subscribe返回MID前同步回调的情况，临时结果最多一个。
- publish只允许选定request topic及精确结构的get_version/pushall，sequence_id必须是字符串0，无额外字段/重复JSON键；每client每命令最多尝试一次，不自动重发未知结果。出站QoS0、不retain，Paho队列上限2、inflight1，应用入口实际最多两条。跨代300秒初始化限频仍由Session负责，adapter不另开重试器。
- 只在SUBACK之后转发精确目标report、bytes、payload≤65536。保留消息retain=True丢弃，避免把broker旧缓存当本次新观测；不擅自证明打印机仍在线。时间为接收观察时间及单调时间。
- Paho 2.1本身在on_message前已分配完整报文，单在callback做64KiB判断不构成内存上限。本实现另以版本固定的`_sock_recv` hook在读取declared packet body前拒绝Remaining Length>65536+512；512预算容纳有界topic与MQTT头，callback仍严格限制payload65536。超大声明经真实Paho `_packet_read`＋内存fake socket测试，确认只读取1字节header直到拒绝，不读取大body。
- 私有hook仅三处：有界TLS握手、入站body准入、关闭socketpair；另清理Paho待发送CONNECT字节。升级Paho需重验这些内部结构，当前pin2.1.0。
- 不开启库logger、不传递异常正文、token或原始账户响应到diagnostics；异常出口只有固定类别。Python不可承诺秘密内存物理归零，清理指不再持有这些引用，不伪称安全擦除。

## 离线验收

[17项测试](../tests/test_cloud_mqtt.py)覆盖TLS/客户端参数、owner pump、broker/topic与出站控制拒绝、MID/失败/同步SUBACK、QoS/去重、64KiB/retained/时间、认证拒绝、无自动恢复、异常脱敏、失败清理、旧client回调隔离，以及真实adapter→Session→TelemetryState共同处理链路。私有hook测试独立验证TLS timeout=2和失败关闭；真实Paho对象的构建/字段清理及其packet parser只接内存fake socket，禁止真实网络。

验证命令：

```sh
PYTHONPATH=src:.build/mqtt-research/paho_mqtt-2.1.0-py3-none-any.whl \
  .venv-dev/bin/python -m unittest discover -s tests -p test_cloud_mqtt.py -v
```

结果：17/17 passed，未连接broker；未安装可选Paho时16 passed、1 skipped。调用方安装固定依赖后可直接用统一check执行，无需临时wheel路径。真实Bambu云认证/权限、TLS连接、SUBACK、首帧、固件初始化响应、持续遥测/断网恢复仍pending；这些离线结果不证明R/H或MVP通过。
