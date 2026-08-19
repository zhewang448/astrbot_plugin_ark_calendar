import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from core.models import CalendarSnapshot, TimelineItem, parse_iso
from core.command_args import split_name_and_time
from core.recruitment_calculator import (
    RECRUITMENT_EASTER_EGG_MESSAGE,
    RecruitmentCalculator,
    format_result,
    is_recruitment_easter_egg_query,
)
from core.render_cache import CalendarImageCache, validate_rendered_image
from core.renderer import CalendarRenderer
from core.subscription import SubscriptionManager
from sources.recruitment import RecruitmentSource


def subscription_timezone():
    return ZoneInfo("Asia/Shanghai")


def test_recruitment_easter_egg_accepts_all_and_star_case_insensitively():
    assert is_recruitment_easter_egg_query(" all ") is True
    assert is_recruitment_easter_egg_query("ALL") is True
    assert is_recruitment_easter_egg_query("*") is True
    assert is_recruitment_easter_egg_query("all 输出") is False
    assert "BV1y14y157MD" in RECRUITMENT_EASTER_EGG_MESSAGE


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


def test_subscription_persists_fixed_reminder_time(tmp_path: Path):
    manager = SubscriptionManager(tmp_path, logger=SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None))
    item = TimelineItem(
        id="fixed-1", name="固定活动", category="event", item_type="event",
        start="2026-08-18T04:00:00+08:00", end="2026-08-20T04:00:00+08:00",
    )
    subscription = manager.add_subscription(item, "u", "platform:Group:1", "09:30")
    assert subscription.remind_at == "2026-08-19T09:30:00+08:00"
    assert manager.get_next_reminder_at(datetime(2026, 8, 18, 0, tzinfo=subscription_timezone())) == parse_iso(subscription.remind_at)


def test_subscription_due_check_does_not_read_snapshot(tmp_path: Path):
    logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    manager = SubscriptionManager(tmp_path, logger=logger)
    item = TimelineItem(
        id="fixed-2", name="不变活动", category="event", item_type="event",
        start="2026-08-18T04:00:00+08:00", end="2026-08-20T04:00:00+08:00",
    )
    manager.add_subscription(item, "u", "platform:Group:1", "09:30")
    due = manager.get_due_reminders(datetime(2026, 8, 19, 10, tzinfo=subscription_timezone()))
    assert [subscription.item_name for subscription in due] == ["不变活动"]


def test_legacy_subscription_record_gets_fixed_reminder_time(tmp_path: Path):
    logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    manager = SubscriptionManager(tmp_path, logger=logger)
    manager.cache.save("subscriptions.json", {
        "fixed-legacy:u:platform:Group:1": {
            "item_id": "fixed-legacy",
            "item_name": "旧记录活动",
            "item_type": "event",
            "end_time": "2026-08-20T04:00:00+08:00",
            "user_id": "u",
            "session_id": "platform:Group:1",
            "remind_time": "09:30",
        }
    })
    subscriptions = manager.get_user_subscriptions("u")
    assert subscriptions[0].remind_at == "2026-08-19T09:30:00+08:00"
    assert manager.cache.load("subscriptions.json")["fixed-legacy:u:platform:Group:1"]["remind_at"] == "2026-08-19T09:30:00+08:00"


def test_subscription_rejects_invalid_time_without_treating_it_as_name():
    assert split_name_and_time("危机合约 25:99") == ("危机合约", None, True)
    assert split_name_and_time("危机合约 9:30") == ("危机合约", "09:30", False)
    assert split_name_and_time("活动:特别篇") == ("活动:特别篇", None, False)


def test_render_cache_expiry_starts_at_render_time(tmp_path: Path):
    cache = CalendarImageCache(tmp_path)
    snapshot = _snapshot()
    snapshot.generated_at = (datetime.now().astimezone() - timedelta(minutes=31)).isoformat()
    image = cache.store(
        b"\x89PNG\r\n\x1a\nvalid",
        snapshot,
        {"render_image_type": "png"},
        max_age_minutes=30,
        keep_count=2,
    )
    assert cache.lookup(snapshot, {"render_image_type": "png"}) == image


