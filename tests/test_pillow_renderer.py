from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin.core.models import CalendarSnapshot, Operator, TimelineItem, TodayInfo  # noqa: E402
from astrbot_plugin.core.render_cache import CalendarImageCache, HelpImageCache  # noqa: E402
from astrbot_plugin.core.renderer import CalendarRenderer  # noqa: E402


def _image_data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class _Plugin:
    async def html_render(self, *args, **kwargs):
        raise AssertionError("Pillow 后端不应调用 AstrBot HTML 渲染")


class _Service:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.plugin_version = "test"
        self.config = {"render_engine": "pillow"}

    def value(self, section, key, default, *args):
        if section == "cache_and_render" and key == "render_engine":
            return "pillow"
        return default


class PillowRendererTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = _Service(self.root)
        self.renderer = CalendarRenderer(_Plugin(), self.service)
        self.asset = ROOT / "assets" / "event-blackforest.png"
        self.avatar = ROOT / "assets" / "operator-angelina.png"
        self.snapshot = CalendarSnapshot(
            generated_at="2026-08-06T08:00:00+08:00",
            calendar_date="2026-08-06",
            timeline_start="2026-08-01T00:00:00+08:00",
            timeline_end="2026-08-15T23:59:00+08:00",
            today_info=TodayInfo(
                resource_schedule=[{"name": "货物运送", "open": True}],
                chip_schedule=[{"name": "重装芯片", "open": False}],
                alerts=[{"title": "限时行动", "detail": "请及时完成今日作战。"}],
            ),
            today_birthdays=[Operator("安洁莉娜", 8, 6, "辅助", 6, _image_data_uri(self.avatar))],
            recent_operators=[Operator("卡缇", 1, 1, "重装", 4, _image_data_uri(self.avatar))],
            events=[TimelineItem("event", "黑森林的回声", "event", "活动", "2026-08-02T10:00:00+08:00", "2026-08-12T03:59:00+08:00", image=_image_data_uri(self.asset))],
            gacha_pools=[TimelineItem("pool", "联合行动寻访", "gacha", "限定寻访", "2026-08-03T10:00:00+08:00", "2026-08-14T03:59:00+08:00", image=_image_data_uri(self.asset))],
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_renders_calendar_history_and_help_to_valid_pngs(self):
        calendar = await self.renderer.calendar(self.snapshot)
        history = await self.renderer.historical_calendar(self.snapshot)
        help_image = await self.renderer.help_page(
            self.snapshot,
            [{"name": "方舟日历", "summary": "生成今日行动日历", "argument_hint": "", "example": "/方舟日历"}],
            [],
        )

        for path in (calendar, history, help_image):
            self.assertIsInstance(path, Path)
            self.assertTrue(path.is_file())
            with Image.open(path) as rendered:
                self.assertEqual(rendered.format, "PNG")
                self.assertEqual(rendered.width, 1440)
                self.assertGreater(rendered.height, 400)

    async def test_renders_full_content_with_long_term_cards_before_headhunting(self):
        self.snapshot.today_info = TodayInfo(
            voucher_exchange=[{"name": "凭证兑换", "image": _image_data_uri(self.avatar)}],
            new_skins=[{"name": "新增时装", "image": _image_data_uri(self.avatar)}],
            new_modules=[{"name": "新增模组", "image": _image_data_uri(self.avatar)}],
        )
        self.snapshot.long_term_events = [
            TimelineItem("long", "长期活动", "event", "集成战略", "2026-08-01T00:00:00+08:00", "2026-08-15T23:59:00+08:00", image=_image_data_uri(self.asset))
        ]

        rendered = await self.renderer.calendar(self.snapshot)
        with Image.open(rendered) as image:
            self.assertEqual(image.width, 1440)
            self.assertGreater(image.height, 1800)
            self.assertEqual(image.getpixel((20, image.height - 20))[:3], (16, 21, 23))

    async def test_pillow_rendering_keeps_dynamic_activity_and_operator_images(self):
        rendered = await self.renderer.calendar(self.snapshot)
        with Image.open(rendered) as image:
            # 活动主视觉在头图和活动卡中呈现，干员头像在生日/新增干员卡中呈现；
            # 这里只校验像素不退化为单色占位图。
            colors = image.convert("RGB").getcolors(maxcolors=image.width * image.height)
        self.assertIsNotNone(colors)
        self.assertGreater(len(colors), 256)


class RenderEngineCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = CalendarSnapshot(
            generated_at="2026-08-06T08:00:00+08:00",
            calendar_date="2026-08-06",
            timeline_start="2026-08-01T00:00:00+08:00",
            timeline_end="2026-08-15T23:59:00+08:00",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_calendar_signature_and_help_cache_are_engine_isolated(self):
        cache = CalendarImageCache(self.root / "calendar")
        html_config = {"render_engine": "astrbot", "template_hash": "html"}
        pillow_config = {"render_engine": "pillow", "template_hash": "pillow-v2"}
        self.assertNotEqual(cache.signature(self.snapshot, html_config), cache.signature(self.snapshot, pillow_config))

        help_cache = HelpImageCache(self.root / "help")
        now = datetime(2026, 8, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
        pillow_path = help_cache.store(b"\x89PNG\r\n\x1a\nexample", "full", now=now, engine="pillow")
        self.assertEqual(pillow_path.name, "help-full-pillow-2026-08-06.png")
        self.assertIsNone(help_cache.lookup("full", now=now))
        self.assertEqual(help_cache.lookup("full", now=now, engine="pillow"), pillow_path)


if __name__ == "__main__":
    unittest.main()
