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


def _card(days: str, name: str, file_name: str) -> str:
    return (
        f'<div class="mp-res" data-days="{days}"><span class="mp-res__icon">'
        f'<img src="https://media.prts.wiki/0/03/{quote(file_name)}.png?v=x"></span>'
        f'<span class="mp-res__name">{name}</span></div>'
    )


def _op_group(title: str, cards: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<div class="ak-op-card"><a href="{href}" title="{name}"><span class="ak-op-card__portrait">'
        f'<img src="https://media.prts.wiki/头像_{name}.png"><span class="ak-op-card__rarity"><img src="r.png"></span></span>'
        f'<span class="ak-op-card__name">{name}<span class="ak-op-card__sub">{sub}</span></span></a></div>'
        for name, sub, href in cards
    )
    return f'<div class="mp-ops__group"><div class="mp-ops__title"><span class="cn">{title}</span></div>{body}</div>'


def _card_home_html(force_open: str = "") -> str:
    resources = [
        ("1234567", "作战记录", "道具_高级作战记录"), ("2357", "技巧概要", "道具_技巧概要·卷3"),
        ("2467", "龙门币", "道具_龙门币"), ("1467", "采购凭证", "道具_采购凭证"), ("1356", "碳", "道具_碳素"),
    ]
    chips = [
        ("2367", "近卫 &amp; 特种", "道具_近卫芯片"), ("1457", "重装 &amp; 医疗", "道具_重装芯片"),
        ("3467", "先锋 &amp; 辅助", "道具_先锋芯片"), ("1256", "狙击 &amp; 术师", "道具_狙击芯片"),
    ]

    def group(label, cards):
        return f'<div class="mp-res-group"><div class="mp-label">{label}</div>' + "".join(_card(*c) for c in cards) + "</div>"

    hero = (
        '<div class="mp-hero__slide"><span class="mp-hero__eyebrow">寻访 · Headhunting</span>'
        '<h2 class="mp-hero__title">定向甄选08</h2><span class="ak-countdown" data-until="2026-10-13T03:59:00+08:00"></span></div>'
        '<div class="mp-hero__slide"><span class="mp-hero__eyebrow">寻访 · Headhunting</span>'
        '<h2 class="mp-hero__title">常驻标准寻访</h2><span class="ak-countdown" data-until="2026-10-08T03:59:00+08:00"></span></div>'
        '<div class="mp-hero__slide"><span class="mp-hero__eyebrow">登录活动 · Event</span>'
        '<h2 class="mp-hero__title">稳态测定</h2><span class="ak-countdown" data-until="2026-10-08T03:59:00+08:00"></span></div>'
    )
    return (
        f'<html><body>{hero}<div class="mp-cd"><div class="mp-cd__label"><b>剿灭作战 &amp; 周常任务刷新</b><small>每周一 04:00</small></div></div>'
        f'<div class="mp-today__res" id="mp-res" data-force-open="{force_open}">'
        + group("物资筹备", resources) + group("芯片搜索", chips) + "</div>"
        + _op_group("今天生日", [("古米", "10月2日", "/w/古米")])
        + _op_group("近期新增", [("结城理", "特种 · 六星", "/w/结城理")])
        + _op_group("凭证兑换", [("洋灰", "高级凭证兑换", "/w/洋灰")])
        + _op_group("新增模组", [("结城理", "彼此的声音", "/w/结城理#彼此的声音")])
        + '<div class="mp-stages__event"><div class="mw-heading"><h4>矢量突破#3 「拟生态」<span class="ak-en">VEC</span></h4></div>'
        '<div class="mp-stages__chapter">核心突破</div><div class="mp-stages__grid">'
        + '<div class="ak-stage"><a title="VEC-1"></a></div>' * 2
        + '</div><div class="mp-stages__chapter">特别战线</div><div class="mp-stages__grid"><div class="ak-stage"><a title="VEC-S"></a></div></div></div>'
        '<div class="mp-furn mp-furn--theme"><a href="/w/圣芭菲甜点店" title="圣芭菲甜点店"><span class="mp-furn__pic">'
        '<img src="//torappu.prts.wiki/assets/furniture_theme/furni_set_dessertShop.png"></span>'
        '<span class="mp-furn__name">圣芭菲甜点店<span class="ak-tag">主题</span></span><span class="mp-furn__desc">甜点店。</span></a></div>'
        + "</body></html>"
    )


def test_card_layout_follows_data_days_and_aligns_names():
    home = PrtsSource._card_home(BeautifulSoup(_card_home_html(), "html.parser"), TUESDAY, "https://prts.wiki")

    assert [item["name"] for item in home["resource_schedule"]] == ["作战记录", "技巧概要", "龙门币", "采购凭证", "碳&家具零件"]
    assert [item["name"] for item in home["chip_schedule"]] == ["近卫&特种", "医疗&重装", "先锋&辅助", "术师&狙击"]
    assert home["supplies"] == ["作战记录", "技巧概要", "龙门币"]
    assert home["chips"] == ["近卫&特种", "术师&狙击"]
    assert home["resource_schedule"][1]["weekdays_label"] == "二三五日"
    assert CalendarService._valid_home(home)


def test_card_layout_force_open_window_opens_everything_until_it_ends():
    home = PrtsSource._card_home(
        BeautifulSoup(_card_home_html("2026-09-29T16:00:00+08:00/2026-10-20T03:59:00+08:00"), "html.parser"),
        TUESDAY, "https://prts.wiki",
    )
    assert len(home["supplies"]) == 5 and len(home["chips"]) == 4
    # 缓存在区间结束后被重新判定时恢复按周开放。
    later = CalendarService._refresh_home_status(home, datetime(2026, 10, 20, 12, 0, tzinfo=CN_TZ))
    assert later["supplies"] == ["作战记录", "技巧概要", "龙门币"]


def test_card_layout_operators_and_alerts():
    home = PrtsSource._card_home(BeautifulSoup(_card_home_html(), "html.parser"), TUESDAY, "https://prts.wiki")

    assert home["birthday"] == [{"name": "古米", "avatar": "https://media.prts.wiki/头像_古米.png"}]
    assert [x["name"] for x in home["recent"]] == ["结城理"]
    assert home["voucher_exchange"][0]["subtitle"] == ""
    assert home["new_modules"][0]["subtitle"] == "彼此的声音"
    assert home["new_skins"] == []
    assert home["alerts"] == [
        {"kind": "周常刷新", "name": "剿灭作战 & 周常任务刷新", "time": "10.05 04:00"},
        {"kind": "寻访结束", "name": "定向甄选08", "time": "10.13 03:59"},
    ]


def test_card_layout_new_stages_and_furniture():
    home = PrtsSource._card_home(BeautifulSoup(_card_home_html(), "html.parser"), TUESDAY, "https://prts.wiki")

    assert home["new_stages"] == [{
        "name": "矢量突破#3 「拟生态」", "code": "VEC",
        "chapters": [{"name": "核心突破", "count": 2}, {"name": "特别战线", "count": 1}],
    }]
    furniture = home["new_furniture"][0]
    assert (furniture["name"], furniture["tag"]) == ("圣芭菲甜点店", "主题")
    assert furniture["image"] == "https://torappu.prts.wiki/assets/furniture_theme/furni_set_dessertShop.png"
