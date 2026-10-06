import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from core.image_cache_manager import CalendarImageManager
from core.keyed_lock import KeyedLocks
from core.models import CalendarSnapshot
from core.render_cache import CalendarImageCache
from core.subscription import match_by_name
from sources.gacha import GachaSource
from sources.game_data import GameDataSource

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
LOGGER = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)


def _snapshot() -> CalendarSnapshot:
    now = datetime.now().astimezone()
    return CalendarSnapshot(
        generated_at=now.isoformat(),
        calendar_date=now.date().isoformat(),
        timeline_start=now.isoformat(),
        timeline_end=(now + timedelta(days=30)).isoformat(),
    )


class FakeRenderer:
    template_hash = "test"

    def __init__(self, image: Path):
        self.image = image
        self.calls = 0

    async def calendar(self, _snapshot):
        self.calls += 1
        return str(self.image)


def _image_manager(tmp_path: Path, renderer: FakeRenderer) -> CalendarImageManager:
    service = SimpleNamespace(cache_ttl=lambda: timedelta(minutes=120))
    return CalendarImageManager(CalendarImageCache(tmp_path / "render"), renderer, service, {}, LOGGER)


def test_cache_write_failure_still_returns_fresh_render(tmp_path: Path):
    rendered = tmp_path / "rendered.png"
    rendered.write_bytes(PNG)
    manager = _image_manager(tmp_path, FakeRenderer(rendered))

    def broken_store(*_args, **_kwargs):
        raise OSError("disk full")

    manager.render_cache.store = broken_store
    image, state, manifest = asyncio.run(manager.get_calendar_image(_snapshot(), {"render_image_type": "png"}))
    assert (image, state, manifest) == (str(rendered), "rendered", None)


def test_forced_render_is_cached_for_next_request(tmp_path: Path):
    rendered = tmp_path / "rendered.png"
    rendered.write_bytes(PNG)
    renderer = FakeRenderer(rendered)
    manager = _image_manager(tmp_path, renderer)
    snapshot = _snapshot()
    display = {"render_image_type": "png"}

    forced, forced_state, _ = asyncio.run(manager.get_calendar_image(snapshot, display, use_cache=False))
    again, again_state, _ = asyncio.run(manager.get_calendar_image(snapshot, display))
    assert forced_state == "rendered" and again_state == "cache"
    assert forced == again and renderer.calls == 1


def test_keyed_locks_serialize_same_key_and_release_entries():
    locks = KeyedLocks()
    order: list[str] = []

    async def worker(name: str):
        async with locks.hold("same"):
            order.append(f"{name}-start")
            await asyncio.sleep(0)
            order.append(f"{name}-end")

    async def main():
        await asyncio.gather(worker("a"), worker("b"), worker("c"))

    asyncio.run(main())
    assert order == ["a-start", "a-end", "b-start", "b-end", "c-start", "c-end"]
    assert len(locks) == 0


def test_match_by_name_prefers_exact_match_over_fuzzy():
    names = ["危机合约", "危机合约（活动商店）", "危机合约 · 熔火行动"]
    assert match_by_name(names, "危机合约", str) == ["危机合约"]
    assert match_by_name(names, "熔火", str) == ["危机合约 · 熔火行动"]
    assert match_by_name(names, "  ", str) == []


class GachaHttp:
    """torappu/legacy/weedy 均可用；角色表请求计数，第二次起可配置为失败。"""

    def __init__(self):
        self.character_calls = 0
        self.fail_characters = False

    async def json(self, url):
        if "torappu" in url and url.endswith("gacha_table.json"):
            return {"gachaPoolClient": [{
                "gachaPoolId": "P1", "gachaRuleType": "NORMAL", "gachaPoolName": "测试寻访",
                "openTime": 1787040000, "endTime": 1788206399,
            }]}
        if "pool_info.json" in url:
            return {"pool": {"P1": {"id": "P1", "name": "测试寻访", "type": "NORMAL", "start": 1787040000, "end": 1788206399}}}
        if "weedy" in url:
            return {"gachaPoolClient": [{"gachaPoolId": "P1", "gachaPoolDetail": {"detailInfo": {
                "upCharInfo": {"perCharList": [{"rarityRank": 5, "charIdList": ["char_1"]}]},
            }}}]}
        self.character_calls += 1
        if self.fail_characters:
            raise RuntimeError("character table down")
        return {f"char_{index}": {"name": f"角色{index}", "skills": ["x"] * 10} for index in range(120)}


def test_gacha_source_reuses_character_summary_between_refreshes():
    http = GachaHttp()
    source = GachaSource(http, "pool_info.json", game_data=GameDataSource(http))
    start = datetime(2026, 8, 1).astimezone()
    end = start + timedelta(days=60)

    first = asyncio.run(source.pools(start, end, []))
    second = asyncio.run(source.pools(start, end, []))
    assert first[0]["six"] == second[0]["six"] == ["角色1"]
    assert http.character_calls == 1
    assert source.last_source_states[3]["ok"] is True


def test_gacha_source_marks_character_fallback_when_refresh_fails_with_cache():
    http = GachaHttp()
    game_data = GameDataSource(http)
    source = GachaSource(http, "pool_info.json", game_data=game_data)
    start = datetime(2026, 8, 1).astimezone()
    end = start + timedelta(days=60)
    asyncio.run(source.pools(start, end, []))

    http.fail_characters = True
    game_data.invalidate()
    pools = asyncio.run(source.pools(start, end, []))
    state = source.last_source_states[3]
    assert pools[0]["six"] == ["角色1"]
    assert (state["ok"], state["status"]) == (False, "fallback")


def test_recruit_max_tags_defaults_to_five_and_zero_means_unlimited():
    import importlib
    import json
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    try:
        CalendarService = importlib.import_module(f"{root.name}.core.service").CalendarService
    finally:
        sys.path.remove(str(root.parent))

    service = CalendarService.__new__(CalendarService)
    for config, expected in (({}, 5), ({"basic": {"recruit_max_tags": 3}}, 3), ({"basic": {"recruit_max_tags": 0}}, None)):
        service.config = config
        assert service.recruit_max_tags() == expected

    item = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))["basic"]["items"]["recruit_max_tags"]
    assert (item["type"], item["default"]) == ("int", 5)
