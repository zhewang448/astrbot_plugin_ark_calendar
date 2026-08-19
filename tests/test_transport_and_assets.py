import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.assets import AssetCache, AssetTooLarge
from sources.bilibili_dynamic import BilibiliDynamicSource
from sources.http import HttpClient, UnsafeRemoteUrl


def test_local_asset_encoding_rejects_paths_outside_trusted_roots(tmp_path: Path):
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    cache = AssetCache(asset_root, None)

    assert asyncio.run(cache.data_uri(str(outside))) == ""
    assert asyncio.run(cache.data_uri_local(outside)) == ""
    assert asyncio.run(
        cache.data_uri_local(outside, trusted_roots=(outside.parent,))
    ).startswith("data:image/png;base64,")


def test_http_client_validates_every_redirect_hop():
    class RedirectResponse:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def get(self, *_args, **_kwargs):
            return RedirectResponse()

    client = HttpClient(Session(), retries=0)
    with pytest.raises(UnsafeRemoteUrl):
        asyncio.run(client.text("https://example.com/start"))


def test_bilibili_dynamic_download_uses_extended_image_limit(tmp_path: Path):
    class AssetCache:
        logger = None

        def __init__(self):
            self.calls = []

        async def download(self, url, *, max_bytes):
            self.calls.append((url, max_bytes))
            path = tmp_path / "dynamic.jpg"
            path.write_bytes(b"\xff\xd8\xffimage")
            return path

    asset_cache = AssetCache()
    source = BilibiliDynamicSource(None, None, asset_cache)

    paths = asyncio.run(source._download_images(["http://i0.hdslb.com/bfs/new_dyn/test.jpg"]))

    assert paths == [str(tmp_path / "dynamic.jpg")]
    assert asset_cache.calls == [
        ("https://i0.hdslb.com/bfs/new_dyn/test.jpg", source.MAX_IMAGE_DOWNLOAD_BYTES)
    ]


def test_bilibili_dynamic_logs_failed_image_download():
    warnings = []

    class AssetCache:
        logger = SimpleNamespace(warning=warnings.append)

        async def download(self, *_args, **_kwargs):
            raise AssetTooLarge("图片超过上限")

    source = BilibiliDynamicSource(None, None, AssetCache())

    assert asyncio.run(source._download_images(["https://i0.hdslb.com/bfs/new_dyn/test.jpg?token=secret"])) == []
    assert warnings == ["B站动态图片下载失败：https://i0.hdslb.com/bfs/new_dyn/test.jpg（AssetTooLarge）"]

def test_bilibili_default_rsshub_instances_are_currently_usable_mirrors():
    assert BilibiliDynamicSource.DEFAULT_RSSHUB_INSTANCES == [
        "https://rsshub.liumingye.cn/bilibili/user/dynamic/161775300",
        "https://rsshub-balancer.virworks.moe/bilibili/user/dynamic/161775300",
    ]


def test_bilibili_dynamic_merges_concurrent_mirrors_and_deduplicates_links():
    class Http:
        async def text(self, url, timeout):
            assert timeout == 15
            return {
                "first": """<rss><channel>
                    <item><title>旧动态</title><link>https://t.bilibili.com/1</link><guid>first-old</guid><pubDate>Wed, 19 Aug 2026 08:00:00 GMT</pubDate></item>
                    <item><title>重复动态</title><link>https://t.bilibili.com/2</link><guid>first-shared</guid><pubDate>Wed, 19 Aug 2026 09:00:00 GMT</pubDate></item>
                </channel></rss>""",
                "second": """<rss><channel>
                    <item><title>重复动态</title><link>https://t.bilibili.com/2</link><guid>second-shared</guid><pubDate>Wed, 19 Aug 2026 09:00:00 GMT</pubDate></item>
                    <item><title>新动态</title><link>https://t.bilibili.com/3</link><guid>second-new</guid><pubDate>Wed, 19 Aug 2026 10:00:00 GMT</pubDate></item>
                </channel></rss>""",
            }[url]

    source = BilibiliDynamicSource(Http(), None, SimpleNamespace())
    source.rsshub_instances = ["first", "second"]

    dynamics = asyncio.run(source.recent_dynamics(limit=3))

    assert [dynamic["id"] for dynamic in dynamics] == [
        "https://t.bilibili.com/3",
        "https://t.bilibili.com/2",
        "https://t.bilibili.com/1",
    ]
    assert source.last_fetch_ok is True
