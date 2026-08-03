import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from astrbot_plugin.core.service import CalendarService
from astrbot_plugin.sources.gacha import GachaSource
from astrbot_plugin.sources.prts import PrtsSource


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

    def test_avatar_name_from_prts_media_url(self):
        url = "https://media.prts.wiki/8/87/%E5%A4%B4%E5%83%8F_%E5%8D%A1%E7%BC%87.png"
        self.assertEqual(PrtsSource._avatar_name_from_url(url), "卡缇")


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return {
            "query": {
                "pages": [{
                    "title": "文件:头像 卡缇.png",
                    "imageinfo": [{
                        "url": "https://media.prts.wiki/8/87/%E5%A4%B4%E5%83%8F_%E5%8D%A1%E7%BC%87.png"
                    }],
                }]
            }
        }


class AsyncParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_avatar_ignores_normalized_page_title(self):
        http = FakeHttp()
        source = PrtsSource(http, "https://prts.wiki")
        result = await source.resolve_avatar_urls(["卡缇"])
        self.assertEqual(result["卡缇"], "https://media.prts.wiki/8/87/%E5%A4%B4%E5%83%8F_%E5%8D%A1%E7%BC%87.png")
        self.assertEqual(len(http.calls), 1)


if __name__ == "__main__":
    unittest.main()
