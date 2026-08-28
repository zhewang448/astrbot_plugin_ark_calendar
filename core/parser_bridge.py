"""通过 astrbot_plugin_parser 下载 B站视频的隔离桥接。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger

PARSER_PLUGIN_NAME = "astrbot_plugin_parser"
VIDEO_FETCH_TIMEOUT_SECONDS = 300.0


async def _fetch_video_path(context: Any, video_url: str) -> Path | None:
    """调用已注册 parser 插件；内部异常向外抛出，由带超时包装统一兜底。"""
    get_registered_star = getattr(context, "get_registered_star", None)
    if not callable(get_registered_star):
        logger.debug("B站视频解析：context 未提供 get_registered_star。")
        return None

    star_meta = get_registered_star(PARSER_PLUGIN_NAME)
    if not star_meta:
        logger.debug(f"B站视频解析：未注册 {PARSER_PLUGIN_NAME}。")
        return None

    if not getattr(star_meta, "activated", False):
        logger.debug(f"B站视频解析：{PARSER_PLUGIN_NAME} 未激活。")
        return None

    star_cls = getattr(star_meta, "star_cls", None)
    if not star_cls:
        logger.debug(f"B站视频解析：{PARSER_PLUGIN_NAME} 实例不可用。")
        return None

    parser_map = getattr(star_cls, "parser_map", None)
    key_pattern_list = getattr(star_cls, "key_pattern_list", None)
    if not isinstance(parser_map, dict) or not isinstance(key_pattern_list, (list, tuple)):
        logger.debug("B站视频解析：parser 插件缺少 parser_map/key_pattern_list。")
        return None

    keyword: str | None = None
    searched = None
    for item in key_pattern_list:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        candidate, pattern = item
        if not isinstance(candidate, str) or candidate not in video_url:
            continue
        search = getattr(pattern, "search", None)
        if not callable(search):
            continue
        matched = search(video_url)
        if matched:
            keyword, searched = candidate, matched
            break

    if keyword is None or searched is None:
        logger.debug(f"B站视频解析：parser 无法匹配链接 {video_url}")
        return None

    parser = parser_map.get(keyword)
    parse_method = getattr(parser, "parse", None)
    if parser is None or not callable(parse_method):
        logger.debug(f"B站视频解析：关键词 {keyword} 没有 parser.parse。")
        return None

    parse_result = await parse_method(keyword, searched)
    video_contents = getattr(parse_result, "video_contents", None)
    if not isinstance(video_contents, list) or not video_contents:
        logger.debug("B站视频解析：解析结果中没有视频内容。")
        return None

    get_path = getattr(video_contents[0], "get_path", None)
    if not callable(get_path):
        logger.debug("B站视频解析：视频内容没有 get_path。")
        return None

    video_path = await get_path()
    if not isinstance(video_path, Path) or not video_path.is_file():
        logger.debug(f"B站视频解析：本地视频无效：{video_path!r}")
        return None

    logger.debug(f"B站视频解析：已取得视频文件 {video_path}")
    return video_path


async def fetch_video_path(context: Any, video_url: str) -> Path | None:
    """解析并等待视频落盘；任何失败都返回 None，不干扰图文推送。"""
    if not video_url:
        return None

    try:
        return await asyncio.wait_for(
            _fetch_video_path(context, video_url),
            timeout=VIDEO_FETCH_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            f"B站视频解析超时（{VIDEO_FETCH_TIMEOUT_SECONDS:g} 秒）：{video_url}"
        )
    except Exception:
        logger.warning(f"B站视频解析失败：{video_url}", exc_info=True)
    return None
