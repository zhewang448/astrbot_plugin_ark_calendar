"""面向 LLM 的结构化日历上下文与字段裁剪。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import CalendarSnapshot, Operator, RefreshOutcome, TimelineItem

AI_CONTEXT_SCHEMA_VERSION = 1
_MEDIA_KEYS = {
    "image",
    "images",
    "detail_image",
    "avatar",
    "cached_images",
    "image_url",
    "thumbnail",
}


def _strip_media(value: Any) -> Any:
    """递归移除图片、文件路径等不适合进入文本上下文的字段。"""
    if isinstance(value, dict):
        return {
            str(key): _strip_media(item)
            for key, item in value.items()
            if str(key) not in _MEDIA_KEYS
        }
    if isinstance(value, list):
        return [_strip_media(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_media(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _strip_media(asdict(value))
    return value


def operator_data(operator: Operator | dict[str, Any]) -> dict[str, Any]:
    raw = _strip_media(asdict(operator) if is_dataclass(operator) else operator)
    return {
        key: raw.get(key)
        for key in ("name", "birthday_month", "birthday_day", "profession", "rarity", "is_limited")
        if key in raw
    }


def timeline_item_data(item: TimelineItem | dict[str, Any]) -> dict[str, Any]:
    raw = _strip_media(asdict(item) if is_dataclass(item) else item)
    return {
        key: raw.get(key)
        for key in (
            "id", "name", "category", "item_type", "start", "end", "detail",
            "six_star_up", "weighted_up", "exchange_end", "is_long_term",
        )
        if key in raw and raw.get(key) not in (None, "", [], {})
    }


def snapshot_data(
    snapshot: CalendarSnapshot,
    outcome: RefreshOutcome | None = None,
    *,
    max_items: int = 30,
) -> dict[str, Any]:
    """将快照压缩为稳定、可审计且不含媒体字段的 AI 输入。"""
    max_items = max(1, min(int(max_items), 100))
    today = _strip_media(asdict(snapshot.today_info))
    sources = [
        {
            "name": state.name,
            "ok": state.ok,
            "status": state.status,
            "updated_at": state.updated_at,
            "message": state.message,
            "used_cache": state.used_cache,
        }
        for state in snapshot.source_states
    ]
    result = {
        "schema_version": AI_CONTEXT_SCHEMA_VERSION,
        "calendar_date": snapshot.calendar_date,
        "generated_at": snapshot.generated_at,
        "timeline_start": snapshot.timeline_start,
        "timeline_end": snapshot.timeline_end,
        "refresh_quality": snapshot.refresh_quality,
        "today_info": today,
        "today_birthdays": [operator_data(item) for item in snapshot.today_birthdays[:max_items]],
        "upcoming_birthdays": [
            {
                "month": group.month,
                "day": group.day,
                "operators": [operator_data(item) for item in group.operators[:max_items]],
            }
            for group in snapshot.upcoming_birthdays[:max_items]
        ],
        "recent_operators": [operator_data(item) for item in snapshot.recent_operators[:max_items]],
        "events": [timeline_item_data(item) for item in snapshot.events[:max_items]],
        "gacha_pools": [timeline_item_data(item) for item in snapshot.gacha_pools[:max_items]],
        "long_term_events": [timeline_item_data(item) for item in snapshot.long_term_events[:max_items]],
        "sources": sources,
    }
    if outcome is not None:
        result["refresh_outcome"] = {
            "quality": outcome.quality,
            "error": outcome.error,
            "used_cache": outcome.used_cache,
            "finished_at": outcome.finished_at,
        }
    return _strip_media(result)


def compact_json_data(value: Any) -> Any:
    """供其他 AI 数据源复用的通用裁剪入口。"""
    return _strip_media(value)
