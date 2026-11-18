# 云采集器使用与检查

本轮在M1基础上实现了可发起真实Bambu Cloud HTTP和云MQTT连接的PC采集器、私有终端登录、系统凭据库及前台恢复调度。**代码和离线传输测试已交付，尚未使用真实账号登录或订阅打印机。** 这是一份可供本机联调的候选实现，不表示云采集、目标固件兼容或PetUI上屏已经验收通过。

## 它现在能做什么

在用户自己启动命令后，按明确区域登录 → 查询UID和绑定设备 → 明确选定一台 → 订阅云MQTT → 经既有解析/质量核心输出PC快照。没有默认后台服务，不自动安装开机任务，不访问USB或打印机LAN，不提供打印控制或任意publish，不会更新桌面设备屏幕。屏幕接入仍等待[M0生产源码/能力](M0-HOST-AUDIT.md)。

## 图形引导入口

首次使用可直接运行 `python -m bambu_monitor setup`，按区域、登录、设备和保存四步进入监控首页，无需手写配置。需要带 Tk 的 Python 和 cloud 依赖；本机已有 `.venv-gui` 环境及根目录双击启动器。完整步骤、保存位置、平台和安全边界见[本地向导](SETUP-WIZARD.md)。以下保留适合自动化/NDJSON导出的 CLI 使用方法。

## 安装和首次启动

Python 3.11+；当前本机验证是macOS arm64 / Python 3.15.2。终端私有输入入口目前仅实现POSIX控制终端；Windows原生输入入口待实现，不能从密钥库后端代码推断整个Windows采集器已可用。

```sh
.venv-dev/bin/python -m pip install -e '.[dev,cloud]'
.venv-dev/bin/python -m bambu_monitor --help
.venv-dev/bin/python tools/project.py check
```

新环境按[开发指南](DEVELOPMENT.md)创建venv；之后可用该venv的python替代上述路径。HTTP只用标准库；云extra安装固定`paho-mqtt==2.1.0`及`keyring>=25.7,<26`。Paho使用有界私有hook，升级必须重验，不能直接扩大版本范围。

准备一份自己的非秘密配置，参考[样例](../examples/provider.example.toml)：

```toml
[provider]
kind = "bambu-cloud"
region = "cn"  # 根据账号明确选择cn或global；样例不是自动判断结果
printer_id = "" # 空值运行时询问；填写明确选定ID则每次重新检查其仍在绑定列表

[credentials]
reference = "bambu-monitor/cloud/default"

[freshness]
liveness_seconds = 60

[reconnect]
initial_delay_seconds = 1
max_delay_seconds = 60
jitter = true
```

配置只保存区域、设备ID、逻辑凭据引用和时间策略。不要加入账号、密码、token、验证码或Access Code。区域/逻辑账户引用组成独立系统凭据槽；更换账号时使用新的reference，或显式重新login替换同一槽，不按位置或列表第一项猜账号/设备。

然后在你自己的终端执行（`your-provider.toml`代表你保存的配置路径）：

```sh
.venv-dev/bin/python -m bambu_monitor collect your-provider.toml --duration 60
```

未保存登录时会通过控制终端隐藏输入账号和密码（国区支持 11 位手机号或邮箱，全球区支持邮箱）；若要求验证码，提供“已收到／发送一次／取消”的明确选项，不自动反复发码。没有预选设备时，即使只有一台也要求输入编号；列表仅显示已知机型和ID尾号，未知设备名称原文不进入输出。选择只用于本次运行，不自动改配置。

运行期间stdout逐行输出：

```text
{"input_class":"live-cloud","snapshot":{...},"diagnostics":{...}}
```

`live-cloud`只标明正在使用真实网络入口，**不是“已连接/已在线/已验证”的结论**；以snapshot的connection/quality及diagnostics判断。连接中、无数据、超时和认证失败也会有各自状态。提示走控制终端，诊断走stderr，stdout不混入登录文字；导出是规范化数据，没有原始账户响应/原始遥测。

不指定duration会前台持续运行。Ctrl+C或SIGTERM停止，执行本地清理，不派发新的订阅/初始化。每次owner tick约100ms、快照最多4/s；云消息频率不由本程序保证。

## 登录、会话模式和退出登录

可以先单独登录，再启动采集：

```sh
.venv-dev/bin/python -m bambu_monitor login your-provider.toml
.venv-dev/bin/python -m bambu_monitor collect your-provider.toml
```