def test_cleanup_uses_saved_end_time_without_snapshot(tmp_path: Path):
    logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    manager = SubscriptionManager(tmp_path, logger=logger)
    original = TimelineItem(
        id="expired", name="已结束活动", category="event", item_type="event",
        start="2026-08-15T04:00:00+08:00", end="2026-08-16T04:00:00+08:00",
    )
    manager.add_subscription(original, "u", "platform:Group:1", "00:00")
    assert manager.cleanup_expired(datetime(2026, 8, 17, tzinfo=subscription_timezone())) == 1
    assert manager.get_user_subscriptions("u") == []


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


def test_pool_detail_cards_require_banner():
    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.COLORS = CalendarRenderer.COLORS
    now = datetime(2026, 8, 18, 12, tzinfo=subscription_timezone())
    start = "2026-08-18T04:00:00+08:00"
    end = "2026-08-20T04:00:00+08:00"
    without_banner = TimelineItem(
        id="without-banner", name="未公布标准卡池", category="gacha", item_type="未公布标准卡池",
        start=start, end=end, six_star_up=["能天使"],
    )
    with_banner = TimelineItem(
        id="with-banner", name="标准寻访", category="gacha", item_type="标准寻访",
        start=start, end=end, detail_image="data:image/webp;base64,banner",
    )
    rendered_without_banner = renderer._timeline(
        without_banner,
        datetime(2026, 8, 18, 0, tzinfo=subscription_timezone()),
        datetime(2026, 8, 25, 0, tzinfo=subscription_timezone()),
        now,
    )
    rendered_with_banner = renderer._timeline(
        with_banner,
        datetime(2026, 8, 18, 0, tzinfo=subscription_timezone()),
        datetime(2026, 8, 25, 0, tzinfo=subscription_timezone()),
        now,
    )

    assert rendered_without_banner["color"] == "#858b91"
    assert renderer._pool_details([rendered_without_banner, rendered_with_banner]) == [rendered_with_banner]


def test_core_selection_and_joint_operation_timeline_colors_are_distinct():
    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.COLORS = CalendarRenderer.COLORS
    start = datetime(2026, 8, 18, 0, tzinfo=subscription_timezone())
    end = datetime(2026, 8, 25, 0, tzinfo=subscription_timezone())
    core_selection = TimelineItem(
        id="core-selection", name="中坚甄选", category="gacha", item_type="中坚甄选",
        start=start.isoformat(), end=end.isoformat(),
    )
    joint_operation = TimelineItem(
        id="joint-operation", name="联合行动", category="gacha", item_type="联合行动",
        start=start.isoformat(), end=end.isoformat(),
    )

    core_color = renderer._timeline(core_selection, start, end, start)["color"]
    joint_color = renderer._timeline(joint_operation, start, end, start)["color"]

    assert core_color == "#3c6680"
    assert joint_color == "#b36d10"
    assert core_color != joint_color


def test_unpublished_pool_visibility_setting_filters_daily_report():
    renderer = CalendarRenderer.__new__(CalendarRenderer)
    start = "2026-08-18T04:00:00+08:00"
    end = "2026-08-20T04:00:00+08:00"
    pools = [
        TimelineItem(id="known", name="标准寻访", category="gacha", item_type="标准寻访", start=start, end=end),
        TimelineItem(id="unknown", name="未公布", category="gacha", item_type="未公布", start=start, end=end),
    ]

    class Service:
        def __init__(self, show):
            self.show = show

        def show_unpublished_pools(self):
            return self.show

        def value(self, *_args):
            return True

    renderer.service = Service(False)
    visible = renderer._visible_gacha_pools(pools)
    assert [item.name for item in visible] == ["标准寻访"]
    renderer.service = Service(True)
    visible = renderer._visible_gacha_pools(pools)
    assert [item.name for item in visible] == ["标准寻访", "未公布"]


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


def test_bilibili_long_images_keep_natural_height():
    template = (Path(__file__).parent.parent / "templates" / "bilibili_dynamic.html").read_text("utf-8")

    assert ".gallery img{display:block;width:100%;height:auto;min-height:0;max-height:none;" in template


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
    assert results[0]["tag_combinations"] == [["输出", "生存"], ["生存"]]
    assert results[1]["tags"] == ["输出"]

    all_names = [{"name": f"干员{i}", "rarity": 4} for i in range(10)]
    text = format_result(
        [{"tags": ["输出"], "operators": all_names, "min_rarity": 4, "has_senior": False, "has_top_senior": False}],
        selected_tags=["输出"],
    )
    assert "干员9" in text
    assert "…共" not in text


