from __future__ import annotations

from typing import Any

VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def normalize_weekdays(values: Any) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    invalid: list[str] = []
    for value in values if isinstance(values, list) else []:
        weekday = str(value).strip().lower()
        if weekday in VALID_WEEKDAYS:
            if weekday not in normalized:
                normalized.append(weekday)
        elif weekday:
            invalid.append(weekday)
    return normalized, invalid


def parse_schedule_times(values: Any) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    invalid: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        try:
            hour_text, minute_text = text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (TypeError, ValueError):
            if text:
                invalid.append(text)
            continue
        formatted = f"{hour:02d}:{minute:02d}"
        if formatted not in normalized:
            normalized.append(formatted)
    return normalized, invalid
