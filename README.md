# astrbot_plugin_ark_calendar

明日方舟活动日历插件。聚合 PRTS、anything-ics 与 ArknightsGachaData，生成 1440px 宽的活动/寻访长图。

## 指令

- `/方舟日历`：生成完整日历长图。
- `/方舟生日 <干员名称>`：查询干员生日。
- `/方舟日历状态`：查看缓存和数据源状态。
- `/方舟日历刷新`：管理员强制刷新。

## 数据来源

- PRTS Wiki：首页今日信息、活动详情、卡池表格、干员资料与图片。
- anything-ics：活动时间与干员生日。
- ArknightsGachaData：卡池时间、类型和 ID。
- PRTS Gacha Server Data：补全卡池六星 UP。

## 安装

将插件目录放入 AstrBot 的 `data/plugins/`，在 WebUI 安装依赖后启用插件。需要 AstrBot 支持最新的插件配置注入和 `html_render()` API。

## 缓存

运行数据写入：

`data/plugin_data/astrbot_plugin_ark_calendar/`

插件升级不会覆盖运行时缓存。

## 说明

插件使用的游戏图片版权属于上海鹰角网络科技有限公司及其关联公司；PRTS 页面内容遵循其站点声明。本插件仅用于非商业信息展示。
