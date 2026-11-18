# 开发指南

日常运行见[使用指南](LOCAL-SERVICE.md)，模块与宿主边界见[架构](ARCHITECTURE.md)。以下命令在项目根目录执行；首次安装依赖需要网络，默认检查与构建不连接账号、打印机或 USB。

## 环境与依赖

Python 3.11+，使用自己的虚拟环境，不修改系统 Python 或应用签名运行时：

```sh
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install -e '.[dev,cloud]'
# macOS 托盘开发再添加 tray
.venv-dev/bin/python -m pip install -e '.[tray]'
```

[pyproject.toml](../pyproject.toml)管理依赖：Pillow用于构建/预览，dev包含Schema验证，cloud包含固定Paho2.1.0与keyring，tray包含pystray及macOS PyObjC。HTTP/回放只用标准库。Paho有私有hook，升级必须复验；传递依赖未全锁定。`pip install` 不启动服务或登录账号。

构建工具可用其他OS上的Python；macOS托盘仅声明在macOS图形会话验证，不能据跨平台依赖推断整个采集器可用。CLI隐藏输入要求POSIX控制TTY；旧Tk要求带tkinter的Python。Windows安全设置保存与原生私有输入尚不支持。

## 统一入口

| 命令 | 作用 | 默认输出 |
| --- | --- | --- |
| `python tools/project.py check` | 生成一致性、变量/按键、PNG、Schema及unittest | 控制台报告，不改基线 |
| `python tools/project.py build` | 生成五份JSON、复制已有字模/封面 | `.build/package/`，恰7个文件 |
| `python tools/project.py preview` | 构建并渲染三页初始占位图 | `.build/preview/` |
| `python tools/project.py display-preview` | 从展示模型渲染三页与降级状态 | `.build/display-preview/`，19个PNG |
| `python tools/project.py pack` | staging后调用官方打包与校验 | `.build/bambu-monitor-0.1.6.hachiapp` |
| `python tools/project.py glyphs` | 显式重建字模与度量 | `.build/glyphs/` |

命令中的 `python` 指所选虚拟环境解释器，参数见各命令 `--help`。也可用 `make check PYTHON=.venv-dev/bin/python`。

普通命令不覆盖 `package/`、`design/`、已有 `dist/`，不清理用户副本。构建目录含未知文件时选择新的 `--build-dir`，不要广泛删除。

## 检查与浏览器验证

`check` 验证五份生成JSON与正式基线一致、13变量类型/绑定、三页×六输入的18条翻页路径、PNG尺寸、Schema、云适配/增量质量/凭据/设置/服务生命周期。默认按键检查是声明解释，不是设备执行。

可选与本机PetUI解释器比较：

```sh
PETUI_SKILL_DIR=/path/to/petui-skill python tools/project.py check --official
```

该目录需包含 `scripts/bounded_app.py`；不写死作者路径。实际测试记录与未覆盖项见[验证状态](VERIFICATION.md)。

真实 Chromium 点击测试使用合成云传输与临时本机服务，无真实账号或钥匙串：

```sh
npm install --prefix .build/browser-check --cache .build/npm-cache --no-audit --no-fund playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node .build/browser-check/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node tests/browser_service_check.cjs
PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node tests/browser_dashboard_check.cjs
```

默认使用 `.venv-dev/bin/python`，可通过 `BAMBU_TEST_PYTHON`指定测试解释器。真实Tk交互可在有图形会话时设 `BAMBU_TEST_GUI=1`运行检查；不要把条件跳过计为通过。代码和测试入口见[tests](../tests/test_local_service.py)。

## 字体与组件基线

默认build复用已提交字模PNG和度量，不读取系统字体。preview需要中文字体：自动找macOS Hiragino Sans GB或Linux Noto CJK；缺失明确报错，不下载字体或静默用缺字字体。

```sh
python tools/project.py preview --font /path/to/cjk-font.ttc
python tools/project.py glyphs --font /path/to/digit-bold.ttf
```

也支持 `BAMBU_CJK_FONT`、`BAMBU_DIGIT_FONT`。Linux可安装 `fonts-noto-cjk`；CI使用 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。跨平台像素可能不同，离线渲染不是实机捕获。字体许可见[第三方说明](../THIRD_PARTY_NOTICES.md)。

展示模型在[display.py](../src/bambu_monitor/display.py)，只做格式化与质量映射，不改变严格快照；渲染器在[render_display.py](../tools/render_display.py)，`display-preview`不写 `package/`。布局与变量在[生成器](../tools/build_package.py)：`generate_package`无I/O，`write_package`才写JSON。改UI时：先保留组件ID并明确版本/指标范围，执行build和preview审阅，再逐一更新五份正式JSON及必要素材，最后check。换字模须将图集与度量一起核对，不能只替换PNG。普通工程重构无需更新组件基线。

## 受控打包与发布边界

pack需另装Pet Manager维护的PetUI CLI，优先PATH，其次用户 `.claw-pet/authoring/petui`（Windows为 `petui.cmd`），也可指定：

```sh
python tools/project.py pack --petui /path/to/petui
```

正式资源清单为 `component.json`、`negative-screen.json`、`buttons.json`、`share.json`、`runtime/widget.json`、`assets/glyphs.png`、`pc/cover.png`。pack只复制这7项，官方工具生成包元数据；配置、Python源码、测试、重复副本均不入包。

pack不代替check，不自动发布/导入/同步。打包器失败保留错误，不手工造ZIP或绕过校验。真实云、设备能力、USB/显示验收需另行完成；发布按明确指令执行。

[CI配置](../.github/workflows/check.yml)在Ubuntu/Python3.11–3.13运行离线构建、检查、预览并上传产物。它不连接云/USB、不持有秘密或发布组件；远程执行是否成功须查看实际CI运行。
