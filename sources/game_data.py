"""卡池时间轴与公招计算共用的游戏数据。

character_table.json 约 8 MB、解析后常驻约 26 MB，而两处调用方只用到少数字段，
因此只缓存派生出的角色摘要（几十 KB，内存 + 磁盘），缺少需要的角色时才回源。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")

CHARACTER_TABLE_URL = "https://torappu.prts.wiki/gamedata/latest/excel/character_table.json"
GACHA_TABLE_URL = "https://torappu.prts.wiki/gamedata/latest/excel/gacha_table.json"
# 角色摘要只保留这些字段；GachaSource 用 name，RecruitmentSource 用其余字段。
CHARACTER_FIELDS = ("name", "rarity", "profession", "position", "tagList")


class GameDataSource:
    CHARACTER_CACHE_NAME = "character_summary.json"
    # 新干员会以缺失 charId / 名称的形式触发即时回源，TTL 只用于兜底同步字段变化。
    CHARACTER_TTL = timedelta(hours=24)
    RECRUIT_NAMES_TTL = timedelta(hours=24)
    # 名单里偶尔会有角色表查不到的名字或尚未收录的 charId；缺项触发的回源至少间隔这么久，
    # 避免每次查询都重新下载 8 MB。
    MISSING_RETRY_INTERVAL = timedelta(hours=1)

    def __init__(self, http: HttpClient, cache: Any | None = None, logger: Any | None = None):
        self.http = http
        self.cache = cache
        self.logger = logger
        self._characters: dict[str, dict[str, Any]] | None = None
        self._characters_fetched_at: datetime | None = None
        self._characters_lock = asyncio.Lock()
        self._characters_attempted_at: datetime | None = None
        self._recruit_detail: str | None = None
        self._recruit_detail_fetched_at: datetime | None = None

    async def characters(
        self,
        required_ids: Iterable[str] = (),
        required_names: Iterable[str] = (),
        *,
        minimum: int = 1,
    ) -> tuple[dict[str, dict[str, Any]], Exception | None]:
        """返回 (角色摘要, 本次回源错误)。

        缓存缺少 required_ids / required_names、条目少于 minimum 或超过 TTL 时回源；
        回源失败但已有缓存时返回旧缓存并附带错误，由调用方决定如何标记数据源状态。
        """
        ids, names = set(required_ids), set(required_names)
        async with self._characters_lock:
            self._load_disk_cache()
            current = self._characters or {}
            if not self._needs_refresh(current, ids, names, minimum):
                return current, None
            self._characters_attempted_at = datetime.now(CN_TZ)
            try:
                table = await self.http.json(CHARACTER_TABLE_URL)
                summary = self._summarize(table)
                if not summary:
                    raise ValueError("明日方舟角色数据为空或格式异常")
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(f"明日方舟角色数据获取失败：{exc}")
                return current, exc
            self._characters = summary
            self._characters_fetched_at = datetime.now(CN_TZ)
            self._save_disk_cache()
            return summary, None

    async def gacha_table(self) -> Any:
        """每次都回源（卡池时间轴需要最新数据），顺带记录公招名单原文供公招计算复用。"""
        data = await self.http.json(GACHA_TABLE_URL)
        if isinstance(data, dict) and isinstance(data.get("recruitDetail"), str):
            self._recruit_detail = data["recruitDetail"]
            self._recruit_detail_fetched_at = datetime.now(CN_TZ)
        return data

    async def recruit_detail(self) -> str:
        """公招名单原文；最近一次 gacha_table 回源在 TTL 内时直接复用。"""
        fetched_at = self._recruit_detail_fetched_at
        if self._recruit_detail is not None and fetched_at and datetime.now(CN_TZ) - fetched_at < self.RECRUIT_NAMES_TTL:
            return self._recruit_detail
        try:
            await self.gacha_table()
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(f"公招名单获取失败：{exc}")
        return self._recruit_detail or ""

    def invalidate(self) -> None:
        """管理员强制刷新时调用：下次使用时重新回源。"""
        self._characters_fetched_at = None
        self._characters_attempted_at = None
        self._recruit_detail_fetched_at = None

    def _needs_refresh(self, current: dict[str, dict[str, Any]], ids: set[str], names: set[str], minimum: int) -> bool:
        fetched_at = self._characters_fetched_at
        if not current or len(current) < minimum or fetched_at is None:
            return True
        now = datetime.now(CN_TZ)
        if now - fetched_at >= self.CHARACTER_TTL:
            return True
        attempted_at = self._characters_attempted_at
        if attempted_at is not None and now - attempted_at < self.MISSING_RETRY_INTERVAL:
            return False
        if ids - current.keys():
            return True
        return bool(names - {str(item.get("name", "")) for item in current.values()})

    @staticmethod
    def _summarize(table: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(table, dict):
            return {}
        return {
            str(char_id): {key: item[key] for key in CHARACTER_FIELDS if key in item}
            for char_id, item in table.items()
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
        }

    def _load_disk_cache(self) -> None:
        if self._characters is not None or self.cache is None:
            return
        stored = self.cache.load(self.CHARACTER_CACHE_NAME)
        if not isinstance(stored, dict) or not isinstance(stored.get("data"), dict):
            return
        try:
            fetched_at = datetime.fromisoformat(str(stored.get("fetched_at", ""))).astimezone(CN_TZ)
        except (TypeError, ValueError):
            fetched_at = None
        self._characters = stored["data"]
        self._characters_fetched_at = fetched_at

    def _save_disk_cache(self) -> None:
        if self.cache is None or self._characters_fetched_at is None:
            return
        try:
            self.cache.save(self.CHARACTER_CACHE_NAME, {
                "fetched_at": self._characters_fetched_at.isoformat(),
                "data": self._characters,
            })
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning(f"明日方舟角色数据缓存写入失败：{exc}")
