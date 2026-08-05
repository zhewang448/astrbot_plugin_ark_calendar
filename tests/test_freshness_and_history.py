import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot_plugin.core.cache import JsonCache
from astrbot_plugin.core.config import config_int
from astrbot_plugin.core.models import CalendarSnapshot, SourceState, TimelineItem
from astrbot_plugin.core.service import CalendarService


CN_TZ = ZoneInfo("Asia/Shanghai")


class Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def make_service(now: datetime) -> CalendarService:
    service = CalendarService.__new__(CalendarService)
    service.config = {
        "basic": {"timeline_days": 28, "include_recent_operators": True, "include_long_term": True},
        "data_sources": {},
        "cache_and_render": {"snapshot_fallback_max_age_hours": 12},
    }
    service.cache = JsonCache(Path(tempfile.mkdtemp()))
    service.logger = Logger()
    service._now = lambda: now
    service._event_detail_semaphore = asyncio.Semaphore(4)
    return service


class SourceFreshnessTests(unittest.IsolatedAsyncioTestCase):
    def test_source_cache_schema_mismatch_is_ignored(self):
        service = make_service(datetime(2026, 8, 4, 8, tzinfo=CN_TZ))
        service.cache.save("events.json", {
            "_cache_kind": service.SOURCE_CACHE_KIND,
            "schema_version": service.SOURCE_CACHE_SCHEMA_VERSION + 1,
            "fetched_at": "2026-08-04T07:00:00+08:00",
            "data": [{"name": "obsolete"}],
        })

        self.assertEqual(service._load_source_cache("events.json"), (None, None))

    def test_config_int_defaults_and_clamps_invalid_values(self):
        config = {"cache_and_render": {"ttl": "bad", "large": "999"}}
        self.assertEqual(config_int(config, "cache_and_render", "ttl", 30, minimum=1, maximum=60), 30)
        self.assertEqual(config_int(config, "cache_and_render", "large", 30, minimum=1, maximum=60), 60)
        self.assertEqual(config_int(config, "cache_and_render", "missing", 30, minimum=1, maximum=60), 30)
    async def test_expired_source_cache_is_not_reused(self):
        initial = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(initial)

        async def success():
            return [{"name": "活动", "start": "2026-08-01T00:00:00+08:00", "end": "2026-08-10T00:00:00+08:00"}]

        validator = CalendarService._valid_events
        data, state = await service._fetch_cached("events.json", "活动", success(), [], validator, timedelta(hours=1))
        self.assertTrue(state.ok)
        self.assertEqual(len(data), 1)

        service._now = lambda: initial + timedelta(hours=2)

        async def failure():
            raise RuntimeError("upstream down")

        data, state = await service._fetch_cached("events.json", "活动", failure(), [], validator, timedelta(hours=1))
        self.assertEqual(data, [])
        self.assertFalse(state.ok)
        self.assertIn("缓存已陈旧", state.message)

    async def test_fresh_source_cache_fallback_is_typed_and_stable(self):
        initial = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(initial)

        async def success():
            return [{"name": "活动", "start": "2026-08-01T00:00:00+08:00", "end": "2026-08-10T00:00:00+08:00"}]

        validator = CalendarService._valid_events
        await service._fetch_cached("events.json", "活动", success(), [], validator, timedelta(hours=1))
        service._now = lambda: initial + timedelta(minutes=30)

        async def failure():
            raise TimeoutError("upstream down")

        _, first = await service._fetch_cached("events.json", "活动", failure(), [], validator, timedelta(hours=1))
        service._now = lambda: initial + timedelta(minutes=31)
        _, second = await service._fetch_cached("events.json", "活动", failure(), [], validator, timedelta(hours=1))

        self.assertEqual(first.status, "fallback")
        self.assertTrue(first.used_cache)
        self.assertEqual(first.event_key, second.event_key)
        self.assertNotEqual(first.message, second.message)

    async def test_event_detail_uses_fresh_cache_before_network(self):
        now = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(now)

        class Prts:
            def __init__(self):
                self.calls = 0

            async def event_detail(self, name):
                self.calls += 1
                return {"type": "活动", "image_url": ""}

        service.prts = Prts()
        first = await service._event_detail("测试活动")
        second = await service._event_detail("测试活动")
        self.assertEqual(first, second)
        self.assertEqual(service.prts.calls, 1)


class SnapshotAndHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_fallback_rejects_mismatched_data_config(self):
        now = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(now)
        snapshot = CalendarSnapshot(
            generated_at="2026-08-04T07:00:00+08:00",
            calendar_date="2026-08-04",
            timeline_start="2026-08-03T00:00:00+08:00",
            timeline_end="2026-08-31T00:00:00+08:00",
            schema_version=service.SNAPSHOT_SCHEMA_VERSION,
            data_config_hash=service._data_config_hash(),
        )
        self.assertTrue(service._can_use_snapshot(snapshot, timedelta(hours=12)))
        service.config["basic"]["timeline_days"] = 14
        self.assertFalse(service._can_use_snapshot(snapshot, timedelta(hours=12)))

    async def test_failed_critical_refresh_uses_last_known_good_without_overwriting_it(self):
        now = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(now)
        service.refresh_lock = asyncio.Lock()
        service.last_refresh_error = ""
        service.last_refresh_quality = "fresh"
        service.last_refresh_used_cache = False
        service.last_refresh_source_states = []
        good = CalendarSnapshot(
            generated_at="2026-08-04T07:30:00+08:00",
            calendar_date="2026-08-04",
            timeline_start="2026-08-03T00:00:00+08:00",
            timeline_end="2026-08-31T00:00:00+08:00",
            schema_version=service.SNAPSHOT_SCHEMA_VERSION,
            data_config_hash=service._data_config_hash(),
        )
        failed = CalendarSnapshot(
            generated_at=now.isoformat(),
            calendar_date="2026-08-04",
            timeline_start="2026-08-03T00:00:00+08:00",
            timeline_end="2026-08-31T00:00:00+08:00",
            source_states=[SourceState(
                "PRTS / 首页",
                False,
                "",
                "当前不可用且无有效缓存",
                event_key="source:PRTS_首页:timeout",
                status="failed",
            )],
            schema_version=service.SNAPSHOT_SCHEMA_VERSION,
            data_config_hash=service._data_config_hash(),
        )
        service.last_snapshot = good
        service.last_known_good_snapshot = good

        async def build_failed():
            return failed

        service._build_snapshot = build_failed
        result = await service.snapshot(force=True)

        self.assertIs(result, good)
        self.assertIs(service.last_known_good_snapshot, good)
        self.assertEqual(service.last_refresh_quality, "fallback")
        self.assertTrue(service.last_refresh_used_cache)
        self.assertEqual(service.last_refresh_source_states[0].event_key, "source:PRTS_首页:timeout")
        self.assertEqual(service.cache.load("snapshot-degraded.json")["refresh_quality"], "failed")

    def test_home_validator_rejects_empty_parse_and_accepts_expected_contract(self):
        self.assertFalse(CalendarService._valid_home({"supplies": [], "resource_schedule": [], "chip_schedule": []}))
        resources = [
            {"name": name, "weekdays": [], "open": False}
            for name in ("作战记录", "技巧概要", "龙门币", "采购凭证")
        ]
        chips = [
            {"name": name, "weekdays": [], "open": False}
            for name in ("术师&狙击", "先锋&辅助", "医疗&重装", "近卫&特种")
        ]
        self.assertTrue(CalendarService._valid_home({
            "resource_schedule": resources,
            "chip_schedule": chips,
        }))

    async def test_historical_schedule_filters_requested_window(self):
        now = datetime(2026, 8, 4, 8, tzinfo=CN_TZ)
        service = make_service(now)

        class Anything:
            async def events(self):
                return [
                    {"name": "历史活动", "start": "2026-07-02T10:00:00+08:00", "end": "2026-07-11T03:59:00+08:00"},
                    {"name": "范围外活动", "start": "2026-06-01T10:00:00+08:00", "end": "2026-06-02T03:59:00+08:00"},
                ]

        class Prts:
            async def gacha_overview(self):
                return [{"name": "历史寻访", "start": "2026-07-01 10:00", "end": "2026-07-15 03:59"}]

        class Gacha:
            last_source_states = [{"name": "ArknightsGachaData", "ok": True, "message": ""}]

            async def pools(self, start, end, overview):
                return [{"name": "历史寻访", "type": "LIMITED", "start": datetime(2026, 7, 1, 10, tzinfo=CN_TZ), "end": datetime(2026, 7, 15, 3, 59, tzinfo=CN_TZ)}]

        service.anything = Anything()
        service.prts = Prts()
        service.gacha = Gacha()
        service.assets = object()

        async def fake_build_events(events_raw, start, end):
            return [TimelineItem("event-1", "历史活动", "event", "活动", "2026-07-02T10:00:00+08:00", "2026-07-11T03:59:00+08:00")], []

        async def fake_build_gacha_items(pools_raw):
            return [TimelineItem("pool-1", "历史寻访", "gacha", "限定寻访", "2026-07-01T10:00:00+08:00", "2026-07-15T03:59:00+08:00")]

        service._build_events = fake_build_events
        service._build_gacha_items = fake_build_gacha_items
        result = await service.historical_snapshot(
            datetime(2026, 7, 1, tzinfo=CN_TZ),
            datetime(2026, 7, 31, 23, 59, tzinfo=CN_TZ),
        )
        self.assertEqual([item.name for item in result.events], ["历史活动"])
        self.assertEqual([item.name for item in result.gacha_pools], ["历史寻访"])


class TemplateBirthdayTests(unittest.TestCase):
    def test_future_birthday_template_does_not_truncate_to_four_groups(self):
        template = (Path(__file__).resolve().parents[1] / "templates" / "calendar.html").read_text("utf-8")
        self.assertIn("snapshot.upcoming_birthdays", template)
        self.assertNotIn("snapshot.upcoming_birthdays[:4]", template)
        self.assertIn("NEXT 9 DAYS", template)
        self.assertIn(".next-birth{display:flex;flex-wrap:wrap}", template)
