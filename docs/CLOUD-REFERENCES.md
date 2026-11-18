# 公开 Bambu Cloud 项目参考对照

核查日期：2026-11-18。用户允许参考网上相关项目，在方案A下独立实现必要协议；没有将整个HA/pybambu或其他SDK引入为依赖。读取公开资料不等于这些协议在用户账号/机型上已验证。HTTP/MQTT逐项源码依据与许可分别见[HTTP研究](HTTP-RESEARCH.md)、[MQTT研究](MQTT-RESEARCH.md)。

| 项目 | 固定版本 / 实读资料 | 本项目如何使用 |
| --- | --- | --- |
| ha-bambulab | `e3c73139d443e9183ed2f804be30d2f2e53bbe66`；[cloud实现](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)、[MQTT实现](https://github.com/greghesp/ha-bambulab/blob/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_client.py)、const/utils/commands与MIT许可 | 必要登录/code分支、区域、UID、云TLS与report/request、初始化字段的主要社区源码证据；独立精简实现，没有复制HA的模型/线程/相机/控制/日志策略 |
| OpenBambuAPI | `cc383a2c96576a9f53391c879acfb8dca5c534e4`；[HTTP](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/cloud-http.md)、[MQTT](https://github.com/Doridian/OpenBambuAPI/blob/cc383a2c96576a9f53391c879acfb8dca5c534e4/mqtt.md)，GFDL1.3 | 交叉核对路由、topic和refresh不可靠等事实，不整体复制文档/样例，不依赖永久自动续期 |
| coelacant1/Bambu-Lab-Cloud-API | `29458fdd5b7ef7f8f71781aadccfb917c3ebed45`；[README](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/README.md)、[认证说明](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/API_AUTHENTICATION.md)、[MQTT说明](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/API_MQTT.md)、[LICENSE](https://github.com/coelacant1/Bambu-Lab-Cloud-API/blob/29458fdd5b7ef7f8f71781aadccfb917c3ebed45/LICENSE) | 额外项目交叉阅读，未导入/复制代码或测试样例。README称AGPL-v3，但固定LICENSE实为GPLv3正文，不能直接标MIT或假定许可一致；本项目不依赖其实现 |
| Eclipse Paho | v2.1.0固定commit `af64a4365c6ac5a7a4d339e7b00f44df91353b35`与官方wheel | 唯一MQTT运行依赖；EPL-2.0/EDL-1.0。核实手动loop、TLS超时、入站分配和清理；实际hook有离线真实Paho对象/内存socket回归 |
| keyring | [25.7.0稳定文档](https://keyring.readthedocs.io/en/stable/)及安装的25.7.0模块 | 系统凭据库API与原生后端范围；只选择明确原生后端，不自动加载可能明文的第三方存储。真实系统密钥库读写尚未执行 |

以上第三个项目的固定commit通过公开commits API取得；后续tree API遇到共享IP限流403，未使用账号或扩大访问权限。随后仅用明确文件URL读取公开README/认证/MQTT/许可（HTTP200），不将无法获取的树或未读实现称为已审阅。

## 交叉阅读后保留的判断

- 邮箱password/code登录与区域域名在多个来源相符，但不能据一份文档把所有账号都限定为同一MFA分支；当前已补齐国区手机号密码/code与SMS发码，见[修复与验证](PHONE-LOGIN-VERIFICATION.md)；网站TOTP仍不支持，各区域真实账号验证仍pending。
- 第三个项目说明中的“token总是JWT/典型24小时/固定限流/固定0.5–2秒推送”等没有在本项目获得真实证据，不写成保证。UID使用preference API，不解token猜用户ID；不实现固定期限假设或可靠refresh。
- 第三个项目的MQTT说明包含多套额外topic、凭据泛称以及不同量级的AMS余量样例；这些不作为本项目新增订阅、LAN Access Code云登录、余量单位或任务控制依据。继续使用主要固定社区源码交叉支持的云broker、`u_<uid>`、完整token及精确目标report/request。
- 其他项目的密码argv、token文件、token前缀打印、原始响应日志、自动重发码等做法不沿用。本项目秘密只经本机私有终端进入内存/系统凭据库，HTTP/MQTT异常使用固定类别。
- 各项目的“测试通过/已验证机型”只属于其自身版本与环境。本轮测试输出仍标合成/离线；用户真实云、固件、USB与显示验收单独记录。

参考是为了核实必要事实和发现边界，方案A、自研精简实现、只读范围、现有13项及M0宿主门禁均保持。
