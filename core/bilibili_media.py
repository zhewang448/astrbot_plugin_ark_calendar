"""B站动态视频链接的共享识别与提取工具。"""

from __future__ import annotations

import re
import warnings
from urllib.parse import urljoin

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

_VIDEO_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/(?:BV[0-9a-zA-Z]{10}|av\d+)(?:\?p=\d{1,3})?",
    re.IGNORECASE,
)


def extract_bilibili_video_url(description_html: str) -> str | None:
    """从 B站动态 RSS 描述 HTML 中提取可交给解析器的视频链接。"""
    if not description_html:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
            soup = BeautifulSoup(description_html, "html.parser")

        for anchor in soup.find_all("a"):
            href = str(anchor.get("href", "") or "").strip()
            if "bilibili.com/video/" in href:
                # RSS 中的 href 已是完整地址；这里顺手兜底协议相对链接。
                return urljoin("https://www.bilibili.com/", href)

        text = soup.get_text(" ", strip=True)
        match = _VIDEO_URL_PATTERN.search(text)
        return match.group(0) if match else None
    except Exception:
        return None
