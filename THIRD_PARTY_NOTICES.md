# 第三方项目、依赖与素材说明

本项目自行编写的代码和文档使用 [MIT License](LICENSE)。第三方软件、字体和商标的权利仍属于各自权利人，本项目的许可证不替代它们的许可。

## 开发依赖

依赖通过包管理器安装，不将其源代码复制进组件包；完整许可证以所安装版本的发行包为准，传递依赖也保留自身许可。

| 项目 | 用途 | 上游许可来源 |
|---|---|---|
| Pillow | 字模生成、离线预览和图像检查 | [Pillow LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| jsonschema | 内部 JSON Schema 契约校验 | [jsonschema COPYING](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) |
| rfc3339-validator | 测试中的日期时间格式校验 | [rfc3339-validator LICENSE](https://github.com/naimetti/rfc3339-validator/blob/master/LICENSE) |
| Paho MQTT 2.1.0 | 可选cloud extra；标准TLS云MQTT客户端 | [固定双许可声明 EPL-2.0 / EDL-1.0](https://github.com/eclipse-paho/paho.mqtt.python/blob/af64a4365c6ac5a7a4d339e7b00f44df91353b35/LICENSE.txt)；作为依赖使用，分发须保留原发行物许可 |
| keyring 25.x | 可选cloud extra；明确原生系统凭据后端 | [keyring 25.7.0发行页](https://pypi.org/project/keyring/25.7.0/)；本机发行包许可实读为MIT，分发保留原许可；不启用第三方明文backend |
| PetUI / Pet Manager | 可选组件打包、校验和设备宿主 | 外部工具，使用者单独安装并遵循其许可；本仓库不分发工具实现 |

## 字体与预览

仓库不包含 Arial、Hiragino、Noto 或 DejaVu 的字体文件。字体解析工具读取开发者电脑上的字体，默认构建直接使用已有字模，不重新读取系统字体。

现有数字字模由 macOS Arial Bold 渲染生成，原预览中的中文由 Hiragino Sans GB 渲染。提交的是渲染产物，不是原始字体文件；本项目不授予这些字体的使用、嵌入、修改或再分发许可。自行更换字体、生成字模或分发派生素材时，应核对所选字体许可。CI 预览使用系统安装的 Noto CJK。

## 协议参考

下列项目用于了解云端协议。本仓库当前没有引入它们的采集器实现，参考文档也不等于运行时依赖。

- [ha-bambulab](https://github.com/greghesp/ha-bambulab)：MIT；如后续复用代码，须保留其版权与许可证。
- [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI)：GFDL 1.3；本项目对协议事实作归纳并链接出处，没有将上游文档整体复制进仓库。

具体参考版本和证据见 [云协议核实记录](docs/CLOUD-PROTOCOL.md)、[本轮HTTP研究](docs/HTTP-RESEARCH.md)、[MQTT研究](docs/MQTT-RESEARCH.md)与[公开项目对照](docs/CLOUD-REFERENCES.md)。额外阅读的coelacant1项目固定README称AGPL-v3而LICENSE为GPLv3正文，未复制/引入其代码或样例，不能标为MIT。

## 名称与商标

Bambu Lab / 拓竹及相关名称和商标属于其权利人。本项目是独立社区项目，与 Bambu Lab 没有官方隶属、背书或合作关系。
