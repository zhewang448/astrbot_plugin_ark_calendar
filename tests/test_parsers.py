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

    def test_gacha_name_discards_prts_category_prefix(self):
        self.assertEqual(
            PrtsSource._gacha_name("【限定寻访·夏季】车辙与风的归所"),
            "车辙与风的归所",
        )

    def test_match_gacha_overview_by_name_when_start_time_differs(self):
        tz = ZoneInfo("Asia/Shanghai")
        row = {
            "name": "车辙与风的归所",
            "start": "2026-08-01 12:00",
            "end": "2026-08-15 03:59",
            "image": "https://media.prts.wiki/banner.jpg",
        }
        found = GachaSource._match_overview(
            datetime(2026, 8, 1, 7, 0, tzinfo=tz),
            datetime(2026, 8, 15, 3, 59, tzinfo=tz),
            [row],
            "车辙与风的归所",
        )
        self.assertEqual(found["image"], "https://media.prts.wiki/banner.jpg")
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


class HomeParserTests(unittest.TestCase):
    def test_resource_schedule_uses_weekday_and_brightness(self):
        from bs4 import BeautifulSoup
        from astrbot_plugin.sources.prts import PrtsSource

        html = """
        <table><tbody>
          <tr><td style='background:#324c65'><img src='https://media.prts.wiki/x/高级作战记录.png'></td><td style='background:#343434'><img src='https://media.prts.wiki/x/技巧概要·卷3.png'></td><td style='background:#343434'><img src='https://media.prts.wiki/x/龙门币.png'></td><td style='background:#585858'><img src='https://media.prts.wiki/x/采购凭证.png'></td><td style='background:#585858'><img src='https://media.prts.wiki/x/碳素.png'></td></tr>
          <tr><td>常驻</td><td>二三五日</td><td>二四六日</td><td>一四六日</td><td>一三五六</td></tr>
          <tr><td style='background:#585858'><img src='https://media.prts.wiki/x/摧枯拉朽.png'></td><td style='background:#343434'><img src='https://media.prts.wiki/x/身先士卒.png'></td><td style='background:#585858'><img src='https://media.prts.wiki/x/固若金汤.png'></td><td style='background:#343434'><img src='https://media.prts.wiki/x/势不可挡.png'></td></tr>
          <tr><td>一二五六</td><td>二三六日</td><td>一四五日</td><td>三四六日</td></tr>
        </tbody></table>
        """
        soup = BeautifulSoup(html, "html.parser")
        resources = PrtsSource._resource_schedule(soup, 0, "https://prts.wiki")
        chips = PrtsSource._chip_schedule(soup, 0, "https://prts.wiki")
        self.assertEqual([x["name"] for x in resources if x["open"]], ["作战记录", "采购凭证", "碳&家具零件"])
        self.assertEqual([x["name"] for x in chips if x["open"]], ["术师&狙击", "医疗&重装"])

    def test_home_highlight_section_splits_module_name(self):
        from bs4 import BeautifulSoup
        from astrbot_plugin.sources.prts import PrtsSource

        soup = BeautifulSoup("""
          <div class='mp-operators-content'><div class='mp-operators-title'>新增模组</div>
            <a title='珊比#出发的勇气' href='/w/珊比#出发的勇气'><img id='charicon' src='https://media.prts.wiki/avatar.png'></a>
          </div>
        """, "html.parser")
        items = PrtsSource._highlight_section(soup, "新增模组", "https://prts.wiki", split_subtitle=True)
        self.assertEqual(items[0]["name"], "珊比")
        self.assertEqual(items[0]["subtitle"], "出发的勇气")


if __name__ == "__main__":
    unittest.main()
