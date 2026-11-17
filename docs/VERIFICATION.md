# 本次工程整理验证记录

日期：2026-11-18（Asia/Shanghai）。范围为工程工具、内部契约、文档与方案；未选择或实现真实数据提供器，未改变组件UI、ID和版本。

## 验证环境

- macOS，本机普通Python `3.15.2`。
- 可用工程环境：`.venv-dev/bin/python`，Pillow `12.3.0`、jsonschema `4.26.0`，并显式安装 `rfc3339-validator`。
- 本机PetUI CLI用于官方pack/validate；本机PetUI技能中的 `bounded_app` 用于可选按键路径对比。
- 新clone按 [开发指南](DEVELOPMENT.md)创建自己的虚拟环境。本机可直接复用以下命令：

```bash
.venv-dev/bin/python tools/project.py check
.venv-dev/bin/python tools/project.py build
.venv-dev/bin/python tools/project.py preview
```

此前尝试复用应用内签名Python创建 `.venv/` 时，jsonschema的原生扩展被macOS库校验拒绝。没有改系统运行时或签名；改用普通Python建立 `.venv-dev/` 并安装依赖后成功。原试验环境保留且被git忽略，当前请使用 `.venv-dev/`。

## 实际通过的项目

| 检查 | 结果与证据范围 |
|---|---|
| `pip install -e '.[dev]'` | 在独立 `.venv-dev/` 成功；不安装云/MQTT提供器 |
| 统一 `check` | 退出码0 |
| 五份生成JSON与正式基线 | 深比较一致 |
| 变量初始化与绑定 | 十三项全部声明和使用，类型通过 |
| 离线按键检查 | 三页面×六输入，共十八条含循环边界通过 |
| 可选PetUI `bounded_app` | 同十八条路径比较通过；不是设备执行验收 |
| JSON Schema | Draft2020-12语法通过 |
| 契约测试 | 十项全部通过，覆盖零/缺测/不支持/过期、范围、时间格式、来源及额外凭据字段 |
| `build` | `.build/package/` 恰七个清单文件，复用原字模和封面 |
| `preview` | 三张640×480初始帧；与原 `design/` 参考帧逐像素一致，首屏人工查看 |
| 官方 `pack` + `validate` | 新包位于 `.build/bambu-monitor-0.1.6.hachiapp`，两个命令退出码均0；包内为七个清单文件及官方生成的hachiapp元数据 |
| 显式 `glyphs` | 只生成 `.build/glyphs/`，正式图集未修改 |
| 文档链接 | 九份项目文档中的五十二个本地链接均存在 |
| 输出保护 | 对工程外、正式资源目录、symlink等路径的拒绝验证通过；无删除用户副本 |
| 按键检查适用边界 | 对当前不支持的when/from_page/else/to等字段明确拒绝，避免误报 |
| 补丁格式 | `git diff --check` 通过 |

根会话最终独立执行：

```bash
PETUI_SKILL_DIR=/path/to/petui-skill .venv-dev/bin/python tools/project.py check --official
```

结果为十项测试全部通过、十八条默认路径和十八条可选解释器路径通过。官方解释器路径是本机配置，不写死在源码里。

## 测试曾发现并修复的问题

最初jsonschema依赖未包含date-time格式检查器，带有 `format: date-time` 的字段未拒绝不带时区时间；契约测试明确失败。已在开发依赖中增加 `rfc3339-validator`，重装后重跑通过，没有删除或放宽该测试。

独立静态审阅还发现统一preview/glyphs输出可指向正式资源，现已让统一入口对这些输出进行与build/pack一致的路径检查。普通构建仍不重建字模。

## 明确未验证或未实施

- Bambu Cloud真实登录、邮箱/SMS/MFA、token寿命与重登。
- HTTP设备列表、UID接口的真实账户调用。
- 云MQTT订阅、首帧、持续遥测、断线恢复和实际固件字段。
- 实际提供器、增量合并、freshness算法、系统密钥库和日志脱敏代码。
- 本地MQTT和HA真实连接。
- 自定义surface实时变量下发、USB、实际设备能力和实机画面。
- GitHub Actions远程执行记录；只提交了 [CI配置](../.github/workflows/check.yml)。
- 组件发布、本地库导入、保存并同步；这些操作均未执行。

[原有发布包](../dist/bambu-monitor-0.1.6.hachiapp)、正式组件资源和原预览未改动，用户已有重复副本保留。本次隔离产物供工程验证，不是包含云功能的新版本。后续验收步骤见 [路线图](ROADMAP.md)。
