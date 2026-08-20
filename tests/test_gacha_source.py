import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

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


def test_torappu_failure_falls_back_to_legacy_timeline():
    source = GachaSource(FakeHttp(RuntimeError("temporary"), _legacy_pool()), "pool_info.json")
    start = datetime(2026, 8, 18, 0, 0, tzinfo=CN_TZ)
    end = datetime(2026, 9, 2, 0, 0, tzinfo=CN_TZ)

    pools = asyncio.run(source.pools(start, end, []))

    assert [pool["id"] for pool in pools] == ["NORM_76_0_3"]
    assert source.last_source_states[0]["status"] == "fallback"


def test_fesclassic_is_labeled_as_core_selection():
    assert GachaSource.label("FESCLASSIC") == "中坚甄选"
