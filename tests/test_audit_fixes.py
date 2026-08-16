import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from core.models import CalendarSnapshot, TimelineItem
from core.recruitment_calculator import RecruitmentCalculator, format_result
from core.render_cache import CalendarImageCache, validate_rendered_image
from core.renderer import CalendarRenderer
from core.subscription import SubscriptionManager
from sources.recruitment import RecruitmentSource


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


def test_recruitment_removes_obsolete_high_air_tag():
    calculator = RecruitmentCalculator([])
    assert calculator.normalize_tag("新手") == "新手"
    assert calculator.normalize_tag("高空") is None
    assert calculator.normalize_tag("对空") is None


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


def test_subscription_syncs_changed_end_time_and_resets_notification(tmp_path: Path):
    logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    manager = SubscriptionManager(tmp_path, logger=logger)
    now = datetime.now().astimezone()
    original = TimelineItem(
        id="event-1", name="延期活动", category="event", item_type="event",
        start=(now - timedelta(days=3)).isoformat(), end=(now + timedelta(hours=6)).isoformat(),
    )
    manager.add_subscription(original, "u", "platform:Group:1", "00:00")
    manager.mark_notified(manager.get_user_subscriptions("u")[0])
    updated = TimelineItem(
        id="event-1", name="延期活动", category="event", item_type="event",
        start=original.start, end=(now + timedelta(hours=30)).isoformat(),
    )
    snapshot = _snapshot()
    snapshot.events = [updated]

    pending = manager.get_pending_reminders(snapshot)

    stored = manager.get_user_subscriptions("u")[0]
    assert stored.end_time == updated.end
    assert stored.notified is False
    assert len(pending) == 1


def test_cleanup_syncs_extended_end_before_expiring_subscription(tmp_path: Path):
    logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    manager = SubscriptionManager(tmp_path, logger=logger)
    now = datetime.now().astimezone()
    original = TimelineItem(
        id="延期", name="延期活动", category="event", item_type="event",
        start=(now - timedelta(days=3)).isoformat(), end=(now - timedelta(hours=1)).isoformat(),
    )
    manager.add_subscription(original, "u", "platform:Group:1", "00:00")
    updated = TimelineItem(
        id=original.id, name=original.name, category=original.category, item_type=original.item_type,
        start=original.start, end=(now + timedelta(days=2)).isoformat(),
    )
    snapshot = _snapshot()
    snapshot.events = [updated]

    assert manager.cleanup_expired(snapshot) == 0
    stored = manager.get_user_subscriptions("u")[0]
    assert stored.end_time == updated.end


def test_help_subscription_items_include_long_term_events():
    now = datetime.now().astimezone()
    snapshot = _snapshot()
    snapshot.long_term_events = [TimelineItem(
        id="long-1", name="长期活动", category="event", item_type="event",
        start=(now - timedelta(days=1)).isoformat(), end=(now + timedelta(days=10)).isoformat(),
    )]
    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.COLORS = {"event": "#000000"}

    assert [item["name"] for item in renderer.subscribable_items(snapshot)] == ["长期活动"]


def test_help_page_forces_png_even_when_calendar_uses_jpeg():
    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.help_template = "help"
    renderer.service = SimpleNamespace(plugin_version="test")
    renderer._help_hero = lambda: _empty_async("")
    renderer._static_assets = lambda _charset: _empty_async({})
    renderer._render_options = lambda: {"type": "jpeg", "timeout": 1000}

    captured = {}

    async def fake_render(_template, _data, options):
        captured.update(options)
        return b"rendered"

    renderer._html_render = fake_render
    result = asyncio.run(renderer.help_page(_snapshot(), [], []))

    assert result == b"rendered"
    assert captured["type"] == "png"