默认优先使用明确的原生系统后端：macOS Keychain、Windows Credential Locker、Linux Secret Service；不自动采用第三方明文/文件/云端keyring backend。正常退出保留系统token供下次恢复，恢复后通过preference API核实，不拆token假设JWT，也不依赖可靠refresh。凭据失效停止并返回auth_required，不在后台重登。

系统密钥库不可用时会明确提示“仅本次内存，重启需重登”。在这种情况下，单独login后退出不会给下一次collect保留token，应直接collect，在同一进程完成登录与采集。也可主动选择：

```sh
.venv-dev/bin/python -m bambu_monitor collect your-provider.toml --session-only
```

邮箱验证码登录可显式选用：

```sh
.venv-dev/bin/python -m bambu_monitor collect your-provider.toml --login-method email-code
```

当前实现**国区手机号密码／短信验证码，以及邮箱密码／邮箱验证码**。图形窗口按账号类型选择发码渠道；CLI 短信验证码登录使用 `--login-method sms-code`，邮箱使用 `--login-method email-code`。国区手机号目前接受不带 `+86` 的 11 位大陆号码，不代表支持其他国家号码。网站CSRF/TOTP分支尚不支持；遇`tfa`明确报告mfa_unsupported。HTTP400验证码错误与过期暂合并为拒绝诊断，未读取错误正文来细分。不要将上游其他账号分支的成功当成本实现已覆盖。

移除当前区域/逻辑账户槽中的本地token：

```sh
.venv-dev/bin/python -m bambu_monitor logout your-provider.toml
```

这只移除本机对应凭据，不调用云端账号退出/全局撤销，不影响其他账户槽。密钥库不可用或删除失败会报错，不宣称已删除。不会把密码或token作为CLI参数、环境变量或输出内容。

退出码：0为本次命令正常结束（包含定时结束，不代表收到过有效目标遥测）；2为配置/交互/本地操作错误；3为认证或设备发现阶段无法继续；交互登录期间Ctrl+C为130。机器输出可能在故障前已有部分记录，消费者也须检查进程退出状态。

## 数据有效性的当前限制

`legacy-cloud-v1`仍是社区旧字段候选，真实区域/机型/固件未验证。真实采集默认不接受离线empty/removed标记、不人工标记完整基线；`baseline_auto_detection=false`。当前代明确字段可单独有效；随后缺席字段在没有完整性/遗漏依据时会保守stale，不把首个delta、SUBACK或已发送pushall当完整基线。AMS余量还要求本代可确认UUID，不能用材料类别代替耗材身份。具体字段/作用域见[协议profile](PROTOCOL-PROFILES.md)。

第一轮真实联调需要确认目标机型/固件、实际字段及完整基线/遗漏语义，再扩展可持续有效性；不能为了让界面数字好看伪造时间或持续有效。

## 可靠性与尚未验收项

HTTP固定两个区域和五个必要路由（短信仅限国区固定网站入口），TLS正常验证，不跟随重定向/环境代理，不读非200正文；成功响应有字节、层级和类型限制。MQTT固定云broker/TLS8883/report订阅，仅允许get_version/pushall初始化，跨代至少300秒限频，拒绝retained旧缓存、其他设备和超大包。会话与运行器共同处理generation、SUBACK、握手期限、网络退避和目标遥测超时；认证拒绝停止恢复。正常关闭与凭据撤销分开。

当前不创建后台网络线程；网络I/O由前台owner调度。已公开的HTTP socket可shutdown取消，但**系统DNS、系统密钥库、HTTP库内部行读取和多地址连接不具备强制总deadline**。参数级timeout和fake清理通过不证明任何阻塞环境都能5秒退出。真实超时/退出预算保留pending，不启动隐蔽线程伪称资源已回收。

真实云账号权限、当前云防护/请求兼容、TLS/首帧/连续30分钟/断网恢复、系统密钥库实际读写、真实终端隐藏输入、固件字段、宿主/USB/HIL均尚需验证。当前沙箱禁止创建伪终端，对应自动测试明确skip；没有为此要求扩大权限或假报通过。

本次手机号修复与离线验证见[手机号登录验证](PHONE-LOGIN-VERIFICATION.md)。真实短信发送和云登录仍待用户本机验证。

公开研究与许可见[HTTP研究](HTTP-RESEARCH.md)、[MQTT研究](MQTT-RESEARCH.md)及[参考对照](CLOUD-REFERENCES.md)；本轮自动化证据与修复见[验证记录](M2-M3-VERIFICATION.md)。
