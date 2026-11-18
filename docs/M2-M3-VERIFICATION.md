# M2/M3 云采集代码与离线验收记录

日期：2026-11-18（Asia/Shanghai）。在同一未提交工作区继续方案A，基点仍是 `b4b237ab821d6e934adacbd13923773985b1dadb`，包含此前M0/M1变更。用户明确授权“按你的想法做”，随后要求多参考公开Bambu云API项目、做好后交检查。没有提交/推送、发布/导入/同步，没有改UI、组件ID或版本，没有真实账号或打印任务操作。

**当前等级：M2/M3实现候选，真实HTTP/MQTT代码、私有TTY/凭据库和前台runner已实现，离线验证通过；真实云采集器阶段仍pending。** 不是M2–M3真实云或完整MVP通过。使用见[云采集器](CLOUD-COLLECTOR.md)，参考版本与取舍见[公开对照](CLOUD-REFERENCES.md)、[HTTP研究](HTTP-RESEARCH.md)、[MQTT研究](MQTT-RESEARCH.md)。M0生产宿主与HIL缺口仍按[审计](M0-HOST-AUDIT.md)处理。

## 实际实现

| 模块 | 可执行行为 | 边界 |
| --- | --- | --- |
| [cloud_http.py](../src/bambu_monitor/cloud_http.py) | 标准库直接HTTPS、明确cn/global及四路由、TLS验证、有界成功JSON与必要投影、终止close/取消active socket | 不redirect/proxy，不读非200正文；真实防护/账号兼容未知；系统DNS与库内部行读取非硬deadline |
| [cloud_mqtt.py](../src/bambu_monitor/cloud_mqtt.py) | 固定Paho2.1.0、TLS8883、owner手动pump、MID/SUBACK、精确两条读初始化、超大包前置拒绝、旧client隔离及失败资源保留 | 无自动网络线程/重连；socket/TLS逐操作≤2s不等于DNS/多地址总2s；真实broker未连接 |
| [credentials.py](../src/bambu_monitor/credentials.py) | 明确原生系统后端、region+reference哈希槽、正常close保留token、401/logout撤销、不可用会话内存 | 未调用用户系统密钥库实际get/set/delete；拒绝自动第三方明文backend；系统服务阻塞预算未实测 |
| [interaction.py](../src/bambu_monitor/interaction.py) | 控制TTY隐藏输入、禁止echo fallback、密码/邮箱code、明确发码/选择/取消、设备脱敏列表 | POSIX入口；Windows私有控制台、SMS、网站CSRF/TOTP未实现；真实控制TTY测试受沙箱限制 |
| [auth.py](../src/bambu_monitor/auth.py) | opaque token、preference获取UID、持久token恢复后验证、过期/401语义、HTTP epoch隔离、白名单机型回退、旧设备列表失效 | 不假设JWT，不可靠refresh；验证码过期/错误正文未细分；名称原文不导出 |
| [collector.py](../src/bambu_monitor/collector.py)与[CLI](../src/bambu_monitor/cli.py) | login/collect/logout、依赖preflight、前台tick、订阅期限、网络恢复、目标超时、NDJSON、duration/信号停止与清理失败 | 无后台服务/宿主IPC/USB；真实入口baseline=False、测试删除标记关闭；真实固件有效性依据仍待确认 |

## 环境与执行证据

本机macOS arm64，Python 3.15.2，Paho MQTT 2.1.0、keyring 25.7.0，Pillow 12.3.0、jsonschema 4.26.0、rfc3339-validator 0.1.4。cloud extra已安装到工程venv。CI配置改为安装dev+cloud，以覆盖固定Paho对象回归；远程CI并未执行或读取，其他Python/OS支持不因矩阵配置自动通过。

| 检查 | 结果 / 退出码 | 证据 |
| --- | --- | --- |
| editable dev+cloud安装 | passed / 0 | 本机依赖安装，无用户登录/凭据读取；没有改系统Python或profile |
| 完整统一check | passed / 0 | [最终check日志](../.build/m2-m3-check.log)：125项，124 passed、1 skipped；全部原有生成/PNG/Schema/18翻页路径通过 |
| HTTP独立测试 | passed / 19项 | [测试](../tests/test_cloud_http.py)：真实HTTP实现接注入connection/socket/clock，包含字节/深度/重定向/类型/取消/例外canary等 |
| MQTT独立测试 | passed / 17项 | [测试](../tests/test_cloud_mqtt.py)：真实Paho构造/内存socket parser、TLS hook、MID、只读命令、旧代和永久清理失败不可绕过 |
| 凭据/交互/runner测试 | 26 passed、1 skipped | [测试](../tests/test_collector.py)：模拟native store、HTTP与泵驱动Session共同核心；真实PTY分配被沙箱EPERM拒绝，明确skip，不改权限 |
| M1与Schema回归 | passed / 62项 | [提供器](../tests/test_provider.py)、[遥测](../tests/test_telemetry.py)、[Schema](../tests/test_snapshot_contract.py) |
| CLI --help与collect --help | passed / 0 | 仅解析/展示入口，无账号、网络或系统凭据访问 |
| 原有build | passed / 0 | [build日志](../.build/m2-m3-build.log)；7文件清单，原UI/13变量/ID/版本/素材保持 |
| 补丁/文档链接/输出审查 | 见[最终审查](../.build/m2-m3-final-review.json) | 检查非秘密配置、正式资源未改、本地链接与测试计数；源码哈希汇总命令被安全网按 `secret.basename.credentials` 拒绝，未执行、未重试或绕过；没有打包发布 |

