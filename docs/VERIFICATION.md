# 验证状态与验收清单

最近功能验证：2026-11-18，提交 `d3f0cfe`。平台 macOS arm64 / Python3.15.2，Paho2.1.0、keyring25.7.0、pystray0.19.5、PyObjC13.0。**当前达到代码/离线集成、本机浏览器与原生托盘验证，真实云和设备端到端仍未通过。** 本文集中保存当前结论；早期单次执行、修复与选型历史可从Git追溯。

## 2026-11-18 设备上屏阻塞

当前同时成立的三条约束：**不修改 Pet Manager 宿主**、**设备不保证有 WiFi**、**无法提供公网 HTTPS 地址**。逐条核对现有通道后，设备端没有可用的实时数据路径：

| 通道 | 限制 | 结果 |
| --- | --- | --- |
| 宿主组成式 `json-get` / `pc-fetch` | 仅公网 HTTPS，拒绝 localhost 与私网 | 不可用 |
| 设备 `wifi.feed` | 需设备联网校时，且只能读公网 HTTPS | 不可用 |
| 宿主到设备的标量绑定 | 最多 8 个，来源仍受上述限制 | 17 项承载不了 |
| 直写 USB 串口 | 宿主独占，协议不公开 | 项目约定禁止抢占 |
| 新增宿主内置数据源 | 需要改宿主 | 为单个组件加 Bambu provider 属于组件专用分支，已否决 |

结论：三条约束同时成立时，设备端只能显示静态内容，实时监控仅在电脑端可用。组件保持 `bambu-monitor` 0.1.6 的静态占位布局；展示模型、三页与降级状态渲染已完成并通过 36 项测试，一旦出现**通用**本机数据通道（宿主开放，或设备可联网且存在公网端点）即可直接复用。

已排除的方案及原因：

- **专用宿主数据源**，包括更小的文件投递或回环 provider 变体：改动量可以压缩，但性质仍是给单个组件开分支，与项目的通用原语方向冲突。
- **把状态发布到公网或隧道**：私有遥测离开本机，且用户没有可用地址。
- **拆成多个组件套 8 变量配额**：来源限制不变，且会丢字段。

未执行的可选项：本机系统通知与托盘进度；把设备占位文案改为诚实说明（需重新打包并由用户在组件中心同步）。

## 2026-11-18 设备展示模型与三页离线前端

新增[展示模型](../src/bambu_monitor/display.py)与[渲染器](../tools/render_display.py)，把九项指标和 AMS 四槽类型/颜色整理成 17 个带质量的显示字段，并生成确认稿的三页与降级状态。统一 `python tools/project.py check` 为 **276项：272通过、4项条件跳过**，其中36项展示测试覆盖格式化边界、有效0与未知/不支持/旧值、AMS 按ID与多单元、材料截断、RGBA合成与描边、提示文案，以及18条布局路径的边界与翻页回环；翻页用例与 `package/buttons.json` 的六个动作互相对照。

`python tools/project.py display-preview` 用生产状态机加合成 `push_status` 渲染六个场景乘三页，人工查看到 `.build/display-preview/review.png`；交付图在 `design/display/`。任务四项需要任务身份才能有效，因此合成数据显式带 `task_id`，这与本机真实快照里任务字段曾为 stale 的规则一致。

组件包、ID、0.1.6 版本和13变量静态布局均未改动；没有打包、发布、导入或同步，也没有连接真实云或设备。上屏仍缺三项原语（动态颜色、质量文本、更宽的投影配额），见[架构](ARCHITECTURE.md)的“设备投影尚未接通”一节。

## 2026-11-18 本机遥测后台扩展

用户会话已反馈成功登录、“已收到目标遥测”，并提供7项有值但当时均为stale、2项unsupported的规范化读数。这是用户提供的真实采集局部证据，不代表独立验证认证全部分支、长期在线、AMS或设备上屏。

