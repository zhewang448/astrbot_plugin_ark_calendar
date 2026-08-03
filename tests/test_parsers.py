import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from astrbot_plugin.core.service import CalendarService
from astrbot_plugin.sources.gacha import GachaSource


class ParserTests(unittest.TestCase):
    def test_normalize_operator_name(self):
        self.assertEqual(CalendarService.normalize_name(" 予愿·安洁莉娜 "), "予愿安洁莉娜")

    def test_match_gacha_overview(self):
        tz = ZoneInfo("Asia/Shanghai")
        row = {"start": "2026-07-30 04:00", "end": "2026-08-13 03:59", "six": ["琳琅诗怀雅", "缇缇"]}
        found = GachaSource._match_overview(
            datetime(2026, 7, 30, 4, 0, tzinfo=tz),
            datetime(2026, 8, 13, 3, 59, tzinfo=tz),
            [row],
        )
        self.assertEqual(found["six"], ["琳琅诗怀雅", "缇缇"])


if __name__ == "__main__":
    unittest.main()
