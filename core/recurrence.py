from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_LIMIT = 30

_SCOPES = {
    "": ("六星干员", {6}, {"标准寻访", "中坚寻访"}),
    "六星": ("六星干员", {6}, {"标准寻访", "中坚寻访"}),
    "6星": ("六星干员", {6}, {"标准寻访", "中坚寻访"}),
    "五星": ("五星干员", {5}, {"标准寻访", "中坚寻访"}),
    "5星": ("五星干员", {5}, {"标准寻访", "中坚寻访"}),
    "标准": ("标准寻访", {5, 6}, {"标准寻访"}),
    "中坚": ("中坚寻访", {5, 6}, {"中坚寻访"}),
    "全部": ("五星、六星干员", {5, 6}, {"标准寻访", "中坚寻访"}),
}


def parse_display_limit(value: object) -> int | None:
    """解析排行榜显示数量；``all``（大小写无关）表示不截断。"""
    text = str(value or "").strip()
    if text.casefold() == "all":
        return None
    if text.isdecimal() and int(text) > 0:
        return int(text)
    raise ValueError("显示数量需为正整数或 all")


def parse_recurrence_query(
    argument_text: str,
    *,
    default_limit: int | None,
) -> tuple[str, int | None]:
    """解析 ``[范围] [数量/all]``，允许单独传数量。"""
    parts = argument_text.strip().split()
    if len(parts) > 2:
        raise ValueError("用法：/方舟未复刻 [六星/五星/标准/中坚/全部] [数量/all]")
    if not parts:
        return "", default_limit
    if len(parts) == 2:
        return parts[0], parse_display_limit(parts[1])
    try:
        return "", parse_display_limit(parts[0])
    except ValueError:
        return parts[0], default_limit


def build_recurrence_report(
    records: list[dict[str, Any]],
    now: datetime,
    scope: str = "",
    *,
    limit: int | None = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """把 PRTS 历史记录转换为“最近一次出率提升结束后”的排行榜。"""
    normalized_scope = scope.strip().replace("★", "星")
    try:
        title, rarities, pool_types = _SCOPES[normalized_scope]
    except KeyError as exc:
        raise ValueError("仅支持：六星、五星、标准、中坚、全部") from exc

    today = now.astimezone(CN_TZ).date()
    rows = []
    for item in records:
        if item.get("rarity") not in rarities or item.get("pool_type") not in pool_types:
            continue
        try:
            release_date = date.fromisoformat(str(item["release_date"]))
            rate_up_end = date.fromisoformat(str(item["rate_up_end"]))
        except (KeyError, TypeError, ValueError):
            continue
        ongoing = bool(item.get("rate_up_ongoing")) and rate_up_end >= today
        days = max(0, (today - rate_up_end).days)
        shop_end = _date_or_none(item.get("shop_end"))
        rows.append({
            "name": str(item.get("name", "") or ""),
            "rarity": int(item["rarity"]),
            "pool_type": str(item["pool_type"]),
            "release_date": release_date.isoformat(),
            "rate_up_end": rate_up_end.isoformat(),
            "rate_up_days": days,
            "rate_up_text": "进行中" if ongoing else f"{days} 天",
            "rate_up_ongoing": ongoing,
            "rate_up_count": max(0, int(item.get("rate_up_count", 0) or 0)),
            "shop_end": shop_end.isoformat() if shop_end else "",
            "shop_days": max(0, (today - shop_end).days) if shop_end else None,
            "shop_count": max(0, int(item.get("shop_count", 0) or 0)),
        })

    # 进行中的卡池不属于“未复刻”状态，固定排在已结束记录之后。
    rows.sort(key=lambda row: (
        row["rate_up_ongoing"],
        -row["rate_up_days"],
        row["release_date"],
        row["name"],
    ))
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("显示数量需为正整数或 all")
    selected = rows if limit is None else rows[:limit]
    for index, row in enumerate(selected, 1):
        row["rank"] = index
        row["shop_text"] = (
            f"{row['shop_end']} / {row['shop_days']} 天 / {row['shop_count']} 次"
            if row["shop_end"] else "尚未进店"
        )
        row["highlight"] = not row["rate_up_ongoing"] and row["rate_up_days"] >= 120

    return {
        "title": title,
        "scope": normalized_scope or "六星",
        "date": today.isoformat(),
        "rows": selected,
        "total": len(rows),
        "ongoing": sum(1 for row in rows if row["rate_up_ongoing"]),
        "max_days": max((row["rate_up_days"] for row in rows if not row["rate_up_ongoing"]), default=0),
    }


def _date_or_none(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
