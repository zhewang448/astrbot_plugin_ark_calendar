import asyncio
from datetime import datetime, timedelta
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from PIL import Image
import pytest

from core.bilibili_gacha_banner import BilibiliGachaBannerCache
from core.cache import JsonCache
from sources.gacha import GachaSource

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 4, 10, tzinfo=TZ)
LOGGER = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)


def config(**changes):
    return {"bilibili_dynamic": {
        "cache_gacha_banner": True, "push_enabled": True,
        "target_sid_list": ["p:Group:1"], **changes,
    }}


def dynamic(**changes):
    return {
        "id": "standard-banner", "dynamic_type": "image", "title": "【常驻标准寻访预告】",
        "description_text": "起止时间：11月05日04:00 ~ 11月19日03:59。本期以下干员获得概率提升。",
        "pub_date": NOW, "images": ["https://i0.hdslb.com/bfs/banner.png"], **changes,
    }


def pool(**changes):
    return {
        "id": "STD_NEW", "name": "常驻标准寻访", "type": "DOUBLE", "image": "",
        "start": datetime(2026, 11, 5, 4, tzinfo=TZ),
        "end": datetime(2026, 11, 19, 3, 59, 59, tzinfo=TZ), **changes,
    }


class Assets:
    def __init__(self, source):
        self.source = source
        self.downloads = []
        self.local = []
        self.remote_result = ""

    async def download(self, url):
        self.downloads.append(url)
        return self.source

    async def data_uri(self, _url, **_kwargs):
        return self.remote_result

    async def data_uri_local(self, path, **_kwargs):
        self.local.append(Path(path))
        return "data:image/png;base64,local-banner"


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "announcement.png"
    image = Image.new("RGB", (720, 488), "blue")
    image.paste("red", (0, 0, 720, 80))
    image.paste("green", (0, 470, 720, 488))
    image.save(source)
    store = BilibiliGachaBannerCache(tmp_path / "banners", JsonCache(tmp_path / "cache"), config(), LOGGER)
    store._now = lambda: NOW
    return store, Assets(source)


@pytest.mark.parametrize("changes", [{"cache_gacha_banner": False}, {"push_enabled": False}, {"target_sid_list": []}])
def test_opt_in_and_active_subscription_are_both_required(setup, changes):
    store, assets = setup
    store.config = config(**changes)
    assert asyncio.run(store.capture([dynamic()], assets)) == 0
    assert not assets.downloads
    assert store.for_pool(pool()) is None
    assert not store.root.exists()


@pytest.mark.parametrize("scale", [1, 2])
def test_announcement_header_and_footer_are_cropped_and_survive_reload(setup, scale):
    store, assets = setup
    if scale != 1:
        with Image.open(assets.source) as original:
            scaled = original.resize((720 * scale, 488 * scale), Image.Resampling.NEAREST)
        scaled.save(assets.source)
    before = store.signature()
    assert asyncio.run(store.capture([dynamic()], assets)) == 1
    target = store.for_pool(pool())
    with Image.open(target) as image:
        assert image.size == (720 * scale, 390 * scale)
        assert image.getpixel((0, 0)) == (0, 0, 255)
        assert image.getpixel((0, 390 * scale - 1)) == (0, 0, 255)
    assert store.signature() != before
    assert asyncio.run(store.capture([dynamic()], assets)) == 0
    assert len(assets.downloads) == 1
    reloaded = BilibiliGachaBannerCache(store.root, store.cache, store.config, LOGGER)
    reloaded._now = lambda: NOW
    assert reloaded.for_pool(pool()) == target
    reloaded.config["bilibili_dynamic"]["push_enabled"] = False
    assert reloaded.for_pool(pool()) is None


def test_old_crop_cache_is_rebuilt_without_footer(setup):
    store, assets = setup
    store.root.mkdir()
    legacy_path = store.root / ("gacha-" + "a" * 24 + ".png")
    with Image.open(assets.source) as source:
        source.crop((0, 80, 720, 488)).save(legacy_path)
    store.cache.save(store.CACHE_NAME, {"version": 1, "records": [{
        "dynamic_id": "standard-banner", "file": legacy_path.name, "kind": "STANDARD",
        "start": pool()["start"].isoformat(), "end": pool()["end"].isoformat(),
        "published": NOW.isoformat(), "text": dynamic()["title"],
    }]})
    assert store.for_pool(pool()) is None
    assert asyncio.run(store.capture([dynamic()], assets)) == 1
    path = store.for_pool(pool())
    assert path != legacy_path
    with Image.open(path) as cropped:
        assert cropped.size == (720, 390)


