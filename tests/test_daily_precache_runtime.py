from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _decorator(*args, **kwargs):
    def apply(target):
        return target
    return apply


def _install_astrbot_stubs() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = _Logger()
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = object
    event.filter = types.SimpleNamespace(
        command=_decorator,
        permission_type=_decorator,
        PermissionType=types.SimpleNamespace(ADMIN="admin"),
    )
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context=None):
            self.context = context

    star.Context = object
    star.Star = Star
    components = types.ModuleType("astrbot.api.message_components")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    paths = types.ModuleType("astrbot.core.utils.astrbot_path")
    paths.get_astrbot_plugin_data_path = lambda: str(ROOT / ".test-data")

    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.api.message_components": components,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": paths,
    })


_install_astrbot_stubs()
main = importlib.import_module("astrbot_plugin.main")


class _Scheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


class _HelpCache:
    def __init__(self):
        self.invalidated = 0
        self.cached_modes: set[str] = set()

    def invalidate(self):
        self.invalidated += 1
        self.cached_modes.clear()

    def lookup(self, mode):
        return Path(f"/{mode}.png") if mode in self.cached_modes else None


class DailyPrecacheRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = main.ArkCalendarPlugin.__new__(main.ArkCalendarPlugin)
        self.plugin._daily_precache_lock = asyncio.Lock()
        self.plugin.help_cache = _HelpCache()
        self.plugin._notify_admin = AsyncMock()
        self.plugin._observe_health = AsyncMock()

    def test_registers_callable_daily_job_at_0400(self):
        self.plugin.scheduler = _Scheduler()

        self.assertEqual(self.plugin._add_daily_precache_job(), 1)
        args, kwargs = self.plugin.scheduler.jobs[0]
        self.assertEqual(args[0].__func__, main.ArkCalendarPlugin._daily_precache)
        self.assertEqual(args[1], "cron")
        self.assertEqual(kwargs["hour"], 4)
        self.assertEqual(kwargs["minute"], 0)
        self.assertEqual(kwargs["id"], "ark_calendar_daily_precache")

    async def test_refreshes_once_and_populates_calendar_and_both_help_modes(self):
        snapshot = object()
        self.plugin.service = types.SimpleNamespace(snapshot=AsyncMock(return_value=snapshot))
        self.plugin._calendar_image = AsyncMock(return_value=(Path("/calendar.png"), "rendered", None))

        async def render_help(mode, actual_snapshot):
            self.assertIs(actual_snapshot, snapshot)
            self.plugin.help_cache.cached_modes.add(mode)
            return Path(f"/{mode}.png")

        self.plugin._render_help_image = AsyncMock(side_effect=render_help)

        await self.plugin._daily_precache()

        self.plugin.service.snapshot.assert_awaited_once_with(force=True)
        self.plugin._calendar_image.assert_awaited_once_with(snapshot)
        self.assertEqual(self.plugin.help_cache.invalidated, 1)
        self.assertEqual(
            [call.args for call in self.plugin._render_help_image.await_args_list],
            [("full", snapshot), ("subscribe", snapshot)],
        )
        self.plugin._observe_health.assert_awaited_once_with(snapshot, "每日预缓存")
        self.plugin._notify_admin.assert_not_awaited()

    async def test_help_failure_does_not_prevent_the_other_help_mode_from_caching(self):
        snapshot = object()
        self.plugin.service = types.SimpleNamespace(snapshot=AsyncMock(return_value=snapshot))
        self.plugin._calendar_image = AsyncMock(return_value=(Path("/calendar.png"), "rendered", None))

        async def render_help(mode, actual_snapshot):
            if mode == "full":
                return None
            self.plugin.help_cache.cached_modes.add(mode)
            return Path("/subscribe.png")

        self.plugin._render_help_image = AsyncMock(side_effect=render_help)

        await self.plugin._daily_precache()

        self.assertEqual(
            [call.args for call in self.plugin._render_help_image.await_args_list],
            [("full", snapshot), ("subscribe", snapshot)],
        )
        self.assertEqual(self.plugin.help_cache.cached_modes, {"subscribe"})
        self.plugin._notify_admin.assert_awaited_once_with(
            "【方舟日历异常告警】\n每日 04:00 预缓存未能生成帮助图：full。详情请查看 AstrBot 日志。",
            "daily_precache_help_failed",
        )
        self.plugin._observe_health.assert_awaited_once_with(snapshot, "每日预缓存")

    async def test_failure_notifies_admin_and_does_not_escape_scheduler_job(self):
        self.plugin.service = types.SimpleNamespace(snapshot=AsyncMock(side_effect=RuntimeError("network")))
        self.plugin._calendar_image = AsyncMock()
        self.plugin._render_help_image = AsyncMock()

        await self.plugin._daily_precache()

        self.plugin._notify_admin.assert_awaited_once()
        self.plugin._calendar_image.assert_not_awaited()
        self.plugin._render_help_image.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