本轮工作区变更将后台 AMS 目标改为四槽类型/颜色，保留设备静态组件。统一 `python tools/project.py check`：240项，236通过、4条件跳过；五份JSON、18翻页路径、Schema通过。新增12项AMS材料测试与8项原始检查/Session测试，覆盖无UUID四槽、颜色/type身份变化、增量时间、质量撤销、未知协议字段、原始脱敏（含数字UID/敏感key/派生材料）、资源预算、换代/停止和CLI默认不输出报文；真实loopback合成链路额外覆盖材料/原始字段/诊断、GET刷新无MQTT写入及停止清空。

两组真实 Chromium + 合成数据均通过：`tests/browser_service_check.cjs` 验证生产服务的登录→保存→采集、材料/原始JSON展示、关闭网页继续采集、停止清空和退出登录；`tests/browser_dashboard_check.cjs` 验证全部9项/质量时间、AMS按ID四槽/缺槽/stale、RGBA色块、XSS/CSP、折叠/输入保留、刷新无写请求和手机宽度。运行截图 `.build/service-dashboard.png` 已人工查看，明确为合成数据。`node --check` 与 `git diff --check` 通过。

没有重启用户现有真实采集进程，没有重新登录或读取真实凭据，没有对真实打印机发送额外请求。新功能需退出旧托盘服务后重启，从托盘重新打开页面；当前登录若仅在内存将需重登。尚待用户实际验证四槽类型/颜色字段、更多固件字段和持续质量。刷新仅本机缓存读取，不是云端pushall。设备布局、打包、发布、USB与上屏均未改动。

## 已验证范围

| 层次 | 结果 | 可复现入口/证据范围 |
| --- | --- | --- |
| 统一项目检查 | 276项：272通过、4跳过 | `python tools/project.py check`；五份JSON一致、13变量、18翻页路径、展示模型36项、PNG、Schema和全部核心回归；TTY/Tk条件跳过不算通过 |
| HTTP/MQTT与数据核心 | 离线通过 | [HTTP](../tests/test_cloud_http.py)、[MQTT](../tests/test_cloud_mqtt.py)、[提供器](../tests/test_provider.py)、[遥测](../tests/test_telemetry.py)：真实实现注入传输、TLS参数、有界解析/队列、增量/身份、重连及只读白名单；不做真实云连接 |
| 凭据/设置/旧Tk | 合成通过，Tk曾在图形会话交互通过 | [采集](../tests/test_collector.py)、[设置](../tests/test_settings.py)、[控制器](../tests/test_setup_controller.py)、[Tk](../tests/test_setup_gui.py)；fake系统存储、私有输入失败路径、国区手机号/SMS、草稿/取消/冲突/清理，不替代真实认证或钥匙串 |
| HTTP服务与owner | 7项通过 | [本地服务](../tests/test_local_service.py)：真实loopback→生产控制器/认证/Session/遥测，合成HTTP/MQTT/内存存储；完整配置/采集/停止、有效0、阻塞期间状态响应、单owner、关闭门禁和旧发布拒绝 |
| 托盘/登录项适配 | 22项通过 | [macOS测试](../tests/test_macos_service.py)：fake Cocoa/pystray、临时plist及mock launchctl；主线程、计时器、signal、ready、venv路径、拒绝覆盖/外来文件、未知注册结果 |
| 后台启动协议 | 5项通过 | [启动测试](../tests/test_service_launcher.py)：pipe握手、mock进程、未就绪不报成功、未知结果不重复spawn、CLI组合校验 |
| 真实Chromium + 合成云 | 完整点击通过 | [浏览器脚本](../tests/browser_service_check.cjs)与[服务fixture](../tests/browser_service_fixture.py)：明确选设备、保存、轮询保留输入、有效0、关闭网页继续采集、再打开、停止清空遥测、换区域取消、退出登录；无JS/CSP错误 |
| 真实macOS托盘 + 合成状态 | 通过 | 主线程runloop覆盖等待→online→stopping，ready仅在首次可见后1次、关闭1次、signal恢复；不读真实配置/钥匙串 |
| 真实独立后台进程，未配置环境 | 通过 | workspace临时设置、`--no-auto-collect --no-browser`、空闲loopback端口；原生ready、HTTP200、对自己创建的子PID发SIGTERM后退出码0；未安装登录项 |
| 组件与Python资源构建 | 通过 | `build`恰7项；隔离wheel包含HTML/JS/CSS与服务模块；ID/版本/正式UI未改变。既有官方pack/validate曾通过，不代表新云功能已进入设备包 |

