import asyncio
from pathlib import Path

import pytest

from core.assets import AssetCache
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
