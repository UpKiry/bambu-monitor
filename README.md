# 拓竹打印机监控 · PetUI 桌搭子组件

ESP32-P4 桌面组件的原型：在 640×480 屏上显示拓竹（Bambu Lab）3D 打印机的
剩余时间、进度、层数、喷嘴/热床温度、风扇和 AMS 各槽余量。

**当前状态：结构完成，数据未接入。** 所有读数是占位值（`00:00` / `0`），
因为 PetUI 契约禁止在没有真实数据源时伪造实时数值。数据通道的两种可行方案见
[docs/DATA-ADAPTER.md](docs/DATA-ADAPTER.md)。

- 组件 ID：`bambu-monitor`
- 版本：`0.1.6`
- 类型：`tool`
- 运行时：`p4-bounded-runtime-v4`

![首屏](design/page1-task.png)

## 目录结构

```
bambu-monitor/
├── package/              # 可打包的组件源，唯一真相
│   ├── component.json
│   ├── negative-screen.json
│   ├── buttons.json
│   ├── share.json
│   ├── runtime/widget.json
│   ├── assets/glyphs.png        # 数字字模图集
│   └── pc/cover.png             # 组件卡片封面
├── tools/
│   ├── build_package.py         # 生成 package/*.json（布局声明在这里）
│   ├── build_glyph_atlas.py     # 生成字模图集与度量
│   ├── render_frames.py         # 按固件语义渲染预览帧
│   ├── make_cover.py            # 首屏渲染帧 → pc/cover.png
│   ├── check_bindings.py        # 断言每个按键都真的翻页，不然退出码非 0
│   ├── diagnose_fonts.py        # 解释中文字号与数字字模的高度差
│   └── glyph_metrics.json       # 图集产物的度量副文件
├── design/               # 当前渲染帧（非手绘稿）
├── dist/                 # 构建产出的 .hachiapp
└── docs/
    └── DATA-ADAPTER.md   # 变量 ↔ 打印机字段映射，接入时照此填
```

## 构建

需要 Python 3 与 Pillow。

```bash
P=python3

$P tools/build_glyph_atlas.py   # 图集 + 度量
$P tools/build_package.py       # 生成 package/*.json
$P tools/render_frames.py       # 渲染 design/*.png
$P tools/make_cover.py          # 首屏 → package/pc/cover.png

petui pack    package dist/bambu-monitor-0.1.6.hachiapp
petui validate dist/bambu-monitor-0.1.6.hachiapp
petui publish dist/bambu-monitor-0.1.6.hachiapp
```

`petui` 位于 `~/.claw-pet/authoring/petui`。

## 交互

| 输入 | 事件 | 行为 |
|---|---|---|
| 摇杆上 / 下 | `joystick.up` / `joystick.down` | 下一页 / 上一页 |
| 摇杆左 / 右 | `knob.rotate_ccw` / `knob.rotate_cw` | 上一页 / 下一页 |
| SW1 | `button.sw1.short_press` | 确认 / 前进 |
| SW2 | `button.sw2.short_press` | 返回 / 后退 |

SW3 不绑定：退出由设备全局设置负责。

三个页面：**打印任务** → **环境温度** → **AMS 耗材**，循环切换。

## 设备约束（都是实测出来的，改布局前请先读）

这几条每一条都来自真机照片暴露的缺陷，不是推测：

1. **正文是 16 px 原生中文点阵。** 16 px 清晰；放大到 26–46 px 是把点阵拉伸，
   边缘会发虚。所以所有中文标签固定 16 px。
2. **大号数字走字模图集，且只能用整数倍缩放。** 图集格高 64 px，绘制尺寸只能是
   64 或 32。26/64、21/64 这类非整数倍会让像素不对齐，字看起来是糊的。
3. **文本从盒子顶部对齐，不是垂直居中。** 早先按居中排版，结果整屏内容下移、
   底部被裁。
4. **可用高度小于 480。** 实测底边约 40–100 px 会被裁掉。当前约定：除底部提示行
   外，所有内容必须结束于 y ≤ 400（`build_package.py` 里有断言）。
5. **meter 只画填充部分。** 进度 0 % 时填充宽度是 0 像素，等于什么都没画。
   所以必须有独立的 panel 当轨道，否则进度条看起来像没渲染。

## 已知未完成

- **数据源未接入。** 组件不联网、不含 URL 或凭据。
- **实机画面未验收通过。** 裁切量、字模图集是否真的被设备采用、颜色通道是否
  与预期一致，都还需要在真机上看过才算确认。
- `dist/` 里的包与本地组件库中的 0.1.6 内容一致（封面已比对相同）。

## 版本说明

`0.1.5` / `0.1.6` 是修 bug 的版本号（用户要求把之前的 0.5.0 回退）。组件库里
仍保留 `0.1.0` 与 `0.1.6`；更早的 0.2.0–0.5.0 已移到 `~/.claw-pet/components/.trash`，
避免客户端按语义化版本选中旧画面。
