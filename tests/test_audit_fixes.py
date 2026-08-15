from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from core.models import CalendarSnapshot, TimelineItem
from core.recruitment_calculator import RecruitmentCalculator
from core.render_cache import CalendarImageCache, validate_rendered_image
from core.subscription import SubscriptionManager


def _snapshot() -> CalendarSnapshot:
    now = datetime.now().astimezone()
    return CalendarSnapshot(
        generated_at=now.isoformat(),
        calendar_date=now.date().isoformat(),
        timeline_start=now.isoformat(),
        timeline_end=(now + timedelta(days=30)).isoformat(),
    )


def test_jpeg_render_validation_and_cache(tmp_path: Path):
    jpeg = b"\xff\xd8\xff" + b"fake-jpeg-payload"
    validate_rendered_image(jpeg, "jpeg")
    cache = CalendarImageCache(tmp_path)
    snapshot = _snapshot()
    image = cache.store(
        jpeg,
        snapshot,
        {"render_image_type": "jpeg"},
        max_age_minutes=30,
        keep_count=2,
    )
    assert image.suffix == ".jpg"
    assert cache.lookup(snapshot, {"render_image_type": "jpeg"}) == image


def test_recruitment_keeps_robot_and_excludes_six_star_from_normal_tags():
    calculator = RecruitmentCalculator([
        {"name": "小车", "rarity": 1, "tags": ["支援机械"]},
        {"name": "五星", "rarity": 5, "tags": ["输出"]},
        {"name": "六星", "rarity": 6, "tags": ["输出"]},
    ])
    normal = calculator._match_operators(["输出"])
    assert {item["name"] for item in normal} == {"五星"}
    assert calculator._match_operators(["支援机械"])[0]["name"] == "小车"
    assert [item["name"] for item in calculator._match_operators(["资深干员"])] == ["五星"]
    assert [item["name"] for item in calculator._match_operators(["高级资深干员"])] == ["六星"]


def test_subscription_reminder_uses_saved_record_when_item_left_snapshot(tmp_path: Path):
    manager = SubscriptionManager(tmp_path, logger=SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None))
    now = datetime.now().astimezone()
    item = TimelineItem(
        id="long-1", name="长期活动", category="event", item_type="event",
        start=(now - timedelta(days=2)).isoformat(), end=(now + timedelta(hours=23)).isoformat(),
    )
    manager.add_subscription(item, "u", "platform:Group:1", "00:00")
    pending = manager.get_pending_reminders(_snapshot())
    assert len(pending) == 1
    assert pending[0][0].item_name == "长期活动"