所有HTTP/MQTT测试输入均为合成；注入边界仍运行实际适配器、认证、Session及TelemetryState，没有另写一套“测试成功”解析路径。Paho测试不做DNS/TLS真实连接；标准库真实socket仅限内存fake或已构造的本地socketpair。没有从已有系统凭据库自动取token来试连。

PTY测试先真实执行并因沙箱拒绝而失败；保留测试并针对PermissionError明确skip，未删除断言、未降权限绕过、未将其称为通过。一般getpass/无TTY/echo-warning安全路径有独立可执行测试，仍不替代用户本机真实隐藏输入检查。

## 对需求/验收的增量覆盖

| 对应需求 / 用例 | 本轮D/U/F状态 | R/H状态与限制 |
| --- | --- | --- |
| FR-02 / AC-02 | passed：区域/槽/设备明确，绑定列表重排/失效，restore失败撤销旧内存scope | 实际账号切换与持久设置实测pending |
| FR-03 / AC-03、NFR-01 / AC-N01 | passed（已实现子集）：邮箱password/code、一次明确发码、200无success、401/403/429、native store注入/内存回退、私有输入安全错误、UID、关闭/epoch | 真实邮箱分支、原生密钥库读写、真实TTY pending；SMS/网站MFA未实现；非200验证码正文错误/过期未细分 |
| FR-04 / AC-04 | passed：ID选择、不默认第一台、旧列表不复用、已知product/model回退、AccessCode/任意名称剔除 | 用户实际绑定列表、目标机型/固件pending |
| FR-05 / AC-05 | passed：真实MQTT适配/TLS参数、固定broker/topic、SUBACK MID/码、出站精确结构、QoS/去重/限频、retained拒绝 | 真实云TLS、认证权限、SUBACK/首帧、初始化响应pending |
| FR-06～08 / AC-06～08 | 原M1回归passed；真实适配器可进入相同核心；没有制造baseline或变更单位以适应未经验证数据 | 候选profile真实遗漏语义、完整基线自动判据、空槽/删除/新固件字段pending |
| FR-09 / AC-09 | passed：HTTP固定类别、认证/权限/限流、网络断开、订阅期限、目标无证据超时、清理失败分别可诊断 | 真实故障/宿主断线分离pending，尚无宿主适配 |
| FR-11 / AC-11、NFR-03 / AC-N03 | passed（可控范围）：owner恢复/有界队列、明确timer、Ctrl+C/SIGTERM机制、stop后禁新初始化/输出、清理失败保留资源/阻止重连 | 系统DNS/原生密钥库/HTTP内部行读取无法强取消，任意网络环境5秒退出目标未通过；真实长时压力/断网恢复pending |
| FR-12 / AC-12、NFR-05 / AC-N05 | passed：离线回放与真实传输共用核心，机器输出/诊断分离，固定参考、许可、可追踪测试 | 已脱敏真实fixture尚无，其他平台/远程CI/R/H均pending |
| NFR-02 / AC-N02 | 可控时钟runner tick覆盖无数据超时与状态变化，不靠外部消息触发 | 真实P95采集时延、云延迟、USB和可见更新pending；此前M1微基准不自动替代本轮网络性能 |
| FR-01/10 / AC-01/10 | 本轮不改M0结论或UI；13变量不减项 | 生产宿主源码、真实能力、投影/预览、USB/HIL pending |

## 审查发现与修复

两名独立实现代理对lead的认证/凭据/交互/运行代码作只读交叉审查；lead读取真实HTTP/MQTT实现并集成、运行全套回归。明确修复并增加回归：

- 邮箱发码成功不能强制要求success=true，兼容参考协议HTTP200确认。
- Paho connect_async不能靠手动loop驱动首次连接；TLS握手原本使用keepalive30s，独立hook保留证书/SNI并设置≤2s；大包须在库分配前拒绝，不能只在callback判断。
- HTTP正常关闭与凭据撤销分开，持久token不会每次退出被删除；旧HTTP结果、失败restore和旧设备列表不能继续使用旧认证scope。
- MQTT第一次close失败必须保留client资源供后续清理，第二次close不能假装成功或允许新连接绕过；preflight先检测缺依赖/错误版本，避免网络恢复循环吞掉安装问题。
- pump期间到达停止请求后，不能继续drain发送订阅/初始化；两条初始化之间也检查停止，auth_required输出分支同样检查。
- 顶层必须观察auth.close/HTTP及存储关闭失败，不能返回正常成功；已知product name优先回退并统一X1 Carbon别名，未知字符串不导出。

## 尚需用户检查

可先审阅[运行说明](CLOUD-COLLECTOR.md)及源码/测试，再在自己的POSIX终端安装cloud extra、明确账号区域并运行60秒候选采集。秘密只在本机隐藏输入，当前会话不收集密码/token。首轮R记录需账号分支、区域、机型/固件、实际字段/质量、首次目标遥测和自然任务变化；建议连续30分钟及一次受控网络中断/恢复。不能擅自开始/暂停/加热打印来造验收事件。

上屏仍需Pet Manager/固件生产工作树与commit、设备实际能力报告及后续外部修改范围授权。当前没有发布、库导入、同步或实机状态UI，不能将终端输出误称屏幕已更新。
