"""从官方 B站寻访公告缓存裁剪后的卡池图，仅在订阅和独立开关同时开启时使用。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import config_section
from .image_scale import MAX_IMAGE_PIXELS

CN_TZ = ZoneInfo("Asia/Shanghai")
DATE_TIME = re.compile(
    r"(?:(?P<year>20\d{2})[年./-])?(?P<month>\d{1,2})[月./-](?P<day>\d{1,2})日?"
    r"\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2})"
)
POOL_KINDS = (
    ("中坚甄选", "FESCLASSIC"), ("中坚寻访", "CLASSIC"),
    ("标准寻访", "STANDARD"), ("限定寻访", "LIMITED"),
    ("联动寻访", "LINKAGE"), ("联合行动", "JOINT"),
    ("定向寻访", "ATTAIN"), ("单人寻访", "SINGLE"),
)
POOL_TYPES = {
    "STANDARD": {"DOUBLE"}, "CLASSIC": {"CLASSIC", "CLASSIC_DOUBLE"},
    "FESCLASSIC": {"FESCLASSIC"}, "LIMITED": {"LIMITED"},
    "LINKAGE": {"LINKAGE"}, "ATTAIN": {"ATTAIN"}, "SINGLE": {"SINGLE"},
}


class BilibiliGachaBannerCache:
    CACHE_NAME = "bilibili_gacha_banners.json"
    MAX_ENTRIES = 32
    CROP_VERSION = 2

    def __init__(self, root: Path, cache, config: Any, logger) -> None:
        self.root = root
        self.cache = cache
        self.config = config
        self.logger = logger
        self._lock = asyncio.Lock()

    def enabled(self) -> bool:
        section = config_section(self.config, "bilibili_dynamic")
        targets = section.get("target_sid_list", [])
        if isinstance(targets, str):
            targets = targets.split(",")
        return bool(
            section.get("cache_gacha_banner", False)
            and section.get("push_enabled", False)
            and isinstance(targets, list)
            and any(str(target).strip() for target in targets)
        )

    def signature(self) -> str:
        """图片抵达或开关改变后，使已有快照和最终日报图自然失效。"""
        if not self.enabled():
            return ""
        records = self._records()
        return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _records(self) -> list[dict]:
        stored = self.cache.load(self.CACHE_NAME)
        if not isinstance(stored, dict) or stored.get("version") != self.CROP_VERSION:
            return []
        records = stored.get("records", [])
        return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []

    def _path(self, record: dict) -> Path | None:
        filename = record.get("file", "")
        if not isinstance(filename, str) or not re.fullmatch(r"gacha-[0-9a-f]{24}\.png", filename):
            return None
        path = self.root / filename
        try:
            return path if path.resolve().is_relative_to(self.root.resolve()) and path.is_file() else None
        except OSError:
            return None

    def _now(self) -> datetime:
        return datetime.now(CN_TZ)

    @staticmethod
    def _time(value: Any) -> datetime:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)

    def _announcement(self, dynamic: dict) -> dict | None:
        if dynamic.get("dynamic_type") == "repost" or not dynamic.get("images"):
            return None
        text = f"{dynamic.get('title', '')}\n{dynamic.get('description_text', '')}"[:6000]
        if "寻访" not in text or not any(word in text for word in ("预告", "概率提升", "限时开启", "起止时间", "卡池更新")):
            return None
        dates = list(DATE_TIME.finditer(text))
        if len(dates) != 2:
            # 同一条公告有多组时间时不猜测配图对应关系。
            return None
        now = self._now()
        try:
            published = self._time(dynamic.get("pub_date") or now)
            values = []
            for match in dates:
                fields = {name: int(match[name]) for name in ("month", "day", "hour", "minute")}
                explicit_year = match["year"]
                if explicit_year:
                    stamp = datetime(int(explicit_year), **fields, tzinfo=CN_TZ)
                elif values:
                    stamp = datetime(values[0].year, **fields, tzinfo=CN_TZ)
                    if stamp < values[0]:
                        stamp = stamp.replace(year=stamp.year + 1)
                else:
                    candidates = []
                    for year in (published.year - 1, published.year, published.year + 1):
                        try:
                            candidates.append(datetime(year, **fields, tzinfo=CN_TZ))
                        except ValueError:
                            continue
                    stamp = min(candidates, key=lambda value: abs(value - published))
                values.append(stamp)
            start, end = values
        except (ValueError, TypeError, OverflowError):
            return None
        if not start < end or not timedelta(hours=1) <= end - start <= timedelta(days=90):
            return None
        if end <= now or start > now + timedelta(days=90):
            return None
        kind = next((kind for label, kind in POOL_KINDS if label in text), "")
        return {
            "dynamic_id": str(dynamic.get("id", "")), "text": text, "kind": kind,
            "start": start.isoformat(), "end": end.isoformat(), "published": published.isoformat(),
        }

    @staticmethod
    def _crop(source: Path, target: Path) -> tuple[int, int, int, int] | None:
        from PIL import Image, ImageOps

        with Image.open(source) as original:
            width, height = original.size
            if width * height > MAX_IMAGE_PIXELS or width < 480 or height < 270:
                return None
            image = ImageOps.exif_transpose(original)
            width, height = image.size
            ratio = width / height
            if 1.70 <= ratio <= 1.86:
                top = 0  # 已经是游戏内横幅尺寸，不重复裁剪。
                bottom = height
            elif 1.40 <= ratio <= 1.62:
                # 公告栏、底部版权黑条随宽度缩放；720x488 去掉顶部80px和底部18px。
                top = round(width / 9)
                bottom = height - round(width / 40)
            else:
                return None
            box = (0, top, width, bottom)
            cropped = image.crop(box).convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                cropped.save(temporary, format="PNG")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return box

    async def capture(self, dynamics: list[dict], assets) -> int:
        if not self.enabled():
            return 0
        async with self._lock:
            original = self._records()
            records = []
            for record in original:
                try:
                    if self._time(record["end"]) > self._now() and self._path(record):
                        records.append(record)
                except (KeyError, ValueError, TypeError):
                    continue
            saved = 0
            for dynamic in reversed(dynamics):
                announcement = self._announcement(dynamic)
                if not announcement:
                    continue
                if any(record.get("dynamic_id") == announcement["dynamic_id"] for record in records):
                    continue
                for url in dynamic.get("images", [])[:4]:
                    try:
                        source = await assets.download(url)
                        digest = hashlib.sha256(f"{self.CROP_VERSION}:{announcement['dynamic_id']}:{url}".encode()).hexdigest()[:24]
                        target = self.root / f"gacha-{digest}.png"
                        box = await asyncio.to_thread(self._crop, source, target)
                        if box is None:
                            continue
                        records.append({**announcement, "file": target.name, "url": url, "crop_box": list(box)})
                        saved += 1
                        break
                    except Exception:
                        self.logger.warning("B站卡池公告图缓存失败，保留原数据源取图流程。", exc_info=True)
            records = sorted(records, key=lambda record: record.get("published", ""))[-self.MAX_ENTRIES:]
            if records != original:
                self.cache.save(self.CACHE_NAME, {"version": self.CROP_VERSION, "records": records})
                keep = {record["file"] for record in records}
                for record in original:
                    if record.get("file") not in keep:
                        path = self._path(record)
                        if path:
                            path.unlink(missing_ok=True)
            return saved

    def for_pool(self, pool: dict) -> Path | None:
        if not self.enabled():
            return None
        try:
            start, end = self._time(pool["start"]), self._time(pool["end"])
        except (KeyError, ValueError, TypeError):
            return None
        matches = []
        for record in self._records():
            try:
                if self._time(record["end"]) <= self._now():
                    continue
                if abs((self._time(record["start"]) - start).total_seconds()) > 120 or abs((self._time(record["end"]) - end).total_seconds()) > 120:
                    continue
                kind = record.get("kind", "")
                named = pool.get("name", "") not in ("", "未知卡池") and str(pool["name"]) in record.get("text", "")
                typed = pool.get("type") in POOL_TYPES.get(kind, set()) or (kind == "JOINT" and "联合行动" in str(pool.get("name", "")))
                if (typed or named) and self._path(record):
                    matches.append(record)
            except (KeyError, ValueError, TypeError):
                continue
        if not matches:
            return None
        # 多张同时间公告只有名称能消歧时才选择，避免标准池和中坚池串图。
        named = [record for record in matches if pool.get("name") and str(pool["name"]) in record.get("text", "")]
        if named:
            return self._path(max(named, key=lambda record: record.get("published", "")))
        return self._path(matches[0]) if len(matches) == 1 else None