def test_recruitment_uses_nine_hour_rarity_floor():
    calculator = RecruitmentCalculator([
        {"name": "THRM-EX", "rarity": 1, "tags": ["爆发"]},
        {"name": "刻刀", "rarity": 4, "tags": ["爆发"]},
        {"name": "GALLUS²", "rarity": 1, "tags": ["削弱"]},
        {"name": "夜烟", "rarity": 4, "tags": ["削弱"]},
        {"name": "Lancet-2", "rarity": 1, "tags": ["治疗"]},
        {"name": "安赛尔", "rarity": 3, "tags": ["治疗"]},
        {"name": "夜刀", "rarity": 2, "tags": ["先锋干员"]},
        {"name": "芬", "rarity": 3, "tags": ["先锋干员"]},
        {"name": "黑角", "rarity": 2, "tags": ["新手"]},
    ])

    results = {result["tags"][0]: result for result in calculator.calculate(["爆发", "削弱", "治疗", "先锋干员", "新手"])}

    assert results["爆发"]["min_rarity"] == 4
    assert results["削弱"]["min_rarity"] == 4
    assert results["治疗"]["min_rarity"] == 3
    assert results["先锋干员"]["min_rarity"] == 3
    assert [op["name"] for op in results["爆发"]["operators"]] == ["刻刀", "THRM-EX"]
    assert [op["name"] for op in results["削弱"]["operators"]] == ["夜烟", "GALLUS²"]
    assert [op["name"] for op in results["治疗"]["operators"]] == ["安赛尔", "Lancet-2"]
    assert [op["name"] for op in results["先锋干员"]["operators"]] == ["芬", "夜刀"]
    assert results["新手"]["has_guarantee"] is False
    assert [op["name"] for op in results["新手"]["operators"]] == ["黑角"]
    assert "【无3★保底】" in format_result([results["新手"]], selected_tags=["新手"])


def test_recruitment_merges_combinations_with_identical_results():
    calculator = RecruitmentCalculator([
        {"name": "甲", "rarity": 4, "tags": ["输出"]},
        {"name": "乙", "rarity": 5, "tags": ["输出", "生存"]},
    ])

    results = calculator.calculate(["输出", "生存"])
    merged = next(result for result in results if [op["name"] for op in result["operators"]] == ["乙"])

    assert {tuple(tags) for tags in merged["tag_combinations"]} == {("生存",), ("输出", "生存")}
    assert len(results) == 2
    assert "输出 + 生存 / 生存" in format_result(results, selected_tags=["输出", "生存"])


def test_recruitment_result_resolves_and_downsizes_avatars():
    class Prts:
        async def resolve_avatar_urls(self, names):
            assert names == ["干员"]
            return {"干员": "https://example.test/avatar.png"}

    class Assets:
        async def data_uri(self, source, **kwargs):
            assert source == "https://example.test/avatar.png"
            assert kwargs == {"box": (64, 64), "quality": 82, "force_webp": True}
            return "data:image/webp;base64,avatar"

    renderer = CalendarRenderer.__new__(CalendarRenderer)
    renderer.recruitment_template = "recruitment"
    renderer.service = SimpleNamespace(assets=Assets(), prts=Prts())
    renderer._static_assets = lambda _charset: _empty_async({"font": "font"})
    renderer._card_render_options = lambda: {"type": "png"}
    captured = {}

    async def fake_render(_template, data, options):
        assert options == {"type": "png"}
        captured.update(data)
        return b"rendered"

    renderer._html_render = fake_render
    result = asyncio.run(renderer.recruitment_result([
        {
            "tags": ["输出"],
            "tag_combinations": [["输出"], ["输出", "生存"]],
            "operators": [{"name": "干员", "rarity": 4}],
            "min_rarity": 4,
        },
    ], ["输出", "生存"]))

    assert result == b"rendered"
    assert captured["rows"][0]["tag_combinations"] == ["输出", "输出 + 生存"]
    assert captured["rows"][0]["operators"][0]["avatar"].startswith("data:image/webp")
