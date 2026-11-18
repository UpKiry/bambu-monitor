# Bambu Cloud HTTP 固定参考与实现边界

核查与实现日期：2026-11-18（Asia/Shanghai）。范围为 M2 HTTP 适配器；实现见 [cloud_http.py](../src/bambu_monitor/cloud_http.py)，无账号测试见 [test_cloud_http.py](../tests/test_cloud_http.py)。本次只访问下面的公开 GitHub 固定版本资源，**没有登录真实账号、调用 Bambu Cloud HTTP 实际网络、发送真实验证码、连接 MQTT 或操作打印任务**。离线脚本连接对象测试并不等于云 HTTP、账号分支或机型实测通过。

## 实际读取的固定参考

所有下列链接均于本次通过 `web_fetch` 实际读取（HTTP 200）；没有用搜索摘要替代全文，也未执行其中代码或依赖其原始日志策略。

| 来源 / 固定 SHA | 实际读取的资源 | 用途与许可 |
| --- | --- | --- |
| ha-bambulab `e3c73139d443e9183ed2f804be30d2f2e53bbe66` | [bambu_cloud.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/bambu_cloud.py) | 密码/code互斥、verifyCode/tfa响应、Bearer鉴权、绑定设备及UID；[LICENSE](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/LICENSE) 实读为MIT，版权ha-bambulab contributors |
| 同一 ha-bambulab SHA | [const.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/const.py)、[utils.py](https://raw.githubusercontent.com/greghesp/ha-bambulab/e3c73139d443e9183ed2f804be30d2f2e53bbe66/custom_components/bambu_lab/pybambu/utils.py) | 五路由常量（含SMS）、China将HTTP `.com`切换到`.cn`的事实；不复制整套云客户端、HA模型、浏览器模拟或控制代码 |
| OpenBambuAPI `cc383a2c96576a9f53391c879acfb8dca5c534e4` | [cloud-http.md](https://raw.githubusercontent.com/Doridian/OpenBambuAPI/cc383a2c96576a9f53391c879acfb8dca5c534e4/cloud-http.md) | 登录/GET体/设备/UID语义、refresh不可靠警告；[LICENSE.md](https://raw.githubusercontent.com/Doridian/OpenBambuAPI/cc383a2c96576a9f53391c879acfb8dca5c534e4/LICENSE.md) 实读为GFDL1.3，不误标MIT |

这里只归纳实现需要的接口事实并链接证据，不复制大段代码、文档或上游测试样例；适配器与本地测试为独立实现，不新增HA/pybambu/requests/cloudscraper依赖。上游是社区复现的私有协议，不是稳定官方API，也不保证本项目User-Agent或请求头被当前云接受。不会冒称Bambu Studio/HA身份，遇403保留为权限/防护类状态，由上层诊断，不自动换域或模拟浏览器绕过。

## 首版白名单与最小出口

构造必须明确 `cn/global`，HTTP分别固定为 `https://api.bambulab.cn` / `https://api.bambulab.com`。实例不能跨区调用；不猜区域，不回退其他域或LAN。

| 请求 | 固定路径 | 首版输入 / 返回给私有认证层的内容 |
| --- | --- | --- |
| POST | `/v1/user-service/user/login` | account＋password（可带空apiError），或account＋code，严格互斥；cn接受大陆11位手机号或邮箱，global仅邮箱；只留accessToken、已知loginType、有效expiresIn |
| POST | `/v1/user-service/user/sendemail/code` | email＋固定type=codeLogin；允许200空body，或只留success布尔、白名单message=success、有限整数code；不会自动重发 |
| POST（仅cn网站） | `https://bambulab.cn/api/v1/user-service/user/sendsmscode` | phone＋固定type=codeLogin；允许200空body，与邮件发码相同的最小响应投影；显式发送一次 |
| GET | `/v1/iot-service/api/user/bind` | 必须Secret token，空请求体；只留唯一dev_id和known model白名单；不返回dev_access_code、任意设备名、完整账户资料或摘要 |
| GET | `/v1/design-user-service/my/preference` | 必须Secret token，空请求体；只留1–32位数字uid并统一成string，不拆opaque token猜UID |

known model字段只接受 `A1/A1 mini/P1P/P1S/X1/X1C/X1E/X1 Carbon` 的明确字符串；未知原文不透传。仅识别名称不代表机型遥测已验证。设备最多128台，重复ID、非法ID或非列表整批拒绝，不能默认第一台；实际选择仍由认证层完成。

网站MFA/TOTP/CSRF、refresh、任务记录、文件/相机、任意上传/删除/打印控制均不在HTTP白名单内。`loginType=tfa`可安全返回给上层诊断unsupported，但不保留tfaKey，也不宣称网站MFA通过。现已修复先前仅允许邮箱的错误限定；手机号与SMS实现及新增离线证据见[手机号修复记录](PHONE-LOGIN-VERIFICATION.md)。手机号格式限制是本地当前支持范围，并非已验证的云端完整规则。固定上游的发码流程只检查HTTP成功，不能要求每个成功响应必有`success:true`；上层应据200与自己的状态机解释。

**所有非200只返回 `Response(status,{})`，不读、不解析或导出响应body/Location/Cookie。** 因此401/403/429等可按状态区分；HTTP400正文里code=1/2的“过期/错误验证码”首版不能进一步区分，只能报告拒绝并由用户显式重试。不得为了看错误而输出原始响应或自动再发验证码。这是明确的首版限制，真实分支仍pending。

## API 与资源边界

```python
from bambu_monitor.cloud_http import BambuHttp, HTTPError

transport = BambuHttp(region,
    connection_factory=None,    # 默认 http.client.HTTPSConnection
    clock=time.monotonic,
    max_response_bytes=65536)   # 可收紧，不可超过64KiB
response = transport.request(method, exact_url,
    body=None, token=None, timeout=10)  # 兼容 auth.HttpTransport
transport.close()
```

- 导入和构造不联网；使用标准库直接HTTPS连接，不读取HTTP_PROXY/HTTPS_PROXY、不配置隧道、不跟随重定向、不存cookies、不自动重试。短信单独固定为国区网站域名及 `/api/v1/.../sendsmscode`，没有API域名回退。固定URL完整比较，拒绝query、fragment、userinfo、显式port、替代host/path/method；没有可注入额外header的接口。
- TLS使用 `ssl.create_default_context()` 的证书链与hostname验证，不提供关闭验证选项。注入factory收到同一验证context，用于离线测试，不另写测试专用解析器。
- 单实例只允许一条在途请求，忙时错误代码为ASCII `busy`；关闭为终止操作，后续请求一律closed。每次请求新建连接，结束明确shutdown/close，不靠连接池保留带凭据会话。
- 请求只允许对应的扁平JSON结构，最大16KiB；邮箱≤254字符，密码≤4096字符，code≤64字符，token≤16384个可打印ASCII字符；禁止header换行、非Secret GET token和POST携带token。
- 成功响应读取上限64KiB，分块读取每次≤4096字节；JSON必须严格UTF-8、object根、嵌套≤32，拒绝重复key、NaN/Infinity/溢出浮点和超过128位文本的整数。无效body不会替换成成功空对象；仅邮箱／短信发码允许真正空的200。
- 自己的响应头准入为≤64项/合计16KiB；Content-Length必须唯一、合法且不超body预算，长度不符拒绝；Transfer-Encoding仅允许chunked且不能同时有Content-Length；不解压gzip/deflate，避免绕过字节上限。Content-Type若存在必须application/json。标准库自身在getresponse阶段先解析头，其内置单行/头数限制仍先于这里，不能把后置16KiB检查称为解析器硬内存上限。
- HTTPError只有固定value-free类别：invalid_request、closed、busy、deadline_exceeded、clock_error、tls_error、network_error、invalid_response、response_too_large、unsupported_encoding、close_failed。异常不保留原URL、原body、原异常文本或chain，不写日志。正常Response的body仍是私有token/UID入口，不应打印；Response repr不显示body。

## 超时与取消的实际保证

默认10秒，调用方可给0–60秒内的正有限预算（包括2秒、5秒、10秒）；deadline使用单调clock。在创建连接、连接、发送、读头及每个read1块前后检查剩余预算，每个已有socket操作设置为剩余timeout。deadline到期立即拒绝后续步骤，不自动重试。

**这不是可强制终止任意阻塞的硬总时限。** 标准库/系统DNS `getaddrinfo`没有本实现可强取消的独立deadline，连接建立前没有可shutdown的socket。HTTP标准库内部读状态行、header行或chunk大小行可能在一个调用里执行多次recv；慢速字节持续到达时，socket timeout也不能严格等价于全过程deadline。实现会在该调用返回后检查并拒绝迟到结果，但不创建线程池、守护线程或隐蔽后台请求来伪称超时后已停止。相关真实平台阻塞/5秒退出目标仍pending。

对已公开为active的socket，`close()`先设置终止标记，再调用 `shutdown(SHUT_RDWR)`和connection.close，测试证明阻塞读取被释放、迟到成功不能返回、不能新建后续连接。若close失败显式HTTPError=close_failed，并把实例置为终止状态、保留待清理连接；后续request不能以新socket绕过失败，调用方仍可再次close尝试释放。对尚在DNS/工厂内部的调用，close只能撤销资格，待调用返回再释放资源，不能声称当场强制取消。

## 离线验收

`PYTHONPATH=src .venv-dev/bin/python -m unittest discover -s tests -p test_cloud_http.py -v`

当前独立执行结果：**19/19 passed，命令退出码0**（本机Python 3.15.2，macOS；未据此声明其他平台已实测）。测试通过注入connection/socket/clock覆盖真实实现的TLS默认、两区域、四路由与输入白名单、密码/code互斥、空发码响应、UID规范、必要设备投影、错误/重定向不读body、代理环境不改变直连host、字节/深度/类型/重复key/nonfinite边界、deadline各阶段、chunked/截断、异常canary与无chain、close取消及single-flight，并与实际CloudAuth状态机作无账号集成。所有数据为合成，真实HTTP云可达性、账号密码/code、MFA/SMS及区域兼容保持pending。

本次手机号修复重新读取上述固定SHA的 cloud/const/utils：SMS路径来自const中的 `BambuUrl.SMS_CODE`，utils只将`.com`替换为`.cn`，不会改变其现有网站域名或`/api`前缀。历史19项是原版本记录；当前检查结果见[手机号登录验证](PHONE-LOGIN-VERIFICATION.md)。
