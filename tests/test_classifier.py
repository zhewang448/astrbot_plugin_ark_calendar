from __future__ import annotations

from sources.bilibili_dynamic import BilibiliDynamicSource
from core.bilibili_media import extract_bilibili_video_url


def test_classifier_prioritizes_repost_over_video():
    source = BilibiliDynamicSource(None, None, None)
    html = '<a href="https://www.bilibili.com/video/BV1videoTest">PV</a>'
    assert source._classify_dynamic("标题", "//转发自: 官方", html=html) == "repost"


def test_classifier_detects_direct_video_link():
    source = BilibiliDynamicSource(None, None, None)
    html = '<a href="https://www.bilibili.com/video/BV1videoTest">视频</a>'
    assert source._classify_dynamic("标题", "视频", html=html) == "video"


def test_classifier_detects_plain_video_link():
    source = BilibiliDynamicSource(None, None, None)
    text = "视频链接：https://www.bilibili.com/video/BV1videoTest"
    assert source._classify_dynamic("标题", text, html=text) == "video"


def test_classifier_keeps_image_and_text():
    source = BilibiliDynamicSource(None, None, None)
    assert source._classify_dynamic("标题", "公告", images=["https://example.invalid/a.jpg"]) == "image"
    assert source._classify_dynamic("PV 预告", "仅标题关键词") == "text"


def test_shared_extractor_supports_rss_html_and_plain_text():
    html = '<p>视频链接：<a href="https://www.bilibili.com/video/BV1videoTest">链接</a></p>'
    assert extract_bilibili_video_url(html) == "https://www.bilibili.com/video/BV1videoTest"
    text = "视频链接：https://www.bilibili.com/video/BV1videoTest"
    assert extract_bilibili_video_url(text) == "https://www.bilibili.com/video/BV1videoTest"
    assert extract_bilibili_video_url("https://b23.tv/abc") is None
