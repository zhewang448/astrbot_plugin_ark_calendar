import base64
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin.core.assets import AssetCache, UnsafeAssetUrl


class AssetSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_literal_private_and_http_urls(self):
        cache = AssetCache(Path(tempfile.mkdtemp()), None)  # type: ignore[arg-type]
        with self.assertRaises(UnsafeAssetUrl):
            await cache._validate_remote_url("https://127.0.0.1/image.png")
        with self.assertRaises(UnsafeAssetUrl):
            await cache._validate_remote_url("http://example.com/image.png")

    def test_rejects_private_connected_peer(self):
        cache = AssetCache(Path(tempfile.mkdtemp()), None)  # type: ignore[arg-type]

        class Transport:
            def get_extra_info(self, name):
                return ("127.0.0.1", 443) if name == "peername" else None

        class Connection:
            transport = Transport()

        class Response:
            connection = Connection()

        with self.assertRaises(UnsafeAssetUrl):
            cache._validate_response_peer(Response())
    async def test_allows_local_font_file(self):
        cache = AssetCache(Path(tempfile.mkdtemp()), None)  # type: ignore[arg-type]
        font = cache.root / "font.otf"
        font.write_bytes(b"local-font")
        self.assertTrue((await cache.data_uri(str(font))).startswith("data:font/otf;base64,"))

    async def test_accepts_small_valid_png_data_uri(self):
        cache = AssetCache(Path(tempfile.mkdtemp()), None)  # type: ignore[arg-type]
        payload = bytes.fromhex("89504e470d0a1a0a") + b"example"
        source = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
        self.assertEqual(await cache.data_uri(source), source)
