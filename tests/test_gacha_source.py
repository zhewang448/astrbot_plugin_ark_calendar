import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sources.gacha import GachaSource


CN_TZ = ZoneInfo("Asia/Shanghai")


def _characters():
    return {f"char_{index}": {"name": f"角色{index}"} for index in range(100)}


def _torappu_pool():
    return {
        "gachaPoolId": "NORM_76_0_3",
        "gachaRuleType": "NORMAL",
        "openTime": 1787040000,
        "endTime": 1788206399,
        "gachaPoolName": "适合多种场合的强力干员",
        "gachaPoolSummary": "结束于9月1日 03:59",
        "gachaPoolDetail": "-",
    }


def _legacy_pool(pool_id="NORM_76_0_3"):
    return {
        "pool": {
            pool_id: {
                "id": pool_id,
                "name": "未知卡池",
                "type": "NORMAL",
                "start": 1787040000,
                "end": 1788206399,
            }
        }
    }


class FakeHttp:
    def __init__(self, torappu, legacy):
        self.torappu = torappu
        self.legacy = legacy

    async def json(self, url):
        if url.endswith("gacha_table.json") and "torappu" in url:
            if isinstance(self.torappu, Exception):
                raise self.torappu
            return self.torappu
        if "pool_info.json" in url:
            if isinstance(self.legacy, Exception):
                raise self.legacy
            return self.legacy
        if "weedy" in url:
            return {"gachaPoolClient": [{"gachaPoolId": "NORM_76_0_3"}]}
        return _characters()


def test_torappu_pool_enters_timeline_without_legacy_record():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, _legacy_pool("OLD_POOL")), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)

    pools = asyncio.run(source.pools(start, end, []))

    assert [pool["id"] for pool in pools] == ["NORM_76_0_3"]
    assert pools[0]["name"] == "未知卡池"
    assert pools[0]["type"] == "NORMAL"
    assert GachaSource.label(pools[0]["type"], pools[0]["name"]) == "未知卡池（NORMAL）"
    assert pools[0]["unpublished"] is True
    assert GachaSource.label("NORMAL", "", True) == "未公布"
    assert GachaSource.label("DOUBLE", "", True) == "未公布标准卡池"
    assert GachaSource.label("FESCLASSIC", "", True) == "未公布中坚甄选卡池"


def test_overview_can_confirm_joint_operation_name():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, _legacy_pool("OLD_POOL")), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)
    overview = [{
        "name": "联合行动-第二十三期",
        "start": "2026-08-18 16:00",
        "end": "2026-09-01 03:59",
        "six": ["干员甲", "干员乙"],
        "image": "https://example.invalid/banner.jpg",
    }]

    pools = asyncio.run(source.pools(start, end, overview))

    assert pools[0]["name"] == "联合行动-第二十三期"
    assert GachaSource.label(pools[0]["type"], pools[0]["name"]) == "联合行动"
    assert pools[0]["six"] == ["干员甲", "干员乙"]


def test_formal_legacy_name_wins_over_prts_short_name():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, {
        "pool": {
            "NORM_76_0_3": {
                "id": "NORM_76_0_3",
                "name": "联合行动-第二十三期",
                "type": "NORMAL",
                "start": 1787040000,
                "end": 1788206399,
            }
        }
    }), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)
    overview = [{
        "name": "联合行动23",
        "start": "2026-08-18 16:00",
        "end": "2026-09-01 03:59",
        "six": [],
        "image": "",
    }]

    pools = asyncio.run(source.pools(start, end, overview))

    assert pools[0]["name"] == "联合行动-第二十三期"


def test_formal_selection_name_wins_over_prts_short_name():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, {
        "pool": {
            "NORM_76_0_3": {
                "id": "NORM_76_0_3",
                "name": "中坚甄选-第一十四期",
                "type": "FESCLASSIC",
                "start": 1787040000,
                "end": 1788206399,
            }
        }
    }), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)
    overview = [{
        "name": "甄选14",
        "start": "2026-08-18 16:00",
        "end": "2026-09-01 03:59",
        "six": [],
        "image": "",
    }]

    pools = asyncio.run(source.pools(start, end, overview))

    assert pools[0]["name"] == "中坚甄选-第一十四期"


