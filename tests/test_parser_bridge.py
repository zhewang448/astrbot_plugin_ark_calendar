from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace


def _install_astrbot_stubs() -> None:
    astrbot = ModuleType("astrbot")
    astrbot.__path__ = []
    api = ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = SimpleNamespace(
        debug=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    sys.modules.update({"astrbot": astrbot, "astrbot.api": api})


_install_astrbot_stubs()

from core.parser_bridge import fetch_video_path  # noqa: E402


def test_parser_plugin_missing_returns_none():
    class Context:
        def get_registered_star(self, _name):
            return None

    url = "https://www.bilibili.com/video/BV1videoTest"
    assert asyncio.run(fetch_video_path(Context(), url)) is None


def test_parser_plugin_inactive_returns_none():
    class Context:
        def get_registered_star(self, _name):
            return SimpleNamespace(activated=False, star_cls=None)

    url = "https://www.bilibili.com/video/BV1videoTest"
    assert asyncio.run(fetch_video_path(Context(), url)) is None


def test_empty_url_returns_none():
    assert asyncio.run(fetch_video_path(SimpleNamespace(), "")) is None