@pytest.mark.parametrize("changes", [
    {"type": "CLASSIC_DOUBLE", "name": "中坚寻访"},
    {"start": datetime(2026, 11, 6, 4, tzinfo=TZ)},
    {"end": datetime(2026, 11, 20, 3, 59, tzinfo=TZ)},
])
def test_banner_does_not_leak_to_another_pool_type_or_period(setup, changes):
    store, assets = setup
    asyncio.run(store.capture([dynamic()], assets))
    assert store.for_pool(pool(**changes)) is None


@pytest.mark.parametrize("announcement", [
    dynamic(title="新增时装", description_text="11月05日04:00 ~ 11月19日03:59"),
    dynamic(description_text="寻访预告，详见图片"),
    dynamic(dynamic_type="repost"),
    dynamic(description_text="起止时间：10月05日04:00 ~ 10月19日03:59"),
    dynamic(description_text="起止时间：11月05日04:00 ~ 11月19日03:59；12月01日04:00 ~ 12月15日03:59"),
])
def test_non_matching_and_ambiguous_announcements_are_ignored(setup, announcement):
    store, assets = setup
    assert asyncio.run(store.capture([announcement], assets)) == 0
    assert not assets.downloads


def test_existing_game_banner_is_not_cropped_again(setup):
    store, assets = setup
    Image.new("RGB", (720, 405), "green").save(assets.source)
    assert asyncio.run(store.capture([dynamic()], assets)) == 1
    with Image.open(store.for_pool(pool())) as image:
        assert image.size == (720, 405)


def test_bad_image_does_not_block_later_matching_image(setup):
    store, assets = setup
    valid = assets.source
    invalid = valid.with_name("broken.png")
    invalid.write_bytes(b"not an image")

    async def download(url):
        return invalid if "broken" in url else valid

    assets.download = download
    assert asyncio.run(store.capture([dynamic(images=["https://example.invalid/broken.png", "https://example.invalid/banner.png"])], assets)) == 1
    assert store.for_pool(pool()) is not None


def test_ended_banner_is_pruned_and_never_reused(setup):
    store, assets = setup
    asyncio.run(store.capture([dynamic()], assets))
    path = store.for_pool(pool())
    store._now = lambda: NOW + timedelta(days=16)
    assert store.for_pool(pool()) is None
    asyncio.run(store.capture([], assets))
    assert not path.exists()


def test_cross_year_announcement_matches_next_year_pool(setup):
    store, assets = setup
    store._now = lambda: datetime(2026, 12, 25, tzinfo=TZ)
    announcement = dynamic(pub_date=store._now(), description_text="起止时间：12月30日04:00 ~ 01月13日03:59")
    assert asyncio.run(store.capture([announcement], assets)) == 1
    target_pool = pool(start=datetime(2026, 12, 30, 4, tzinfo=TZ), end=datetime(2027, 1, 13, 3, 59, tzinfo=TZ))
    assert store.for_pool(target_pool) is not None


def test_leap_day_announcement_uses_the_valid_year(setup):
    store, assets = setup
    store._now = lambda: datetime(2028, 2, 25, tzinfo=TZ)
    announcement = dynamic(pub_date=store._now(), description_text="起止时间：02月29日04:00 ~ 03月14日03:59")
    assert asyncio.run(store.capture([announcement], assets)) == 1
    target_pool = pool(start=datetime(2028, 2, 29, 4, tzinfo=TZ), end=datetime(2028, 3, 14, 3, 59, tzinfo=TZ))
    assert store.for_pool(target_pool) is not None


def service_class():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    try:
        return importlib.import_module(f"{root.name}.core.service").CalendarService
    finally:
        sys.path.remove(str(root.parent))


def test_report_uses_cached_banner_until_original_source_recovers(setup):
    store, assets = setup
    asyncio.run(store.capture([dynamic()], assets))
    Service = service_class()
    service = Service.__new__(Service)
    service.assets, service.prts = assets, object()
    service.gacha = SimpleNamespace(label=GachaSource.label)
    service.config, service.bilibili_banners = store.config, store
    service.last_snapshot = None
    items = asyncio.run(service._build_gacha_items([pool()]))
    assert items[0].image == items[0].detail_image == "data:image/png;base64,local-banner"
    assets.local.clear()
    assets.remote_result = "data:image/png;base64,original-source"
    items = asyncio.run(service._build_gacha_items([pool(image="https://prts.wiki/banner.png")]))
    assert items[0].image == items[0].detail_image == assets.remote_result
    assert not assets.local


def test_banner_arrival_and_disabling_setting_invalidate_snapshot_signature(setup):
    store, assets = setup
    Service = service_class()
    service = Service.__new__(Service)
    service.config, service.bilibili_banners = store.config, store
    initial = service._data_config_hash()
    asyncio.run(store.capture([dynamic()], assets))
    updated = service._data_config_hash()
    assert updated != initial
    store.config["bilibili_dynamic"]["cache_gacha_banner"] = False
    assert service._data_config_hash() != updated
