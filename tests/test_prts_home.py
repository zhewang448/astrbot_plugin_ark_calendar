import importlib
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from sources.prts import PrtsSource

# service.py 使用 ..sources 相对导入，需按插件包路径加载。
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT.parent))
try:
    CalendarService = importlib.import_module(f"{_ROOT.name}.core.service").CalendarService
finally:
    sys.path.remove(str(_ROOT.parent))


CN_TZ = ZoneInfo("Asia/Shanghai")
# 2026-09-29 周二 12:00，游戏内周二（weekday=1）。
TUESDAY = datetime(2026, 9, 29, 12, 0, tzinfo=CN_TZ)
RESOURCE_FILES = ["道具_高级作战记录", "道具_技巧概要·卷3", "道具_龙门币", "道具_采购凭证", "道具_碳素"]
CHIP_FILES = ["摧枯拉朽_缩略", "身先士卒_缩略", "固若金汤_缩略", "势不可挡_缩略"]


def _icon_cell(file_name: str, anchor: str, colspan: int) -> str:
    href = f"/w/{quote('关卡一览/资源收集')}#{anchor}"
    src = f"https://media.prts.wiki/thumb/x/{quote(file_name)}.png/80px-{quote(file_name)}.png"
    return (
        f'<td colspan="{colspan}" style="background:#343434;">'
        f'<a href="{href}"><img src="{src}"></a></td>'
    )


def _home_html(resource_days: list[str], chip_days: list[str]) -> str:
    def day_row(labels: list[str], colspan: int) -> str:
        return "<tr>" + "".join(f'<td colspan="{colspan}">{label}</td>' for label in labels) + "</tr>"

    return (
        "<html><body><table><tbody>"
        "<tr>" + "".join(_icon_cell(name, "物资筹备", 4) for name in RESOURCE_FILES) + "</tr>"
        + day_row(resource_days, 4 if len(resource_days) > 1 else 20)
        + "<tr>" + "".join(_icon_cell(name, "芯片搜索", 5) for name in CHIP_FILES) + "</tr>"
        + day_row(chip_days, 5 if len(chip_days) > 1 else 20)
        + "</tbody></table></body></html>"
    )


def _schedules(html: str) -> tuple[list[dict], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    weekday = 1
    return (
        PrtsSource._resource_schedule(soup, weekday, "https://prts.wiki"),
        PrtsSource._chip_schedule(soup, weekday, "https://prts.wiki"),
    )


def test_all_day_open_merged_row_marks_every_stage_open():
    resources, chips = _schedules(_home_html(["全天开放"], ["全天开放"]))

    assert [item["name"] for item in resources] == ["作战记录", "技巧概要", "龙门币", "采购凭证", "碳&家具零件"]
    assert [item["name"] for item in chips] == ["术师&狙击", "先锋&辅助", "医疗&重装", "近卫&特种"]
    assert all(item["always_open"] and item["open"] for item in resources + chips)
    assert CalendarService._valid_home({"resource_schedule": resources, "chip_schedule": chips})
    refreshed = CalendarService._refresh_home_status({"resource_schedule": resources, "chip_schedule": chips}, TUESDAY)
    assert len(refreshed["supplies"]) == 5 and len(refreshed["chips"]) == 4


def test_weekday_labels_still_follow_schedule():
    resources, chips = _schedules(_home_html(
        ["常驻", "一三五六", "常驻", "一四六日", "二三五日"],
        ["一四五日", "一二五六", "二三六日", "三四六日"],
    ))

    assert {item["name"]: item["open"] for item in resources} == {
        "作战记录": True, "技巧概要": False, "龙门币": True, "采购凭证": False, "碳&家具零件": True,
    }
    assert {item["name"]: item["open"] for item in chips} == {
        "术师&狙击": False, "先锋&辅助": True, "医疗&重装": True, "近卫&特种": False,
    }
    assert CalendarService._valid_home({"resource_schedule": resources, "chip_schedule": chips})


def test_resource_table_ignores_unrelated_tables():
    soup = BeautifulSoup("<table><tr><td>常驻 二三五日 一四六日</td></tr></table>", "html.parser")

    assert PrtsSource._resource_table(soup) is None
