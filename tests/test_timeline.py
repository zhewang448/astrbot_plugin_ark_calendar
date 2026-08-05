import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from astrbot_plugin.core.renderer import CalendarRenderer


class TimelineTickTests(unittest.TestCase):
    def test_ticks_follow_configured_window(self):
        tz = ZoneInfo("Asia/Shanghai")
        start = datetime(2026, 8, 3, tzinfo=tz)
        ticks = CalendarRenderer._ticks(start, datetime(2026, 8, 4, tzinfo=tz), 14)
        self.assertEqual(ticks[-1]["left"], 100)
        self.assertTrue(any(item["today"] for item in ticks))
        self.assertEqual(len({item["left"] for item in ticks}), len(ticks))
