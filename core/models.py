from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SourceState:
    name: str
    ok: bool = True
    updated_at: str = ""
    message: str = ""
    event_key: str = ""
    status: str = "fresh"
    used_cache: bool = False


@dataclass(slots=True)
class Operator:
    name: str
    birthday_month: int | None = None
    birthday_day: int | None = None
    profession: str = ""
    rarity: int | None = None
    avatar: str = ""
    is_limited: bool = False


@dataclass(slots=True)
class BirthdayGroup:
    month: int
    day: int
    operators: list[Operator] = field(default_factory=list)


@dataclass(slots=True)
class TimelineItem:
    id: str
    name: str
    category: str
    item_type: str
    start: str
    end: str
    image: str = ""
    images: list[str] = field(default_factory=list)
    detail: str = ""
    six_star_up: list[str] = field(default_factory=list)
    weighted_up: list[str] = field(default_factory=list)
    exchange_end: str = ""
    is_long_term: bool = False


@dataclass(slots=True)
class TodayInfo:
    supplies: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    alerts: list[dict[str, str]] = field(default_factory=list)
    resource_schedule: list[dict[str, Any]] = field(default_factory=list)
    chip_schedule: list[dict[str, Any]] = field(default_factory=list)
    voucher_exchange: list[dict[str, Any]] = field(default_factory=list)
    new_skins: list[dict[str, Any]] = field(default_factory=list)
    new_modules: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CalendarSnapshot:
    generated_at: str
    calendar_date: str
    timeline_start: str
    timeline_end: str
    today_info: TodayInfo = field(default_factory=TodayInfo)
    today_birthdays: list[Operator] = field(default_factory=list)
    upcoming_birthdays: list[BirthdayGroup] = field(default_factory=list)
    recent_operators: list[Operator] = field(default_factory=list)
    events: list[TimelineItem] = field(default_factory=list)
    gacha_pools: list[TimelineItem] = field(default_factory=list)
    long_term_events: list[TimelineItem] = field(default_factory=list)
    source_states: list[SourceState] = field(default_factory=list)
    schema_version: int = 1
    data_config_hash: str = ""
    refresh_quality: str = "fresh"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalendarSnapshot":
        return cls(
            generated_at=data["generated_at"],
            calendar_date=data["calendar_date"],
            timeline_start=data["timeline_start"],
            timeline_end=data["timeline_end"],
            today_info=TodayInfo(**data.get("today_info", {})),
            today_birthdays=[Operator(**x) for x in data.get("today_birthdays", [])],
            upcoming_birthdays=[BirthdayGroup(x["month"], x["day"], [Operator(**op) for op in x.get("operators", [])]) for x in data.get("upcoming_birthdays", [])],
            recent_operators=[Operator(**x) for x in data.get("recent_operators", [])],
            events=[TimelineItem(**x) for x in data.get("events", [])],
            gacha_pools=[TimelineItem(**x) for x in data.get("gacha_pools", [])],
            long_term_events=[TimelineItem(**x) for x in data.get("long_term_events", [])],
            source_states=[SourceState(**x) for x in data.get("source_states", [])],
            schema_version=int(data.get("schema_version", 1) or 1),
            data_config_hash=str(data.get("data_config_hash", "") or ""),
            refresh_quality=str(data.get("refresh_quality", "fresh") or "fresh"),
        )


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
