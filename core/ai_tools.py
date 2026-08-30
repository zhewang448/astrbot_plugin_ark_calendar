"""AstrBot LLM Tool 定义：只返回结构化文本，不发送图片。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from astrbot.core.agent.tool import FunctionTool, ToolSet

from .ai_context import compact_json_data, operator_data, snapshot_data
from .recruitment_calculator import RecruitmentCalculator

TOOL_NAMES = (
    "ark_calendar_today",
    "ark_calendar_events",
    "ark_calendar_birthday",
    "ark_calendar_recruitment",
    "ark_calendar_recurrence",
    "ark_calendar_subscriptions",
    "ark_calendar_status",
    "ark_calendar_bilibili",
    "ark_calendar_operator_history",
    "ark_calendar_subscribe",
    "ark_calendar_unsubscribe",
)

MUTATION_TOOL_NAMES = {"ark_calendar_subscribe", "ark_calendar_unsubscribe"}
LEGACY_DEFAULT_TOOL_NAMES = set(TOOL_NAMES[:8])


def _enabled_names(plugin: Any) -> set[str]:
    configured = plugin.service.value("ai", "enabled_tools", list(TOOL_NAMES))
    if not isinstance(configured, list):
        return set(TOOL_NAMES)
    selected = {str(name).strip() for name in configured if str(name).strip()} & set(TOOL_NAMES)
    # 兼容新增可选函数前已经保存的“全部默认工具”列表。
    if selected == LEGACY_DEFAULT_TOOL_NAMES:
        return set(TOOL_NAMES)
    return selected


def _json(value: Any) -> str:
    return json.dumps(compact_json_data(value), ensure_ascii=False, separators=(",", ":"))


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], handler) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        handler=handler,
    )


def build_ai_tools(plugin: Any) -> ToolSet:
    max_items = plugin.service.int_value("ai", "max_items", 30, minimum=1, maximum=100)
    enabled_names = _enabled_names(plugin)
    allow_mutations = bool(plugin.service.value("ai", "allow_subscription_mutations", False))
    if not allow_mutations:
        enabled_names -= MUTATION_TOOL_NAMES

    async def today(event):
        snapshot, outcome = await plugin.service.snapshot_with_outcome()
        data = snapshot_data(snapshot, outcome, max_items=max_items)
        return _json({"calendar": data["calendar_date"], "today_info": data["today_info"], "birthdays": data["today_birthdays"], "quality": data["refresh_quality"]})

    async def events(event, query: str = "", limit: int = 20):
        snapshot, outcome = await plugin.service.snapshot_with_outcome()
        data = snapshot_data(snapshot, outcome, max_items=max_items)
        needle = str(query or "").strip().casefold()
        items = data["events"] + data["gacha_pools"] + data["long_term_events"]
        if needle:
            items = [item for item in items if needle in str(item.get("name", "")).casefold() or needle in str(item.get("detail", "")).casefold()]
        return _json({"items": items[: max(1, min(int(limit), max_items))], "quality": data["refresh_quality"], "as_of": data["generated_at"]})

    async def birthday(event, name: str):
        operator, candidates = await plugin.service.find_operator(name)
        return _json({"operator": operator_data(operator) if operator else None, "candidates": candidates})

    async def recruitment(event, tags: list[str]):
        if not plugin.recruitment_source:
            return _json({"error": "recruitment_source_uninitialized"})
        pool = await plugin.recruitment_source.get_recruitment_pool()
        calculator = RecruitmentCalculator(pool.get("characters", []))
        valid, invalid = calculator.normalize_tags([str(tag) for tag in tags])
        if invalid:
            return _json({"valid_tags": valid, "invalid_tags": invalid, "results": []})
        results = calculator.calculate(valid) if valid else []
        return _json({"valid_tags": valid, "results": results})

    async def recurrence(event, scope: str = ""):
        report = await plugin.service.recurrence_report(scope)
        return _json(report)

    async def operator_history(event, name: str):
        report = await plugin.service.recurrence_report("全部")
        needle = str(name or "").strip().casefold()
        rows = [row for row in report.get("rows", []) if needle in str(row.get("name", "")).casefold()]
        return _json({
            "query": name,
            "matches": [
                {
                    "name": row.get("name"),
                    "rarity": row.get("rarity"),
                    "pool_type": row.get("pool_type"),
                    "last_rate_up_end": row.get("rate_up_end"),
                    "rate_up_count": row.get("rate_up_count", 0),
                    "rate_up_ongoing": row.get("rate_up_ongoing", False),
                }
                for row in rows
            ],
            "as_of": report.get("date"),
        })

    async def subscribe(event, item_name: str, remind_time: str = "12:00", confirmed: bool = False):
        if not allow_mutations:
            return _json({"ok": False, "error": "subscription_mutations_disabled"})
        from .command_args import parse_hhmm

        name = str(item_name or "").strip()
        normalized_time = parse_hhmm(str(remind_time or "12:00"))
        if not name:
            return _json({"ok": False, "error": "item_name_required"})
        if normalized_time is None:
            return _json({"ok": False, "error": "invalid_remind_time", "expected": "HH:MM"})
        snapshot = await plugin.service.snapshot()
        matches = plugin._find_timeline_items(snapshot, name)
        if not matches and name.casefold() in {"下个卡池", "下一个卡池", "next pool"}:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            future = [item for item in snapshot.gacha_pools if item.start and item.start > now.isoformat()]
            matches = sorted(future, key=lambda item: item.start)[:1]
        if not matches:
            return _json({"ok": False, "error": "item_not_found", "query": name})
        if len(matches) > 1:
            return _json({"ok": False, "error": "multiple_matches", "candidates": [item.name for item in matches[:10]]})
        item = matches[0]
        preview = {"item_name": item.name, "item_type": item.category, "end_time": item.end, "remind_time": normalized_time}
        if not confirmed:
            return _json({"ok": False, "requires_confirmation": True, "action": "subscribe", **preview})
        user_id = str(event.message_obj.sender.user_id)
        session_id = event.unified_msg_origin
        subscription = plugin.subscription_manager.add_subscription(item, user_id, session_id, normalized_time)
        plugin._ensure_subscription_scheduler()
        return _json({"ok": True, "action": "subscribed", "item_name": subscription.item_name, "end_time": subscription.end_time, "remind_time": subscription.remind_time, "remind_at": subscription.remind_at})

    async def unsubscribe(event, item_name: str, confirmed: bool = False):
        if not allow_mutations:
            return _json({"ok": False, "error": "subscription_mutations_disabled"})
        name = str(item_name or "").strip()
        if not name:
            return _json({"ok": False, "error": "item_name_required"})
        user_id = str(event.message_obj.sender.user_id)
        session_id = event.unified_msg_origin
        records = plugin.subscription_manager.get_user_subscriptions(user_id, session_id)
        needle = name.casefold()
        matches = [item for item in records if needle in item.item_name.casefold() or item.item_name.casefold() in needle]
        if not matches:
            return _json({"ok": False, "error": "subscription_not_found", "query": name})
        if len(matches) > 1:
            return _json({"ok": False, "error": "multiple_matches", "candidates": [item.item_name for item in matches[:10]]})
        item = matches[0]
        if not confirmed:
            return _json({"ok": False, "requires_confirmation": True, "action": "unsubscribe", "item_name": item.item_name, "end_time": item.end_time})
        removed = plugin.subscription_manager.remove_subscription(item.item_id, user_id, session_id)
        if removed:
            plugin._schedule_next_subscription_reminder()
        return _json({"ok": removed, "action": "unsubscribed" if removed else "subscription_not_found", "item_name": item.item_name})

    async def subscriptions(event):
        user_id = str(event.message_obj.sender.user_id)
        session_id = event.unified_msg_origin
        records = plugin.subscription_manager.get_user_subscriptions(user_id, session_id)
        return _json({"subscriptions": [
            {"item_name": item.item_name, "item_type": item.item_type, "end_time": item.end_time, "remind_time": item.remind_time, "remind_at": item.remind_at, "notified": item.notified}
            for item in records
        ]})

    async def status(event):
        snapshot, outcome = await plugin.service.snapshot_with_outcome()
        return _json({"quality": outcome.quality, "error": outcome.error, "used_cache": outcome.used_cache, "finished_at": outcome.finished_at, "sources": [asdict(item) for item in snapshot.source_states]})

    async def bilibili(event, limit: int = 10):
        if not plugin.bilibili_manager:
            return _json({"error": "bilibili_uninitialized", "items": []})
        dynamics = await plugin.bilibili_manager.query_list(max(1, min(int(limit), max_items)))
        items = []
        for item in dynamics:
            items.append({key: item.get(key) for key in ("id", "title", "link", "pub_date", "dynamic_type", "description_text") if item.get(key) not in (None, "")})
        return _json({"items": items})

    tools = [
        _tool("ark_calendar_today", "查询今日作战、芯片、提醒和生日。返回 JSON 文本，不包含图片。", {}, [], today),
        _tool("ark_calendar_events", "查询活动和卡池时间轴，可按名称筛选。返回 JSON 文本，不包含图片。", {"query": {"type": "string", "description": "活动或卡池名称关键词，可留空"}, "limit": {"type": "integer", "description": "最多返回条数"}}, [], events),
        _tool("ark_calendar_birthday", "查询指定干员生日和基础资料。", {"name": {"type": "string", "description": "干员名称"}}, ["name"], birthday),
        _tool("ark_calendar_recruitment", "根据公开招募标签计算可招募干员。", {"tags": {"type": "array", "items": {"type": "string"}, "description": "游戏内公招标签列表"}}, ["tags"], recruitment),
        _tool("ark_calendar_recurrence", "查询干员未复刻排行，可填写六星、五星、标准、中坚或数量。", {"scope": {"type": "string", "description": "排行筛选条件，可留空"}}, [], recurrence),
        _tool("ark_calendar_operator_history", "查询指定干员最近一次 UP 结束日期和累计 UP 次数，不包含进店历史。", {"name": {"type": "string", "description": "干员名称"}}, ["name"], operator_history),
        _tool("ark_calendar_subscriptions", "查询当前用户在当前会话中的活动和卡池订阅。", {}, [], subscriptions),
        _tool("ark_calendar_status", "查询数据源刷新质量、缓存使用情况和来源状态。", {}, [], status),
        _tool("ark_calendar_bilibili", "查询官方 B 站动态的文字摘要和链接。", {"limit": {"type": "integer", "description": "最多返回条数"}}, [], bilibili),
        _tool("ark_calendar_subscribe", "添加活动或卡池订阅。首次调用只返回确认预览，确认后再次传 confirmed=true 才会写入。", {"item_name": {"type": "string", "description": "活动或卡池名称"}, "remind_time": {"type": "string", "description": "HH:MM，默认 12:00"}, "confirmed": {"type": "boolean", "description": "用户已明确确认时传 true"}}, ["item_name"], subscribe),
        _tool("ark_calendar_unsubscribe", "取消当前用户在当前会话中的订阅。首次调用只返回确认预览，确认后再次传 confirmed=true 才会写入。", {"item_name": {"type": "string", "description": "活动或卡池名称"}, "confirmed": {"type": "boolean", "description": "用户已明确确认时传 true"}}, ["item_name"], unsubscribe),
    ]
    return ToolSet([tool for tool in tools if tool.name in enabled_names])
