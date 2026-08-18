from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.recurrence import build_recurrence_report, parse_recurrence_query
from sources.prts import PrtsSource


CN_TZ = ZoneInfo("Asia/Shanghai")


def _table(rows: str, heading: str = "六星干员", pool: str = "标准寻访") -> str:
    return f"""
    <h2>{heading}</h2><h3>{pool}</h3>
    <table class="wikitable"><tr><th>实装时间</th><th>干员</th><th>所在寻访</th>
    <th>出率提升</th><th>商店兑换</th></tr>{rows}</table>
    """


def _row(name: str, release: str, end: str, days: str, count: int, shop: str = "") -> str:
    shop_cells = f"<td>{shop}</td><td>12</td><td>2</td>" if shop else "<td colspan=\"3\">尚未进店</td>"
    return f"""<tr><td>{release}</td><td><a title="{name}">{name}</a></td><td>标准寻访</td>
    <td>{end}</td><td>{days}</td><td>{count}</td>{shop_cells}</tr>"""


def test_prts_recurrence_parser_extracts_table_rows():
    html = _table(
        _row("甲", "2020-01-01", "2026-01-01", "229", 4, "2026-02-01")
        + _row("乙", "2021-01-01", "2026-08-30", "进行中", 2),
    )

    rows = PrtsSource._parse_recurrence_overview(html)

    assert rows == [
        {
            "name": "甲", "rarity": 6, "pool_type": "标准寻访",
            "release_date": "2020-01-01", "rate_up_end": "2026-01-01",
            "rate_up_ongoing": False, "rate_up_count": 4,
            "shop_end": "2026-02-01", "shop_count": 2,
        },
        {
            "name": "乙", "rarity": 6, "pool_type": "标准寻访",
            "release_date": "2021-01-01", "rate_up_end": "2026-08-30",
            "rate_up_ongoing": True, "rate_up_count": 2,
            "shop_end": "", "shop_count": 0,
        },
    ]


def test_recurrence_report_sorts_finished_before_ongoing_and_formats_shop():
    records = [
        {"name": "最近", "rarity": 6, "pool_type": "标准寻访", "release_date": "2020-01-01", "rate_up_end": "2026-07-01", "rate_up_count": 1, "shop_end": "", "shop_count": 0},
        {"name": "最久", "rarity": 6, "pool_type": "中坚寻访", "release_date": "2020-02-01", "rate_up_end": "2025-01-01", "rate_up_count": 3, "shop_end": "2025-02-01", "shop_count": 2},
        {"name": "进行中", "rarity": 6, "pool_type": "标准寻访", "release_date": "2020-03-01", "rate_up_end": "2026-09-01", "rate_up_ongoing": True, "rate_up_count": 2, "shop_end": "", "shop_count": 0},
        {"name": "五星", "rarity": 5, "pool_type": "标准寻访", "release_date": "2020-04-01", "rate_up_end": "2024-01-01", "rate_up_count": 5, "shop_end": "", "shop_count": 0},
    ]
    now = datetime(2026, 8, 18, 12, tzinfo=CN_TZ)

    report = build_recurrence_report(records, now, "六星")

    assert [row["name"] for row in report["rows"]] == ["最久", "最近", "进行中"]
    assert report["rows"][0]["rate_up_text"] == "594 天"
    assert report["rows"][0]["shop_text"] == "2025-02-01 / 563 天 / 2 次"
    assert report["rows"][1]["shop_text"] == "尚未进店"
    assert report["rows"][2]["rate_up_text"] == "进行中"
    assert report["rows"][0]["highlight"] is True


def test_recurrence_report_supports_filters_and_rejects_unknown_scope():
    records = [
        {"name": "五星", "rarity": 5, "pool_type": "标准寻访", "release_date": "2020-01-01", "rate_up_end": "2026-01-01"},
        {"name": "中坚", "rarity": 6, "pool_type": "中坚寻访", "release_date": "2020-02-01", "rate_up_end": "2026-02-01"},
    ]
    now = datetime(2026, 8, 18, tzinfo=CN_TZ)

    assert [row["name"] for row in build_recurrence_report(records, now, "五星")["rows"]] == ["五星"]
    assert [row["name"] for row in build_recurrence_report(records, now, "中坚")["rows"]] == ["中坚"]
    with pytest.raises(ValueError, match="仅支持"):
        build_recurrence_report(records, now, "限定")


def test_recurrence_query_supports_optional_count_and_case_insensitive_all():
    assert parse_recurrence_query("", default_limit=18) == ("", 18)
    assert parse_recurrence_query("20", default_limit=18) == ("", 20)
    assert parse_recurrence_query("ALL", default_limit=18) == ("", None)
    assert parse_recurrence_query("中坚 12", default_limit=18) == ("中坚", 12)
    assert parse_recurrence_query("全部 aLl", default_limit=18) == ("全部", None)
    with pytest.raises(ValueError, match="正整数或 all"):
        parse_recurrence_query("六星 0", default_limit=18)


def test_recurrence_report_all_returns_all_matching_rows():
    records = [
        {"name": f"干员{index}", "rarity": 6, "pool_type": "标准寻访", "release_date": "2020-01-01", "rate_up_end": f"202{index}-01-01"}
        for index in range(1, 4)
    ]
    report = build_recurrence_report(records, datetime(2026, 8, 18, tzinfo=CN_TZ), limit=None)

    assert len(report["rows"]) == 3
    assert [row["rank"] for row in report["rows"]] == [1, 2, 3]
