# 第三方项目、依赖与素材说明

本项目自行编写的代码和文档使用 [MIT License](LICENSE)。第三方软件、字体和商标的权利仍属于各自权利人，本项目的许可证不替代它们的许可。

## 开发依赖

依赖通过包管理器安装，不将其源代码复制进组件包；完整许可证以所安装版本的发行包为准，传递依赖也保留自身许可。

| 项目 | 用途 | 上游许可来源 |
|---|---|---|
| Pillow | 字模生成、离线预览和图像检查 | [Pillow LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| jsonschema | 内部 JSON Schema 契约校验 | [jsonschema COPYING](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) |
| rfc3339-validator | 测试中的日期时间格式校验 | [rfc3339-validator LICENSE](https://github.com/naimetti/rfc3339-validator/blob/master/LICENSE) |
| PetUI / Pet Manager | 可选组件打包、校验和设备宿主 | 外部工具，使用者单独安装并遵循其许可；本仓库不分发工具实现 |

## 字体与预览

仓库不包含 Arial、Hiragino、Noto 或 DejaVu 的字体文件。字体解析工具读取开发者电脑上的字体，默认构建直接使用已有字模，不重新读取系统字体。

现有数字字模由 macOS Arial Bold 渲染生成，原预览中的中文由 Hiragino Sans GB 渲染。提交的是渲染产物，不是原始字体文件；本项目不授予这些字体的使用、嵌入、修改或再分发许可。自行更换字体、生成字模或分发派生素材时，应核对所选字体许可。CI 预览使用系统安装的 Noto CJK。

## 协议参考

下列项目用于了解云端协议。本仓库当前没有引入它们的采集器实现，参考文档也不等于运行时依赖。

- [ha-bambulab](https://github.com/greghesp/ha-bambulab)：MIT；如后续复用代码，须保留其版权与许可证。
- [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI)：GFDL 1.3；本项目对协议事实作归纳并链接出处，没有将上游文档整体复制进仓库。

具体参考版本和证据见 [云协议核实记录](docs/CLOUD-PROTOCOL.md)。

## 名称与商标

Bambu Lab / 拓竹及相关名称和商标属于其权利人。本项目是独立社区项目，与 Bambu Lab 没有官方隶属、背书或合作关系。