def test_numeric_overview_name_does_not_replace_formal_name():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, {
        "pool": {
            "NORM_76_0_3": {
                "id": "NORM_76_0_3",
                "name": "常驻标准寻访-第一百九十一期",
                "type": "NORMAL",
                "start": 1787040000,
                "end": 1788206399,
            }
        }
    }), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)
    overview = [{
        "name": "191",
        "start": "2026-08-18 16:00",
        "end": "2026-09-01 03:59",
        "six": [],
        "image": "https://example.invalid/banner.jpg",
    }]

    pools = asyncio.run(source.pools(start, end, overview))

    assert pools[0]["name"] == "常驻标准寻访-第一百九十一期"


@pytest.mark.parametrize(("pool_type", "period", "expected"), [
    ("DOUBLE", "196", "常驻标准寻访-第196期"),
    ("CLASSIC_DOUBLE", "76", "中坚寻访-第76期"),
    ("CLASSIC", "76", "中坚寻访-第76期"),
    ("FESCLASSIC", "76", "中坚甄选-第76期"),
])
def test_numeric_prts_period_name_is_recovered_from_pool_type(pool_type, period, expected):
    raw_pool = {**_torappu_pool(), "gachaRuleType": pool_type}
    overview = [{
        "name": period,
        "start": "2026-08-18 16:00",
        "end": "2026-09-01 03:59",
        "six": ["干员甲", "干员乙"],
        "image": "https://example.invalid/banner.jpg",
    }]
    source = GachaSource(FakeHttp({"gachaPoolClient": [raw_pool]}, RuntimeError("legacy source retired")))

    pools = asyncio.run(source.pools(
        datetime(2026, 8, 18, tzinfo=CN_TZ), datetime(2026, 9, 2, tzinfo=CN_TZ), overview,
    ))

    assert pools[0]["name"] == expected
    assert pools[0]["unpublished"] is False
    assert pools[0]["image"] == overview[0]["image"]
    assert pools[0]["six"] == overview[0]["six"]


def test_torappu_failure_falls_back_to_legacy_timeline():
    source = GachaSource(FakeHttp(RuntimeError("temporary"), _legacy_pool()), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)

    pools = asyncio.run(source.pools(start, end, []))

    assert [pool["id"] for pool in pools] == ["NORM_76_0_3"]
    assert source.last_source_states[0]["status"] == "fallback"


@pytest.mark.parametrize("url", ["", "   ", GachaSource.RETIRED_POOL_INFO_URL])
def test_disabled_legacy_source_is_not_requested_or_reported(url):
    class RecordingHttp(FakeHttp):
        def __init__(self):
            super().__init__({"gachaPoolClient": [_torappu_pool()]}, RuntimeError("HTTP 404"))
            self.urls = []

        async def json(self, url):
            self.urls.append(url)
            return await super().json(url)

    http = RecordingHttp()
    source = GachaSource(http, url)
    pools = asyncio.run(source.pools(
        datetime(2026, 8, 18, tzinfo=CN_TZ),
        datetime(2026, 9, 2, tzinfo=CN_TZ),
        [{"name": "联合行动23", "start": "2026-08-18 16:00", "end": "2026-09-01 03:59", "six": ["干员甲"]}],
    ))

    assert all(url and "pool_info.json" not in url for url in http.urls)
    assert pools[0]["name"] == "联合行动23"
    assert pools[0]["six"] == ["干员甲"]
    assert [state["name"] for state in source.last_source_states] == [
        "Torappu / gacha_table.json", "PRTS Gacha Server Data", "明日方舟角色数据",
    ]
    assert all(state["ok"] for state in source.last_source_states)


@pytest.mark.parametrize("torappu", [RuntimeError("Torappu offline"), {}])
def test_disabled_legacy_does_not_hide_timeline_failure(torappu):
    source = GachaSource(FakeHttp(torappu, _legacy_pool()))
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(source.pools(
            datetime(2026, 8, 18, tzinfo=CN_TZ), datetime(2026, 9, 2, tzinfo=CN_TZ), [],
        ))
    assert source.last_source_states[0]["ok"] is False
    assert source.last_source_states[0]["status"] == "failed"


def test_custom_legacy_failure_is_still_reported():
    source = GachaSource(FakeHttp({"gachaPoolClient": [_torappu_pool()]}, RuntimeError("mirror offline")), "pool_info.json")
    pools = asyncio.run(source.pools(
        datetime(2026, 8, 18, tzinfo=CN_TZ), datetime(2026, 9, 2, tzinfo=CN_TZ), [],
    ))
    assert pools
    state = source.last_source_states[1]
    assert state["name"] == "ArknightsGachaData"
    assert state["ok"] is False
    assert state["message"] == "mirror offline"


def test_fesclassic_is_labeled_as_core_selection():
    assert GachaSource.label("FESCLASSIC") == "中坚甄选"
