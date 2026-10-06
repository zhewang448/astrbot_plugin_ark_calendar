# 安装、文案与运行数据

## 安装

将插件目录放入 AstrBot 的 `data/plugins/`，在 WebUI 安装依赖后启用。

要求：

- AstrBot `>=4.26.8`
- Python 3.10 或更高版本
- 能访问配置的数据源和 T2I 渲染服务的网络

如需 B站视频动态附带发送视频文件，另安装并启用 [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)，并在其 B站解析器中配置清晰度、编码、Cookie、时长限制和缓存。

插件使用 AstrBot 系统配置中的 `t2i_strategy` 和 `t2i_endpoint` 生成 HTML 图片。远程 T2I 的网络延迟、排队和长图尺寸都会影响首次渲染耗时。需要减少远程延迟时，可部署 [AstrBot T2I Service](https://github.com/AstrBotDevs/astrbot-t2i-service)。

## 消息文案

默认风格为“罗德岛轻度猫娘”，还可选“极简正式”或“自定义消息模板”。状态查询、管理员告警和日志保持正式表述。

图片开始渲染提示也遵循这三套文案风格；在自定义消息模板中可编辑“图片开始渲染”，用于公招、帮助、未复刻排行、历史日程、订阅帮助和 B 站动态等图片指令。

自定义模板中，留空或填写 `@catgirl` 使用轻度猫娘文案，填写 `@plain` 使用极简正式文案，其他文本按原样使用。支持变量：

```text
{name} {birthday} {details} {candidates} {names}
{count} {max} {user} {time} {end_time} {error} {index} {sent} {failed} {tags}
```

## 运行数据

运行数据位于：

```text
data/plugin_data/astrbot_plugin_ark_calendar/
```

其中包括数据快照、网络图片资源、最终日报和帮助图缓存、告警状态、生日祝贺状态与订阅记录。AI 工具只读取并裁剪其中的结构化 JSON，不会把图片或内部会话字段传入模型。插件升级不会覆盖这些数据。

主要缓存文件：

| 文件 | 内容 |
|---|---|
| `cache/last_known_good_snapshot.json` | 最近一次完整快照，数据源刷新失败时用于回退。 |
| `cache/character_summary.json` | 角色摘要（名称、星级、职业、位置、词缀），卡池时间轴与公招计算共用，24 小时后或出现未收录干员时重新获取。 |
| `cache/*.json` 其余文件 | 各数据源的原始数据缓存，实时请求失败时兜底。 |
| `render/` | 最终日报图、帮助图和字体子集缓存。 |
| `assets/` | 网络图片及其缩放结果。 |
| `subscriptions/` | 活动订阅与干员蹲池记录。 |

从 v1.2.2 及更早版本升级后，`cache/snapshot.json` 与 `cache/snapshot-degraded.json` 不再写入，可手动删除。

数据源请求启用 gzip/deflate 压缩传输；单个响应上限 32 MB 按解压后的大小计算。

## 数据来源

- [PRTS Wiki](https://prts.wiki)：首页今日信息、活动详情、卡池表格、干员资料与图片。
- [anything-ics](https://github.com/SmallZombie/anything-ics)：活动时间与干员生日。
- [Torappu / Arknights Asset Storage](https://torappu.prts.wiki/gamedata/latest/excel/gacha_table.json)：最新卡池开关时间、规则类型、卡池 ID 和公开招募名单；角色表用于 UP 干员名称与公招词条。
- [ArknightsGachaData](https://github.com/s-yh-china/ArknightsGachaData)：卡池时间、类型和 ID。
- [PRTS Gacha Server Data](https://weedy.prts.wiki/)：补全卡池六星 UP 信息。
