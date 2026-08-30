import json
from datetime import datetime

from core.ai_context import snapshot_data
from core.models import CalendarSnapshot, RefreshOutcome, TimelineItem, TodayInfo


def test_snapshot_data_is_text_safe_and_drops_media_fields():
    snapshot = CalendarSnapshot(
        generated_at="2026-08-30T12:00:00+08:00",
        calendar_date="2026-08-30",
        timeline_start="2026-08-29T00:00:00+08:00",
        timeline_end="2026-09-26T00:00:00+08:00",
        today_info=TodayInfo(resource_schedule=[{"name": "龙门币", "image": "data:image/png;base64,secret"}]),
        events=[TimelineItem("e1", "活动", "event", "活动", "2026-08-30", "2026-09-01", image="secret")],
    )
    result = snapshot_data(snapshot, RefreshOutcome(quality="fresh"))
    encoded = json.dumps(result, ensure_ascii=False)
    assert "data:image" not in encoded
    assert "secret" not in encoded
    assert result["events"][0]["name"] == "活动"
    assert result["refresh_outcome"]["quality"] == "fresh"


def test_snapshot_data_converts_datetime_values_to_iso_text():
    snapshot = CalendarSnapshot(
        generated_at="2026-08-30T12:00:00+08:00",
        calendar_date="2026-08-30",
        timeline_start="2026-08-29T00:00:00+08:00",
        timeline_end="2026-09-26T00:00:00+08:00",
        today_info=TodayInfo(alerts=[{"time": datetime(2026, 8, 30, 12, 0)}]),
    )
    result = snapshot_data(snapshot)
    assert result["today_info"]["alerts"][0]["time"] == "2026-08-30T12:00:00"
