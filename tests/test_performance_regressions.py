from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "ark_calendar_performance_tests"


def _load_package() -> None:
    if PACKAGE_NAME in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec and spec.loader
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)


_load_package()
from ark_calendar_performance_tests.core.assets import AssetCache  # noqa: E402
from ark_calendar_performance_tests.core.service import CalendarService  # noqa: E402
from ark_calendar_performance_tests.sources.http import HttpClient, ResponseTooLarge  # noqa: E402


def test_birthday_indexes_support_exact_partial_and_date_lookup() -> None:
    async def scenario() -> None:
        service = CalendarService.__new__(CalendarService)
        service._birthdays = []
        service._operator_index = {"阿米娅": {"profession": "术师", "rarity": 5}}
        service._set_birthdays([
            {"name": "阿米娅", "birthday": {"month": 12, "day": 23}},
            {"name": "阿米娅（近卫）", "birthday": {"month": 12, "day": 23}},
        ])

        async def no_op() -> None:
            return None

        service._ensure_reference_data = no_op
        operator, candidates = await service.find_operator("阿米娅")
        assert operator and operator.name == "阿米娅"
        assert candidates == []

        operator, candidates = await service.find_operator("近卫")
        assert operator and operator.name == "阿米娅（近卫）"
        assert candidates == []
        assert [item["name"] for item in service._birthdays_by_date[(12, 23)]] == [
            "阿米娅",
            "阿米娅（近卫）",
        ]

        # 兼容直接替换 _birthdays 的旧测试/调用路径：下次查询会自动重建索引。
        service._birthdays = [{"name": "能天使", "birthday": {"month": 5, "day": 1}}]
        operator, candidates = await service.find_operator("能天使")
        assert operator and operator.name == "能天使"
        assert candidates == []

    asyncio.run(scenario())


def test_avatar_url_cache_is_loaded_once_and_reused() -> None:
    class Cache:
        def __init__(self) -> None:
            self.loads = 0
            self.saved: dict[str, str] | None = None

        def load(self, name: str):
            assert name == "avatar_urls.json"
            self.loads += 1
            return {"阿米娅": "https://example.test/amiya.png"}

        def save(self, name: str, value: dict[str, str]) -> None:
            assert name == "avatar_urls.json"
            self.saved = dict(value)

    class Prts:
        async def resolve_avatar_urls(self, names: list[str]) -> dict[str, str]:
            return {name: f"https://example.test/{name}.png" for name in names}

    async def scenario() -> None:
        service = CalendarService.__new__(CalendarService)
        service.cache = Cache()
        service.prts = Prts()
        service.logger = None
        first = await service._safe_avatar_urls(["阿米娅", "能天使"])
        second = await service._safe_avatar_urls(["阿米娅", "能天使"])
        assert service.cache.loads == 1
        assert first == second
        assert service.cache.saved and "能天使" in service.cache.saved

    asyncio.run(scenario())


def test_cached_image_validation_reads_only_magic_header() -> None:
    cache = AssetCache.__new__(AssetCache)
    artifact_root = ROOT / ".codex_artifacts"
    with tempfile.TemporaryDirectory(dir=artifact_root) as raw:
        temp_root = Path(raw)
        valid = temp_root / "cached.png"
        valid.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 1024)
        invalid = temp_root / "cached.bin"
        invalid.write_bytes(b"not-an-image")
        assert cache._valid_cached_file(valid)
        assert not cache._valid_cached_file(invalid)


def test_disk_pruning_is_eager_once_then_batched() -> None:
    cache = AssetCache.__new__(AssetCache)
    calls: list[int] = []
    cache._prune_disk_cache = lambda: calls.append(1)
    cache._maybe_prune_disk_cache()
    for _ in range(AssetCache.DISK_CACHE_PRUNE_INTERVAL - 1):
        cache._maybe_prune_disk_cache()
    assert calls == [1]
    cache._maybe_prune_disk_cache()
    assert calls == [1, 1]


def test_http_body_uses_streaming_buffer_and_keeps_limit() -> None:
    class Content:
        async def iter_chunked(self, size: int):
            assert size == HttpClient.CHUNK_BYTES
            yield b"abc"
            yield b"def"

    class Response:
        content_length = 6
        content = Content()

    async def oversized():
        class BigContent:
            async def iter_chunked(self, size: int):
                yield b"x" * (HttpClient.MAX_RESPONSE_BYTES + 1)

        class BigResponse:
            content_length = None
            content = BigContent()

        assert await HttpClient._read_limited_body(Response()) == b"abcdef"
        with pytest.raises(ResponseTooLarge):
            await HttpClient._read_limited_body(BigResponse())

    asyncio.run(oversized())
