# AI 调用与结构化数据

本插件可以向 AstrBot 注册结构化 LLM Tool，让聊天模型用自然语言查询日历数据。查询工具返回经过裁剪的 JSON 文本，不返回日报图片；现有斜杠命令仍按原行为发送图片。

## 配置

在 WebUI 的“AI 工具与结构化数据”中设置：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| 启用 AI 工具 | 关闭 | 开启后注册下方只读工具。 |
| 启用的 AI 工具 | 全部 | 可单独选择要注册的函数；默认全部开启，取消全部后不注册函数。 |
| 允许 AI 修改订阅 | 关闭 | 单独控制添加/取消活动和卡池订阅；开启后仍需工具调用传入 `confirmed=true`。 |
| AI 单次最多返回条数 | 30 | 限制列表长度，避免上下文过大。 |

插件重载时会先移除同名旧工具，再按当前配置注册。关闭配置不会影响普通命令。

从旧版本升级时，如果保存的工具列表仍是旧版完整默认列表，插件会自动补齐新增函数；明确选择过子集的配置保持原样。

## 可用工具

| 工具名 | 用途 |
|---|---|
| `ark_calendar_today` | 今日作战、芯片、提醒和生日 |
| `ark_calendar_events` | 活动与卡池时间轴，可按名称筛选 |
| `ark_calendar_birthday` | 干员生日与基础资料 |
| `ark_calendar_recruitment` | 根据公招标签计算结果 |
| `ark_calendar_recurrence` | 未复刻排行 |
| `ark_calendar_subscriptions` | 当前用户、当前会话的订阅列表 |
| `ark_calendar_status` | 数据源状态、刷新质量和缓存状态 |
| `ark_calendar_bilibili` | 官方 B 站动态的文字摘要和链接 |
| `ark_calendar_operator_history` | 指定干员最近一次 UP 和累计 UP 次数，不包含进店历史 |
| `ark_calendar_subscribe` | 添加活动或卡池订阅（需配置允许并二次确认） |
| `ark_calendar_unsubscribe` | 取消活动或卡池订阅（需配置允许并二次确认） |

## 数据来源与裁剪

工具通过 `CalendarService`、`SubscriptionManager` 和 `BilibiliDynamicManager` 读取运行时缓存，主要来源包括：

- `cache/snapshot.json` 与 `last_known_good_snapshot.json`
- `cache/gacha_pools.json`、`recurrence_overview.json`、`event_detail_*.json`
- `subscriptions/subscriptions.json`
- `cache/bilibili_dynamic_state.json`

AI 上下文会附带快照时间、刷新质量和来源状态，并移除图片、Base64、文件路径、原始 HTML、用户 ID、会话 SID 等字段。数据源降级时，模型应在回答中说明结果可能来自缓存。

## 示例

用户可以直接询问：

```text
今天有哪些资源可以刷？
未来七天哪些活动或卡池即将结束？
泥岩的生日是什么时候？
近卫、输出、生存这几个标签能招到谁？
最近有哪些六星干员很久没有复刻？
我当前会话订阅了哪些内容？
```

## 边界

订阅写操作默认关闭。开启“允许 AI 修改订阅”并勾选对应函数后，工具第一次调用只返回匹配项和确认预览，只有用户明确确认、且后续调用传入 `confirmed=true` 才会执行现有订阅方法。强制刷新等管理员操作仍不开放给 AI。
