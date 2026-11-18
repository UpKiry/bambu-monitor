# 后台采集服务验证记录

日期：2026-11-18；基点 `3ced3a1` 后本工作区变更。首发验证平台为 macOS arm64、Python 3.15.2、pystray 0.19.5、PyObjC 13.0、Paho 2.1.0、keyring 25.7.0。没有提交/推送、安装用户真实 LaunchAgent、读取真实云凭据或连接打印机；原组件 ID、版本、13 变量和全部正式资源保持。

## 完成范围与证据

| 层次 | 实际检查 | 结果 |
|---|---|---|
| 单元/合成集成 | [HTTP/运行器测试](../tests/test_local_service.py)：真实 loopback HTTP → 单 owner → 生产 SetupController/CloudAuth/Session/TelemetryState，注入合成 HTTP/MQTT/内存存储 | 7 项通过；完整配置、采集/停止、退出登录、有效0、停止期间拒绝旧发布、阻塞登录时状态仍响应、停止恢复时不启动MQTT、关闭不继续discover、异常清理与幂等 |
| 原生适配离线 | [托盘/登录项测试](../tests/test_macos_service.py)：fake Cocoa/pystray、临时 plist、mock launchctl | 22 项通过；主线程调用、颜色/文字状态、菜单启停、signal恢复、首次可见后ready一次、退出timer清理、venv路径保留、不覆盖已有文件、未知bootstrap结果、拒绝外来/符号链接/被替换文件 |
| 启动协议离线 | [后台启动测试](../tests/test_service_launcher.py)：pipe握手、mock进程与CLI | 5 项通过；未就绪不报成功、未知结果不再生成副本、独立进程会话、不相容CLI组合拒绝且不安装 |
| 真实浏览器/合成云 | [Chromium脚本](../tests/browser_service_check.cjs) + [合成服务](../tests/browser_service_fixture.py) | 完整点击通过；区域、密码登录、明确选设备、内存保存、设置持久化、轮询不覆盖未提交输入、有效0、关闭网页继续采集、再打开读取状态、停止清空遥测、换区域草稿取消、退出登录；无JS/CSP错误 |
| 真实 macOS 托盘/合成状态 | 真实 pystray/AppKit/Foundation 主线程runloop，模拟采集中→目标online→stopping，后台Timer仅设置退出Event | passed；实际状态采样覆盖三阶段，主线程关闭1次、signal恢复；ready回调仅在首次显示后1次；未读真实配置/钥匙串 |
| 真实独立后台进程/未配置环境 | `start_background` 启动真实CLI子进程；显式 `.build/` 临时设置、`--no-auto-collect --no-browser`、空闲loopback端口；读取HTTP首页后对自己创建的确切子PID发送SIGTERM并waitpid | passed；原生ready已收到、HTTP200、退出码0。未创建登录项或访问真实账号 |
| 项目统一回归 | `python tools/project.py check` | 220项，216 passed / 4 skipped（真实TTY/Tk可选检查）；生成JSON一致、13变量、18翻页路径、PNG、Schema及既有云核心回归通过 |
| 组件构建 | `python tools/project.py build` | 7个原有明确清单文件；未改变UI基线、导入、同步或发布 |
| Python发行资源 | 在 `.build/` 隔离staging执行pip wheel，核实归档含全部HTML/JS/CSS及服务模块 | passed；版本仍0.1.6；这是资源打包检查，不是签名macOS安装包或发布 |

最终全量日志：[check](../.build/local-service-check.log)、[build](../.build/local-service-build.log)、[浏览器结果](../.build/local-service-browser.json)。截图均加注“合成离线预览”，见[登录页](../.build/service-login.png)与[运行页](../.build/service-dashboard.png)。这些日志/预览为本机 `.build/` 产物，不进入组件包。

初轮实现发现并修复：请求线程直接接触控制器、停止/退出后保留旧online读数、轮询覆盖表单、托盘刷新未调度、递归timer、错误响应缺长度、重复构造器、后台启动未握手、关闭时追加设备发现、重复close改变已关闭状态。最终代码以永久单owner、脱离控制器的状态副本、显式停止/关闭门禁和主线程NSTimer为边界。独立只读审查后，补充拒绝 `--install-launch-agent --no-auto-collect`，避免明确禁用自动采集的参数被静默忽略。

## 复现

```sh
.venv-dev/bin/python tools/project.py check
.venv-dev/bin/python tools/project.py build
node --check src/bambu_monitor/webui/app.js

# 可选：真实浏览器点击，依赖只放在本仓库 .build/
npm install --prefix .build/browser-check --cache .build/npm-cache --no-audit --no-fund playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node .build/browser-check/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node tests/browser_service_check.cjs
```

浏览器脚本自行创建/关闭临时合成服务，不使用真实账号或系统密钥库。原生托盘运行要求 macOS GUI 会话，不能由fake测试或Linux CI替代。启动使用见[服务说明](LOCAL-SERVICE.md)。

## 保留待验收项

- 真实 Bambu 账号各区域/认证分支、邮件/短信发送、系统钥匙串真实读写、目标固件字段与长时断网恢复仍 pending；本轮沿用自研云适配器和候选profile，未宣称云阶段或完整MVP通过。
- 用户真实 LaunchAgent 安装、重登录自动恢复、睡眠/唤醒、签名/打包/升级待验证。实现及mock测试不替代这些宿主环境证据。
- DNS、HTTP内部或系统钥匙串阻塞不具备硬5秒退出保证；清理失败不会宣称已回收。网络调用阻塞时退出请求先撤下当前状态，owner仍需等待实际调用结束。
- Pet Manager生产宿主、USB、13变量实时投影与设备HIL沿用[M0审计](M0-HOST-AUDIT.md)的缺口；本地Web管理API不等于设备宿主已经能消费快照。
