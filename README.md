# 罗德岛行动终端 `astrbot_plugin_ark_calendar`

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17.0-00a8c6.svg)](https://github.com/AstrBotDevs/AstrBot)

## 访问统计

<p align="center">
  <a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@:astrbot_plugin_ark_calendar?theme=rule34" alt="访问统计"></a>
</p>

明日方舟信息聚合与查询插件。插件聚合 PRTS、anything-ics、Torappu 与 ArknightsGachaData，生成罗德岛风格的活动、寻访和作战图片，并提供订阅、生日、B站动态和公开招募工具。

当前版本：`v0.9.3`

## 文档导航

- [指令与交互](docs/commands.md)：普通指令、管理员指令、示例和 B站消息发送规则。
- [配置与定时任务](docs/configuration.md)：配置顺序、画质作用范围、自动任务、订阅和 B站推送。
- [安装、文案与运行数据](docs/operation.md)：安装要求、消息风格、缓存目录和数据来源。
- [版本更新记录](CHANGELOG.md)

## 功能概览

- 今日作战信息、活动与寻访时间轴、卡池详情和干员生日。
- 活动与卡池订阅提醒，支持群聊 @ 行为。
- 官方 B站动态查询与推送，支持文字、图片、视频动态和转发筛选。
- 公开招募标签计算、别名识别和招募终端图片。
- 日报、历史日程、帮助图、公招图和 B站动态图均支持清晰度档位。
- 数据快照、最终图片、帮助图和网络资源缓存，以及异常降级通知。

## 快速开始

1. 将插件目录放入 AstrBot 的 `data/plugins/`。
2. 在 WebUI 安装依赖并启用插件。
3. 发送 `/方舟日历帮助` 查看当前指令和配置说明。

详细安装要求、T2I 服务和运行目录见[安装、文案与运行数据](docs/operation.md)。

## 效果预览

### 1. 日历总览

<details>
<summary>展开查看日历总览</summary>
<p align="center">
  <img src="assets/readme/daily-calendar.webp" alt="罗德岛行动终端日历总览" width="900">
</p>
</details>

### 2. B 站动态

<p align="center">
  <img src="assets/readme/bilibili-dynamic-image.webp" alt="B站动态预览" width="700">
</p>

### 3. 公招计算

<details>
<summary>展开查看公招计算结果</summary>
<p align="center">
  <img src="assets/readme/recruitment.webp" alt="公开招募计算结果" width="700">
</p>
</details>

### 4.指令手册

<p align="center">
  <img src="assets/readme/command-manual.webp" alt="罗德岛终端手册预览" width="900">
</p>

## 🙏 致谢

感谢以下数据来源和项目：

- [PRTS Wiki](https://prts.wiki)：首页今日信息、活动详情、卡池表格、干员资料与图片。
- [anything-ics](https://github.com/SmallZombie/anything-ics)：活动时间与干员生日。
- [Torappu / Arknights Asset Storage](https://torappu.prts.wiki/gamedata/latest/excel/gacha_table.json)：最新卡池开关时间、规则类型和卡池 ID。
- [ArknightsGachaData](https://github.com/s-yh-china/ArknightsGachaData)：补充正式名称、历史类型和 ID，并作为时间轴回退源。
- [PRTS Gacha Server Data](https://weedy.prts.wiki/)：补全卡池六星 UP 信息。

本插件使用的游戏图片版权属于上海鹰角网络科技有限公司及其关联公司；PRTS 页面内容遵循其站点声明。本项目仅用于非商业信息展示。
