# bambu-monitor 数据接入说明

组件包本身只声明结构，不含任何联网代码、URL 或凭据。数据接入时把下列字段写进
runtime/widget.json 的 vars（或由宿主下发快照），画面自动更新，布局与状态机无需改动。

## 变量映射（组件变量 ← 打印机字段）

| 组件变量 | 类型 | 语义 | ha-bambulab 同源字段 |
|---|---|---|---|
| remain | string | 剩余时间 "HH:MM" | mc_remaining_time（分钟，'0' 表示未知）→ 自行格式化 |
| prog | int 0-100 | 打印进度 | mc_percent |
| layer | int | 当前层 | layer_num |
| layers | int | 总层数 | total_layer_num |
| nozzle | int | 喷嘴温度 °C | nozzle_temper |
| bed | int | 热床温度 °C | bed_temper |
| chamber | int | 腔体温度 °C | chamber_temper |
| fan_part | int % | 部件冷却风扇 | cooling_fan_speed（'0'-'15' 档位，需换算百分比） |
| fan_aux | int % | 辅助风扇 | big_fan1_speed / big_fan2_speed |
| tray1 - tray4 | int % | AMS 各槽余量 | ams.tray[].remain |

## 本版为何没有状态机

首版只展示读数，没有 gcode_state 驱动的「打印中 / 已暂停 / 完成 / 失败」：
契约禁止在没有数据源时伪造实时值，状态机依赖真实事件，接数据后再加更稳。
接入时建议新增 int 变量 gcode_state，用 logic: "p4-logic-v1" 的 when 条件驱动
标题与状态芯片，而不是把状态写死成文本。

## 两条可选通道

1. PC 提供器（推荐）：客户端新增 bambu.printer 数据源，走 USB widget/data。
   IP 与 access code 留在 PC 私有存储，不进包、不下发设备。
2. 设备直连：固件有 wifi.feed + response_map + http，但固件无 MQTT/TLS 客户端，
   因此需要先在本机把 MQTT 数据转成公开 HTTPS JSON 再让设备拉取；固件拒绝私网地址。

## 重新打包

    petui pack pkg <输出>.hachiapp
    petui validate <输出>.hachiapp
    petui publish <输出>.hachiapp

保持 component.json.id = "bambu-monitor" 不变，新内容哈希会成为同一组件的新版本；
设备需在组件中心打开同一卡片点「保存并同步」才会覆盖。
