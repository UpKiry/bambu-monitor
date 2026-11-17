# 拓竹打印机监控 · Bambu Monitor

ESP32-P4 / PetUI 的 640×480 打印机监控组件，包含打印任务、环境温度、AMS 耗材三个页面。组件 ID 为 `bambu-monitor`，版本 `0.1.6`，运行时为 `p4-bounded-runtime-v4`。

**当前可运行的是显示组件及离线构建工程；真实数据提供器尚未实现。** 现有数值是初始化占位值。工程整理没有接入账号、改动界面、发布组件或同步设备。

数据接入优先使用 **Bambu Lab 云**；以后实现本地 MQTT。云实时遥测仍使用 MQTT，但连接的是 Bambu 云服务器，不需要连接打印机的局域网地址。

![打印任务页面](design/page1-task.png)

## 先读哪些文档

| 目的 | 文档 |
|---|---|
| 比较可实施方案、决定下一步 | [云端接入方案](docs/CLOUD-OPTIONS.md) |
| 核对登录、区域、设备列表、云 MQTT 和证据来源 | [云协议研究](docs/CLOUD-PROTOCOL.md) |
| 理解采集、模型、显示和宿主的职责 | [架构与边界](docs/ARCHITECTURE.md) |
| 查看 13 个变量和规范化数据规则 | [数据契约与字段映射](docs/DATA-ADAPTER.md) |
| 安装依赖、检查、构建、预览、打包 | [开发指南](docs/DEVELOPMENT.md) |
| 后续实施顺序和验收标准 | [路线图](docs/ROADMAP.md) |
| 了解本次交付实际验证到哪里 | [验证记录](docs/VERIFICATION.md) |

## 快速开始

要求 Python 3.11+；推荐独立虚拟环境。以下命令仅处理本地工程，不登录云账号：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python tools/project.py check
python tools/project.py build
```

Windows 使用 `.venv\Scripts\Activate.ps1` 激活；也可直接调用虚拟环境内的 Python。Make 是可选快捷方式，不是构建依赖。

```bash
python tools/project.py preview  # 需要中文字体，输出到 .build/
python tools/project.py pack    # 需要本机 PetUI，打包并校验；不会发布
```

具体字体、PetUI 路径和产物位置见 [开发指南](docs/DEVELOPMENT.md)。普通构建复用已经提交的字模，不因电脑不同而重建字体。

## 工程结构

```text
bambu-monitor/
├── package/                 # 已提交的组件资源和生成后的 JSON 基线
├── tools/                   # 布局生成、工程入口、预览与离线检查
├── schemas/                 # 待实现数据提供器的内部 JSON Schema
├── examples/                # 合成快照、配置设计样例，均不作为实时数据
├── tests/                   # 离线契约测试
├── docs/                    # 架构、云端选型、开发、验收文档
├── design/                  # 现有界面参考帧
├── dist/                    # 原有 0.1.6 包，本次未更新
├── .github/workflows/       # 离线工程检查配置
├── .build/                  # 新构建和预览产物，不提交
├── pyproject.toml
└── Makefile
```

布局和变量的生成逻辑在 [构建脚本](tools/build_package.py)；[组件资源](package/runtime/widget.json)保留为可比对基线。修改布局时先改生成逻辑，通过检查后再按 [开发指南](docs/DEVELOPMENT.md)更新基线。目录中的用户重复副本不自动删除；打包使用显式清单，避免误收录。

## 已实现与待实现

| 项目 | 当前状态 |
|---|---|
| 三页 UI、13 个变量绑定、六个按键翻页 | 已有组件结构；实机画面仍待验收 |
| 可移植构建入口、明确打包清单、离线检查 | 本次工程化范围，执行结果见验证记录 |
| 统一数据模型、样例、缺测/过期语义 | 已定义内部契约；尚无数据采集器 |
| 云登录、设备发现、云 MQTT、私有凭据存储 | 待方案选择后实施 |
| 本地 MQTT | 后续提供器，保留模型兼容位置 |
| 自定义 surface 的实时变量下发 | 待核对宿主和固件能力；现有列表协议不能直接证明支持 |
| 新版数据状态 UI、发布、USB 同步、设备端验收 | 尚未进行 |

## 交互和设备约束

摇杆上下、左右均可翻页；SW1 向前，SW2 返回。三个页面循环切换。退出由设备全局设置负责，组件不占用 SW3。

保留原项目的屏幕约束：中文正文 16 px，大号数字使用字模图集；关键读数结束于 y ≤ 400；进度条有独立轨道。现有布局见 [构建脚本](tools/build_package.py)，设备裁切、字体效果和颜色仍需真机验证。

## 凭据和发布边界

账号密码、验证码、云 token 和本地访问码应由后续 PC 提供器私有管理，不进入组件、样例或日志。当前不需要填写任何真实凭据。[配置样例](examples/provider.example.toml)仅用于讨论配置设计，没有加载器。

打包、校验、本地导入、设备同步、实机运行是不同阶段。本次不发布新版本。今后修改仍复用组件 ID `bambu-monitor`；本地发布后，需要在 Pet Manager 的同一组件卡片执行“保存并同步”才能替换设备内容。
