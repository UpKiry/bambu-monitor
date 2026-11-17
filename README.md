# Bambu Monitor · 拓竹打印机监控

[![Offline checks](https://github.com/UpKiry/bambu-monitor/actions/workflows/check.yml/badge.svg)](https://github.com/UpKiry/bambu-monitor/actions/workflows/check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)
[![Status: Prototype](https://img.shields.io/badge/Status-Prototype-orange.svg)](#项目状态)

面向 **ESP32-P4 / PetUI** 的 640×480 拓竹打印机监控组件，用三个页面展示打印任务、环境温度和 AMS 耗材信息。

A Bambu Lab printer dashboard prototype for ESP32-P4 / PetUI, with offline build tools and a provider-independent telemetry contract.

> **当前是显示原型，尚未连接真实打印机数据。** 页面中的 `00:00` 和 `0` 是初始化占位值。云认证、实时采集和设备变量下发仍在规划中。

## 界面预览

| 打印任务 | 环境温度 | AMS 耗材 |
| :---: | :---: | :---: |
| ![打印任务：剩余时间、层数、进度和温度](design/page1-task.png) | ![环境页面：腔温和风扇占位读数](design/page2-environment.png) | ![AMS 页面：四个槽的余量占位读数](design/page3-ams.png) |

这些图片由离线渲染工具生成，不是实时遥测或设备实拍。

## 项目状态

| 能力 | 状态 |
| --- | --- |
| 三页布局、13 个显示变量、六个按键翻页 | 已实现声明结构，实机画面待验收 |
| 隔离构建、预览、明确文件清单打包 | 已实现 |
| 数据契约、合成样例、离线检查和 CI 配置 | 已实现；CI 运行结果见上方徽章 |
| Bambu Cloud 认证、设备发现、云 MQTT 采集 | 已选择方案 A（自研），需求和计划已整理，实现待开始 |
| 自定义界面的实时变量下发 | 待核对 Pet Manager 与固件能力 |
| 打印机本地 MQTT | 后续计划 |

组件 ID 为 `bambu-monitor`，版本 `0.1.6`，运行时为 `p4-bounded-runtime-v4`。现有 [组件包](dist/bambu-monitor-0.1.6.hachiapp)是显示原型，不包含云端数据提供器。

## 数据接入方向

**已选择方案 A：自研 Bambu Cloud 适配器。** 优先实现云认证、设备发现和云 MQTT 遥测，以后增加本地 MQTT。云端 HTTP 用于认证和元数据，云 MQTT 用于实时状态，不要求电脑连接打印机的局域网 MQTT。

```text
Bambu Cloud HTTP + 云 MQTT → 自研采集器 → 统一快照 → Pet Manager宿主适配 → PetUI显示
打印机 LAN MQTT（后续）───────────────────┘
```

这是待实现的数据流。先完成 M0 宿主/13变量能力核实，并行推进 M1 离线采集核心，再分别验收认证、云订阅、宿主传输和实机显示。Python 是计划中的参考采集器运行时，不在组件包或 ESP32 内执行。

从 [需求规格](docs/REQUIREMENTS.md)、[实施计划](docs/IMPLEMENTATION-PLAN.md)与 [验收矩阵](docs/ACCEPTANCE.md)阅读；准备在新对话实施时使用 [交接文档](docs/HANDOFF.md)。B（pybambu）和 C（HA）的 [历史比较](docs/CLOUD-OPTIONS.md)保留供追溯，不再作为待选项。

相关云协议由社区源码核实，并非官方稳定开放 API；区域、验证码/MFA和token失效处理见 [协议研究](docs/CLOUD-PROTOCOL.md)。

统一模型区分有效 `0`、未知、不支持与过期。已有composition/bridge适配器限制为8项，PC fetch则是每来源8项且不适合私网鉴权；当前13变量与质量状态需要独立核对宿主及目标设备支持，不能只靠声明配额推断。

## 快速开始

开发工具要求 **Python 3.11+**。仅做本地检查、构建和预览，不需要打印机、Bambu 账号或 Pet Manager。

```bash
git clone https://github.com/UpKiry/bambu-monitor.git
cd bambu-monitor

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'

python tools/project.py check
python tools/project.py build
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活；也可直接调用虚拟环境里的 Python。

### 预览

```bash
python tools/project.py preview
```

默认输出三张图片到 `.build/preview/`，需要本机可用的中文字体。macOS / Linux 支持自动查找，也可以手动指定：

```bash
python tools/project.py preview --font /path/to/cjk-font.ttc
```

Linux 可安装 `fonts-noto-cjk`。跨平台中文字形可能不同；普通构建复用仓库中的字模，只有显式执行 `glyphs` 才会重建它。

### 打包到设备组件

打包需要单独安装 Pet Manager 维护的 PetUI CLI：

```bash
python tools/project.py pack
# 或指定工具位置
python tools/project.py pack --petui /path/to/petui
```

输出为 `.build/bambu-monitor-0.1.6.hachiapp`，命令会调用官方打包器和校验器，不自动发布或同步。可在 Pet Manager 组件中心导入包，再打开同一组件卡片执行“保存并同步”；同步前须满足目标设备能力要求。

构建仅收录明确清单内的组件文件，源码、测试、示例和凭据不会随组件打包。更多参数、字体与基线更新方式见 [开发指南](docs/DEVELOPMENT.md)。

## 交互

| 输入 | 行为 |
| --- | --- |
| 摇杆上 / 右、SW1 | 下一页 |
| 摇杆下 / 左、SW2 | 上一页 |

三个页面循环切换。退出由设备全局设置负责，组件不占用 SW3。关键读数布局结束于 y ≤ 400，中文正文保持 16 px，大号数字使用字模图集；裁切、字体和颜色仍需实机验证。

## 工程结构

```text
bambu-monitor/
├── package/       # 组件资源与生成 JSON 基线
├── tools/         # 统一入口、布局生成、预览和检查
├── schemas/       # 来源无关的内部数据契约
├── examples/      # 合成快照与配置设计示例
├── tests/         # 离线契约测试
├── docs/          # 架构、协议、方案、开发与验证记录
├── design/        # 三页离线参考图
├── dist/          # 已有组件包
└── .github/       # CI 配置
```

默认产物进入 `.build/`，不覆盖正式组件资源。布局与变量由 [生成脚本](tools/build_package.py)维护，[运行清单](package/runtime/widget.json)用于基线比对；[统一入口](tools/project.py)负责开发命令。

## 文档

| 文档 | 内容 |
| --- | --- |
| [需求规格](docs/REQUIREMENTS.md) | 方案 A 的目标、MVP范围、功能与非功能需求 |
| [实施计划](docs/IMPLEMENTATION-PLAN.md) | 架构决策、宿主证据、M0–M6及阶段退出条件 |
| [验收矩阵](docs/ACCEPTANCE.md) | FR/NFR对应的离线、云端与实机验证 |
| [新会话交接](docs/HANDOFF.md) | 已选决策、环境、已知缺口和可复制的启动消息 |
| [云端接入历史比较](docs/CLOUD-OPTIONS.md) | 三种路线及选择 A 的记录 |
| [云协议研究](docs/CLOUD-PROTOCOL.md) | 区域、认证、设备接口、MQTT 与固定版本证据 |
| [架构与边界](docs/ARCHITECTURE.md) | 采集、缓存、显示投影与设备宿主的职责 |
| [数据契约](docs/DATA-ADAPTER.md) | 字段映射、增量、缺测与过期规则 |
| [开发指南](docs/DEVELOPMENT.md) | 环境、检查、构建、预览、打包 |
| [路线图](docs/ROADMAP.md) | 后续实施顺序及验收条件 |
| [验证记录](docs/VERIFICATION.md) | 已执行的检查和未验证的真实链路 |

## 测试与贡献

```bash
python tools/project.py check
```

检查覆盖生成一致性、13 个变量绑定、三页 × 六输入的 18 条翻页路径、PNG 资源、JSON Schema 和数据契约测试。它不连接云端、打印机或 USB，也不代表实机验收通过。GitHub Actions 在 Ubuntu / Python 3.11、3.12、3.13 上运行离线构建、检查和预览。

欢迎通过 Issue 或 Pull Request 参与。报告兼容问题时请附 Python / 操作系统版本，以及相关机型、固件和脱敏错误；提交代码前运行检查，说明改动目的与验证方式。界面改动请附预览，保持生成逻辑与组件基线一致。

不要在 Issue、PR、日志或组件中提交密码、验证码、云 token、LAN Access Code 或原始账户响应。[配置示例](examples/provider.example.toml)只用于设计讨论，当前没有配置加载器。

## 开源协议

本项目自行编写的代码和文档采用 [MIT License](LICENSE)，允许使用、修改和再分发，保留版权与许可声明即可。依赖、系统字体及第三方参考遵循各自许可，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。

Bambu Lab / 拓竹等名称和商标属于其权利人。本项目是独立社区项目，与 Bambu Lab 没有官方隶属或背书关系。
