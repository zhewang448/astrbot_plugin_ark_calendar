# 安装、文案与运行数据

## 安装

将插件目录放入 AstrBot 的 `data/plugins/`，在 WebUI 安装依赖后启用。

要求：

- AstrBot `>=4.17.0`
- Python 3.10 或更高版本
- 能访问配置的数据源和 T2I 渲染服务的网络

插件使用 AstrBot 系统配置中的 `t2i_strategy` 和 `t2i_endpoint` 生成 HTML 图片。远程 T2I 的网络延迟、排队和长图尺寸都会影响首次渲染耗时。需要减少远程延迟时，可部署 [AstrBot T2I Service](https://github.com/AstrBotDevs/astrbot-t2i-service)。

## 消息文案

默认风格为“罗德岛轻度猫娘”，还可选“极简正式”或“自定义消息模板”。状态查询、管理员告警和日志保持正式表述。

图片开始渲染提示也遵循这三套文案风格；在自定义消息模板中可编辑“图片开始渲染”，用于公招、帮助、未复刻排行、历史日程、订阅帮助和 B 站动态等图片指令。

自定义模板中，留空或填写 `@catgirl` 使用轻度猫娘文案，填写 `@plain` 使用极简正式文案，其他文本按原样使用。支持变量：

```text
{name} {birthday} {details} {candidates} {names}
{count} {user} {time} {end_time} {error} {index} {sent} {failed} {tags}
```

## 运行数据

运行数据位于：

```text
data/plugin_data/astrbot_plugin_ark_calendar/
```

其中包括数据快照、网络图片资源、最终日报和帮助图缓存、告警状态、生日祝贺状态与订阅记录。插件升级不会覆盖这些数据。

## 数据来源

- [PRTS Wiki](https://prts.wiki)：首页今日信息、活动详情、卡池表格、干员资料与图片。
- [anything-ics](https://github.com/SmallZombie/anything-ics)：活动时间与干员生日。
- [ArknightsGachaData](https://github.com/s-yh-china/ArknightsGachaData)：卡池时间、类型和 ID。
- [PRTS Gacha Server Data](https://weedy.prts.wiki/)：补全卡池六星 UP 信息。