前述220项是功能提交时的记录；改代码后应重跑并记录实际结果。示例与测试输入为合成或明确脱敏数据，不能据 `input_class=live-cloud`、Schema通过或broker连接推断打印机已在线。

## 复现

```sh
python tools/project.py check
python tools/project.py build
node --check src/bambu_monitor/webui/app.js
```

真实浏览器、Tk和可选官方按键对比的准备见[开发指南](DEVELOPMENT.md)。截图/日志默认放 `.build/`，不要求新clone拥有作者电脑的临时文件，也不打入组件。真实托盘需macOS GUI会话，Linux CI与fake Cocoa不能替代原生验证。

## 真实云与系统验收

| 待验收 | 最小步骤与通过条件 |
| --- | --- |
| 云账号/区域/认证 | 用户在本机私有入口输入；分别记录区域、账号类型与实际认证分支；验证码送达、错误/过期、未支持MFA、限流和重登可诊断；不记录秘密或原始响应 |
| 系统钥匙串 | 实际get/set/delete、重启恢复、权限拒绝/不可用内存回退；退出保留令牌、退出登录确实删除、失败不报成功 |
| 设备发现与切换 | 多台/重名/重排按ID选择，设备消失不跳到另一台；切账号/区域/设备后旧结果不能污染新会话 |
| 云MQTT与固件profile | 验TLS/认证/SUBACK/目标首帧；确认目标机型/固件、13字段实际路径/单位/缺测、任务/槽身份、完整基线及遗漏语义；候选profile不能当实机兼容 |
| 长时与恢复 | 建议至少30分钟及一次受控网络中断/恢复；仅自然观察任务变化，不启动/暂停/加热造事件；认证拒绝停止重试，超时不谎称设备断电 |
| launchd与睡眠 | 实际安装、GUI重登录、自动恢复采集、退出/卸载、睡眠唤醒；mock安装测试不替代它们，签名.app/自动升级仍未实现 |
| 资源和时延 | 实测阻塞网络/钥匙串退出与长时内存；工程目标接收到快照P95≤1秒、宿主快照到可见≤2秒、liveness变化≤5秒，均需标负载/平台；不含云送达SLA |

DNS、HTTP库内部调用和系统钥匙串目前没有硬总deadline；停止后5秒仍有owner存活必须报清理未完成，不能假报回收成功。

## 宿主与设备验收

生产Pet Manager/固件源码、commit、目标能力报告仍未取得。当前本机PetUI0.1.232的8项限制和静态预算见[架构](ARCHITECTURE.md)，不是目标设备能力证明。

1. 只读追踪受控提供器/IPC、实例/epoch/序号、serializer与传输/接收预算；需要外部改动先列文件与授权范围。
2. 经生产路径用明确标记的合成快照验证单标量，再扩至13项及质量：null/0、退出/重开、换实例、迟到/乱序/超大帧、断连失效。
3. 审阅设备UI质量状态预览和预算；不默减指标、不借提高声明配额伪造支持，不用重打包每条遥测或抢占串口实现更新。
4. 目标设备实测USB、三页/按键、质量/身份、重连与可见时延，再与真实云组合验收。

完成上述适用项后才能称完整MVP通过。打包、导入、同步、发布各自记录并按明确指令执行；任何一层通过都不自动代表下一层通过。