def test_dynamic_and_recruitment_cards_use_png_and_keep_template_data():
    class Assets:
        async def data_uri(self, path, **_kwargs):
            return f"data:image/png;base64,{path}"

    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.bilibili_template = "dynamic"
    renderer.recruitment_template = "recruitment"
    renderer.service = SimpleNamespace(assets=Assets())
    renderer._static_assets = lambda _charset: _empty_async({"font": "font"})
    renderer._card_render_options = lambda: {"type": "png"}
    captured = []

    async def fake_render(template, data, options):
        captured.append((template, data, options))
        return b"rendered"

    renderer._html_render = fake_render
    dynamic = {
        "title": "双图动态", "description_text": "内容", "dynamic_type": "image",
        "images": ["one", "two"], "cached_images": ["one", "two"],
    }

    assert asyncio.run(renderer.bilibili_dynamic(dynamic, include_images=True)) == b"rendered"
    assert captured[0][1]["images"] == ["data:image/png;base64,one", "data:image/png;base64,two"]
    assert captured[0][2]["type"] == "png"

    assert asyncio.run(renderer.recruitment_result([
        {"tags": ["输出"], "operators": [{"name": "干员", "rarity": 4}], "min_rarity": 4},
    ], ["输出"])) == b"rendered"
    assert captured[1][1]["rows"][0]["recommended"] is True
    assert captured[1][2]["type"] == "png"


async def _empty_async(value):
    return value


def test_recruitment_source_uses_gacha_detail_allowlist():
    class FakeHttp:
        async def json(self, url: str):
            if url.endswith("gacha_table.json"):
                return {
                    "recruitDetail": (
                        "说明文字\r\n★\\n<@rc.eml>小车</>\r\n"
                        "--------------------\r\n"
                        "★★★★\\n<@rc.eml>公招干员</>\r\n"
                        "--------------------\r\n"
                    )
                }
            return {
                "robot": {
                    "name": "小车", "rarity": "TIER_1", "tagList": ["支援机械"],
                    "profession": "SPECIALIST", "position": "MELEE",
                    "itemObtainApproach": "招募",
                },
                "recruitable": {
                    "name": "公招干员", "rarity": "TIER_4", "tagList": ["输出"],
                    "profession": "WARRIOR", "position": "MELEE",
                    "itemObtainApproach": "招募",
                },
                "not_in_pool": {
                    "name": "普通干员", "rarity": "TIER_4", "tagList": ["输出"],
                    "profession": "WARRIOR", "position": "MELEE",
                    "itemObtainApproach": "招募",
                },
            }

    pool = asyncio.run(RecruitmentSource(FakeHttp()).get_recruitment_pool())
    assert {item["name"] for item in pool["characters"]} == {"小车", "公招干员"}


def test_recruitment_source_retries_after_temporary_fetch_failure():
    class FakeHttp:
        def __init__(self):
            self.calls = 0

        async def json(self, _url: str):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary failure")
            return {"char": {"name": "干员"}}

    source = RecruitmentSource(FakeHttp())
    assert asyncio.run(source._fetch_character_table()) == {}
    assert asyncio.run(source._fetch_character_table()) == {"char": {"name": "干员"}}


def test_recruitment_aliases_sorting_and_full_output():
    calculator = RecruitmentCalculator([
        {"name": "甲", "rarity": 4, "tags": ["输出"]},
        {"name": "乙", "rarity": 4, "tags": ["输出"]},
        {"name": "丙", "rarity": 4, "tags": ["输出", "生存"]},
    ])
    assert calculator.normalize_tag("快活") == "快速复活"
    assert calculator.normalize_tag("DPS") == "输出"
    assert calculator.normalize_tag("奶") == "治疗"
    assert calculator.normalize_tag("术士") == "术师干员"

    results = calculator.calculate(["输出", "生存"])
    assert results[0]["tags"] == ["输出", "生存"]
    assert results[1]["tags"] == ["生存"]
    assert results[2]["tags"] == ["输出"]

    all_names = [{"name": f"干员{i}", "rarity": 4} for i in range(10)]
    text = format_result(
        [{"tags": ["输出"], "operators": all_names, "min_rarity": 4, "has_senior": False, "has_top_senior": False}],
        selected_tags=["输出"],
    )
    assert "干员9" in text
    assert "…共" not in text
