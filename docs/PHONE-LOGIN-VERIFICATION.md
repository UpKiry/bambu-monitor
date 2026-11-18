# 国区手机号登录修复与验证

日期：2026-11-18（Asia/Shanghai）。用户指出国区使用手机号登录，原实现仅提示和接受邮箱。本次修复本仓库的账号输入、认证、HTTP及CLI，保留邮箱登录；没有使用真实账号、发送真实短信、读取系统凭据或连接打印机。

## 修复范围

- [账号格式](../src/bambu_monitor/accounts.py)：cn接受大陆11位手机号或邮箱，global仍接受邮箱。11位、无`+86`是本项目当前支持范围，不是已证明的云端完整规则。
- [HTTP](../src/bambu_monitor/cloud_http.py)：手机号password/code与邮箱使用同一login接口，字段互斥；SMS仅允许国区固定网站URL `https://bambulab.cn/api/v1/user-service/user/sendsmscode`，JSON为`phone`＋`type=codeLogin`。空200发码响应可确认请求，无跨域回退或自动重试。
- [认证](../src/bambu_monitor/auth.py)：手机号的verifyCode挑战标记sms_code，显式发码按账号类型选择短信／邮件，不自动发送。
- [窗口](../src/bambu_monitor/setup_gui.py)与[控制器](../src/bambu_monitor/setup_controller.py)：国区显示“手机号或邮箱”，通用验证码入口；切换密码／验证码保留账号、清空密码，切换区域清空账号；发码提示短信／邮件。
- [终端交互](../src/bambu_monitor/interaction.py)与[CLI](../src/bambu_monitor/cli.py)：新增`--login-method sms-code`，保留password/email-code；password挑战后的显式发码同样按账号分流。

账号、密码、验证码不进入普通设置、日志或组件包；新增测试号码与密码均为合成数据，传输均为注入假连接。设备组件ID、版本、三页布局和13指标没有修改。

## 协议依据

重新读取项目已固定的ha-bambulab提交 `e3c73139d443e9183ed2f804be30d2f2e53bbe66`：

- [bambu_cloud.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py)：password/code提交原样account，发码按账号含不含`@`分流；SMS体为phone＋codeLogin。
- [const.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/const.py)：SMS_CODE精确指向国区网站域名及`/api/v1`前缀。
- [utils.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/utils.py)：China只将`.com`替换为`.cn`，已为.cn的SMS URL保持不变。
- 官方[验证码登录](https://bambulab.cn/zh-cn/sign-in)与[密码登录](https://bambulab.cn/zh-cn/password-sign-in)公开页面确认国区有手机号登录入口；网页展示不证明实际API在本项目已可用。

上述资源由本轮主代理／只读研究代理实读，固定社区源码沿用此前MIT参考；独立实现必要接口，没有导入其客户端或浏览器模拟。其他公开Gist采用不同的api子域名，本次优先固定社区源码，不并列尝试或自动回退。

## 检查结果

| 检查 | 结果 | 覆盖／证据 |
| --- | --- | --- |
| `.venv-dev/bin/python tools/project.py check` | exit 0；186项，182 passed、4 skipped | [最终日志](../.build/phone-login-check.log)；3项Tk需显式开启，1项PTY受沙箱限制；JSON生成一致、18条翻页路径、Schema均通过 |
| `BAMBU_TEST_GUI=1 .venv-gui/bin/python -m unittest discover -s tests -p test_setup_gui.py -v` | exit 0；6/6 passed | [最终Tk日志](../.build/phone-login-tk.log)；实际Tk控件，合成HTTP；国区手机号、短信分流、模式切换保留、跨区域清空，既有设置及采集启停回归 |
| HTTP/认证新增回归 | passed，包含在统一检查 | [HTTP测试](../tests/test_cloud_http.py)：真实生产适配器的password挑战→手动SMS→code登录、三个精确host、无自动发码、非法格式/全球区/错渠道拒绝 |
| 私有终端新增回归 | passed，包含在统一检查 | [交互测试](../tests/test_collector.py)：显式SMS、取消零请求、错误区域及账号拒绝、密码挑战分流 |
| 只读独立审查 | 无阻断问题 | 核对域名／路径、区域隔离、verifyCode、GUI与CLI；修正文案把11位格式表述为本地支持范围 |
| `git diff --check` | exit 0 | 工作区包含此前未提交实现；本次未提交、推送、打包、发布或同步 |

首轮回归曾发现抽取发码共用函数后仍使用旧email路径，修复后全部通过。Tk首轮虽exit 0但有跨测试垃圾回收导致Tcl析构线程警告；测试在主线程tearDown回收已销毁窗口的循环引用，最终6项无警告。未改生产线程模型。

## 仍待验证

国区真实手机号密码／短信送达／验证码登录、网站入口当前防护兼容、带国家码的输入格式、邮箱真实回归、系统密钥库与云MQTT均未在本轮实测。离线与Tk通过不表示云端登录成功；仍按[验收矩阵](ACCEPTANCE.md)保留R/H层pending。
