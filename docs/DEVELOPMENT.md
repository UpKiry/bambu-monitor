# 开发指南

## 环境

要求 Python 3.11+。默认构建使用已提交的 PNG 和字模度量，不需要系统中文字体；预览需要 Pillow 与本地中文字体。`check` 额外需要 jsonschema，命令执行期间不访问网络；首次安装依赖需要包源。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell 激活：`.venv\Scripts\Activate.ps1`。也可始终直接使用虚拟环境里的 Python，不依赖激活。打包需要本机 Pet Manager 维护的 PetUI CLI，检查/构建/预览不需要启动 Pet Manager。

`pyproject.toml` 管理依赖范围；已包含可运行的 `bambu-monitor` 离线CLI与 `src/` 核心，命令见 [离线操作](OFFLINE-CORE.md)。`pip install` 不会启动服务或登录账号。新增可选cloud依赖与 `login/collect/logout` 入口见 [云采集使用](CLOUD-COLLECTOR.md)；真实账号、云兼容与密钥库实测仍pending。macOS 托盘服务使用可选 `tray` extra（pystray macOS backend）；HTTP服务、HTML浏览器配置均无额外Web框架依赖。`python -m bambu_monitor service` 不自动注册开机启动；只有显式 `--install-launch-agent` 会改写当前用户LaunchAgents并启动服务。离线核心和HTTP用标准库；云MQTT固定Paho2.1.0，原生凭据库用keyring，既有预览依赖Pillow。没有锁定所有传递依赖，CI安装的是范围内版本。真实采集部署前再确定网络/密钥库依赖、分发和锁定策略。

## 统一入口和输出

| 命令 | 作用 | 默认产物 |
|---|---|---|
| `python tools/project.py check` | 生成一致性、变量/页面/绑定、PNG、Schema、unittest | 控制台报告；不改基线 |
| `python tools/project.py build` | 生成五份JSON，复制已有字模和封面 | `.build/package/`，恰七个正式文件 |
| `python tools/project.py preview` | 构建后渲染三页初始占位画面 | `.build/preview/` |
| `python tools/project.py pack` | 隔离staging，调用官方打包并校验 | `.build/bambu-monitor-0.1.6.hachiapp` |
| `python tools/project.py glyphs` | 显式重建数字字模和度量 | `.build/glyphs/` |

可用 `python tools/project.py <命令> --help` 查看参数。Make只是可选快捷方式：

```bash
make check PYTHON=.venv/bin/python
make preview PYTHON=.venv/bin/python ARGS='--font /path/to/cjk-font.ttc'
```

普通命令不清理用户目录，也不改 `package/`、`design/`、已有 `dist/`。已有目录混入未知文件时构建会报错，选择新的 `--build-dir`，不要用广泛删除来处理。

## 检查覆盖范围

`check` 检查以下工程约束：

- 生成的五份JSON与已提交基线深比较一致。
- 十三个变量的初始化类型和surface绑定，页面引用有效。
- 三个页面×六个物理输入共十八条翻页路径，包括循环边界。
- 字模/封面的PNG格式和尺寸。
- JSON Schema语法及内部契约、遥测质量/作用域、fake传输/认证、CLI回放与生命周期回归。具体用例与边界见 [本轮记录](M1-VERIFICATION.md)。

默认翻页检查解释本项目的声明规则，不是固件解释器。可额外配置PetUI技能目录，对比其 `bounded_app` 实现：

```bash
PETUI_SKILL_DIR=/path/to/petui-skill python tools/project.py check --official
```

目录中须有 `scripts/bounded_app.py`；不再写死某位开发者的home路径。该对比仍不验证USB、真实设备、云数据或像素渲染。

## 字体和预览

自动查找macOS的Hiragino Sans GB或Linux的Noto CJK；缺失会明确报错，不下载字体、不用不含中文字形的默认字体静默代替。可指定：

```bash
python tools/project.py preview --font /path/to/cjk-font.ttc
BAMBU_CJK_FONT=/path/to/cjk-font.ttc python tools/project.py preview
```

Ubuntu可安装 `fonts-noto-cjk`；CI使用 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。Windows请用 `--font` 指向本机合法可用的中文字体。跨系统预览的中文字形可能不同，不能据此承诺实机字体一致。

字模重建是独立操作，普通build不会执行：

```bash
python tools/project.py glyphs --font /path/to/digit-bold.ttf
```

也可设置 `BAMBU_DIGIT_FONT`。重建输出只在 `.build/glyphs/`；换字体会改变字形和度量，需要视觉审核后再手动更新正式图集和对应度量，二者必须一起更新。禁止把只改PNG不改度量的产物当作完成。

`render_frames.py` 是本项目的离线近似渲染，不是P4捕获。`make_cover.py` 默认只写构建目录；当前构建复用原封面，不自动用平台不同字体的新预览覆盖它。

## 修改布局和更新基线

布局在 [生成脚本](../tools/build_package.py)，`generate_package(glyph_metrics)` 是无IO的纯生成函数；`write_package` 才显式写JSON。工具import不会生成或覆盖组件文件。

1. 修改生成逻辑，保持组件ID；本阶段无UI需求时保留现有版本/视觉/按键。
2. 执行build和preview，检查首屏与相关页面；视觉变化应先获得用户确认。
3. 将 `.build/package/` 中五份JSON逐一核对后更新 `package/` 同名基线。素材和封面另行审阅，不整目录覆盖，不收录重复副本。
4. 执行check。确认生成逻辑与基线一致后再打包；故意修改布局时check在更新基线前失败是正常保护。

当前没有自动“接受全部生成变更”的命令，避免误覆盖用户资源。一般工程重构不需要更新基线。

## 受控打包

默认查找PATH中的 `petui`，或用户目录 `.claw-pet/authoring/petui`（Windows为 `petui.cmd`）；可显式指定：

```bash
python tools/project.py pack --petui /path/to/petui
PETUI_BIN=/path/to/petui python tools/project.py pack
```

正式清单只有：

```text
component.json
negative-screen.json
buttons.json
share.json
runtime/widget.json
assets/glyphs.png
pc/cover.png
```

每次pack在 `.build/staging/` 建独立清单目录，再调用官方 `pack` 和 `validate`。它不递归复制原始资源目录，因此用户已有的重名副本不会进入新包，也不会被删除。官方工具会生成自己的包元数据。

`pack` 不代替 `check`；建议先check再pack。参数可改变隔离输出位置，禁止指向原正式资源和已发布包。打包器/校验器报错则保留原错误，不手工造ZIP、哈希目录或绕过验证。

本次工程整理没有产品功能修改，不发布或同步；未来发布时还需按实际组件能力执行PetUI要求的自测、真实预览和设备验证，再使用正式发布器。

## CI与实际验证

[CI配置](../.github/workflows/check.yml)在Ubuntu的Python 3.11/3.12/3.13运行build、check、preview，上传预览产物。它不持有账号秘密，不连接云、打印机、USB，不发布组件。云端CI是否已触发须以仓库实际运行记录为准，不能仅凭配置文件声称绿灯。

本机已执行项目及限制见 [验证记录](VERIFICATION.md)。
