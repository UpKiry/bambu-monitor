# 登录设置向导接入验证

日期：2026-11-18。用户在确认可点击流程后授权“合适，接入吧”，后续授权自行处理本机依赖。实现范围限本仓库与项目独立环境；没有使用真实账号、系统密钥库条目或打印机连接，没有修改安装中的 Pet Manager、系统 profile 或固件。

## 本轮交付

- [设置模块](../src/bambu_monitor/settings.py)：普通 Config TOML 的受控 POSIX 读取、原子保存、外部修改检测及路径保护。
- [向导控制器](../src/bambu_monitor/setup_controller.py)：内存草稿登录、显式设备选择、确认提交、系统凭据迁移、恢复校验、切设备、取消、普通设置更新、采集和清理。
- [本地窗口](../src/bambu_monitor/setup_gui.py)：区域→密码/邮箱码→设备→保存→首页；首页启停、切机、重登、设置和本机退出登录。单 owner worker，结果和最新快照有界，主线程无HTTP/MQTT/密钥库调用。
- [CLI](../src/bambu_monitor/cli.py)新增setup；[双击入口](../start-monitor.command)选择本项目带Tk的环境。原CLI保留。

依赖处理先发现原开发Python缺少_tkinter，系统Python3.9虽有Tk但不满足项目版本；随后发现本机已安装的uv Python3.15.2自带Tk9，在仓库内创建`.venv-gui`并安装dev,cloud。没有安装/替换系统Python或写系统profile。该环境已加入忽略清单。

## 验证

执行环境：macOS arm64，独立Python3.15.2/Tk9，Paho2.1.0、keyring25.7.0。所有账号、令牌、设备和云响应为合成；native后端与真实网络均使用注入替身。

| 检查 | 结果与证据 |
| --- | --- |
| 设置测试 | 21项通过：[测试](../tests/test_settings.py)，含取消不写、原子提交、外部修改/替换/删除、符号链接/硬链接/特殊文件、协作锁和首次写入抢占 |
| 控制器测试 | 31项通过：[测试](../tests/test_setup_controller.py)，含草稿不持久、明确选择、迁移/回滚、保存结果不明、恢复时设备消失、同进程内存登录、未登录修改普通策略、停止HTTP边界和失败清理保留 |
| GUI与worker测试 | 5项通过：[测试](../tests/test_setup_gui.py)。显式BAMBU_TEST_GUI=1，真实Tk窗口、真实向导/设置模块接合成HTTP/凭据/MQTT；包含验证码分支、清空密码、保存、修改策略、取消切区、开始/停止和关闭。另检验worker单飞、最新值合并和有效0/旧值区分 |
| 完整工程check | 182项全部通过，无skip：[最终日志](../.build/setup-check.log)；生成一致性、Schema、PNG与18条原翻页路径通过 |
| build | 通过：[日志](../.build/setup-build.log)，仍是7个明确清单文件，不包含Python/窗口/配置/凭据 |
| 实际控件布局 | [检查日志](../.build/setup-layout.log)：780×700与620×600的6页共12种几何检查通过，128设备使用滚动列表，底部操作可达 |
| CLI入口与启动脚本 | setup --help、sh -n与可执行位通过；无账号输入 |
| 独立只读审阅 | settings代理审阅GUI接口、错误bool、停止和关闭；发现问题已修复并定向验证 |

运行：

```sh
BAMBU_TEST_GUI=1 .venv-gui/bin/python tools/project.py check
.venv-gui/bin/python tools/project.py build
```

默认无图形会话时，2项真实Tk测试明确skip，其他自动化仍运行；本轮最终本机检查显式启用，不能将CI默认skip记为窗口通过。早期M2/M3的PTY skip是历史记录，当前权限下最终check以实际执行结果为准。

## 审阅修复

认证、设备发现、保存、取消和退出登录的bool失败不再触发成功导航；发现失败不会误报没有绑定设备。停止请求后立即撤销当前状态并丢弃排队快照；采集失败/非零退出不显示正常运行。高级设置重启后无需登录也可保存，但不能绕过向导更改身份。清理失败保留Session引用并阻止新连接；关闭等待实际owner结束，不将仍在清理的工作称为已经退出。

保存替换后fsync或核验也可能失败，因此控制器重读结果：确认未提交才撤销新token；确认已写入则保留其登录引用并报告核验警告；结果不明保留可能已被文件引用的新token并阻止后续操作，提示关闭后检查，不能伪称原状态完全回滚。

## 尚未通过的层次

系统屏幕捕获报“could not create image from rect”，已终止无账号演示窗口，不重复截图或绕过权限。真实Tk初始化、交互和控件几何通过，但没有实际截图逐像素视觉验收。暗绿窗口遵循已确认的流程和信息层级，原HTML仍只是流程原型。

真实云密码/验证码、云API兼容性、系统密钥库实际get/set/delete、目标打印机首帧/30分钟/恢复仍待用户在本机窗口操作验证；不把合成测试等同这些层次通过。OS DNS/密钥库等阻塞没有强制总退出时限，关闭等待期间保持提示。安全设置保存仅POSIX，Windows尚不支持；Linux有代码路径但未做实际桌面验证。Pet Manager生产宿主、USB与设备显示仍按[M0](M0-HOST-AUDIT.md)pending。

组件ID、版本、三页与13指标未变；没有发布、导入、同步、提交/推送或操作打印任务。使用见[本地向导](SETUP-WIZARD.md)。
