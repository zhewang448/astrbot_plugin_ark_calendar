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
)


def _enabled_names(plugin: Any) -> set[str]:
    configured = plugin.service.value("ai", "enabled_tools", list(TOOL_NAMES))
    if not isinstance(configured, list):
        return set(TOOL_NAMES)
    return {str(name).strip() for name in configured if str(name).strip()} & set(TOOL_NAMES)


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
        _tool("ark_calendar_subscriptions", "查询当前用户在当前会话中的活动和卡池订阅。", {}, [], subscriptions),
        _tool("ark_calendar_status", "查询数据源刷新质量、缓存使用情况和来源状态。", {}, [], status),
        _tool("ark_calendar_bilibili", "查询官方 B 站动态的文字摘要和链接。", {"limit": {"type": "integer", "description": "最多返回条数"}}, [], bilibili),
    ]
    return ToolSet([tool for tool in tools if tool.name in enabled_names])
