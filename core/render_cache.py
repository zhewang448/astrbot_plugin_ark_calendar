from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import CalendarSnapshot, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def has_png_magic(path: Path) -> bool:
    """只读文件头校验 PNG 签名，避免把整张图片读入内存。"""
    try:
        with path.open("rb") as handle:
            return handle.read(8) == PNG_MAGIC
    except OSError:
        return False


def write_image(rendered: str | Path | bytes, target: Path) -> None:
    if isinstance(rendered, bytes):
        target.write_bytes(rendered)
        return
    source = Path(rendered)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"渲染器未返回可用图片文件：{rendered}")
    shutil.copyfile(source, target)


class CalendarImageCache:
    """持久化并复用最近一次渲染出的日历图片。"""

    MANIFEST_NAME = "calendar-current.json"

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest_cache: tuple[tuple[int, int], dict[str, Any]] | None = None

    @property
    def manifest_path(self) -> Path:
        return self.root / self.MANIFEST_NAME

    def signature(self, snapshot: CalendarSnapshot, display_config: dict[str, Any]) -> str:
        payload = {
            "snapshot": self._business_snapshot(snapshot),
            "display": display_config,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _business_snapshot(snapshot: CalendarSnapshot) -> dict[str, Any]:
        """只取会影响图片内容的字段，构造稳定的签名载荷。"""
        payload = snapshot.to_dict()
        payload.pop("generated_at", None)
        payload.pop("refresh_quality", None)
        source_states = payload.get("source_states", [])
        if isinstance(source_states, list):
            payload["source_states"] = [
                {
                    "name": item.get("name", ""),
                    "ok": bool(item.get("ok", True)),
                    "event_key": item.get("event_key", ""),
                    "status": item.get("status", "fresh"),
                    "used_cache": bool(item.get("used_cache", False)),
                }
                for item in source_states
                if isinstance(item, dict)
            ]
        return payload

    def lookup(self, snapshot: CalendarSnapshot, display_config: dict[str, Any], now: datetime | None = None) -> Path | None:
        manifest = self._load_manifest()
        if not manifest:
            return None
        current = now or datetime.now(CN_TZ)
        if manifest.get("signature") != self.signature(snapshot, display_config):
            return None
        if not self._is_manifest_current(manifest, current):
            return None
        return self._image_path(manifest)

    def fallback(
        self,
        max_age_hours: int | None = None,
        now: datetime | None = None,
        display_config: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any]] | None:
        manifest = self._load_manifest()
        if not manifest:
            return None
        image = self._image_path(manifest)
        if not image:
            return None
        if display_config is not None:
            requested_engine = str(display_config.get("render_engine", "astrbot"))
            cached_engine = str(manifest.get("render_engine", "astrbot"))
            if requested_engine != cached_engine:
                return None
        current = now or datetime.now(CN_TZ)
        if str(manifest.get("calendar_date", "")) != current.date().isoformat():
            return None
        if max_age_hours is not None:
            try:
                rendered_at = parse_iso(str(manifest.get("rendered_at") or manifest.get("snapshot_generated_at", ""))).astimezone(CN_TZ)
            except (TypeError, ValueError):
                return None
            if current - rendered_at > timedelta(hours=max(1, max_age_hours)):
                return None
        return image, manifest

    def store(
        self,
        rendered: str | Path | bytes,
        snapshot: CalendarSnapshot,
        display_config: dict[str, Any],
        max_age_minutes: int,
        keep_count: int,
    ) -> Path:
        signature = self.signature(snapshot, display_config)
        image_name = f"calendar-{signature}.png"
        target = self.root / image_name
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            self._write_image(rendered, temporary)
            if not temporary.exists() or temporary.stat().st_size <= 0:
                raise ValueError("渲染缓存图片为空")
            if not self._has_png_magic(temporary):
                raise ValueError("渲染器未返回 PNG 图片")
            temporary.replace(target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        generated = parse_iso(snapshot.generated_at).astimezone(CN_TZ)
        expires = min(
            generated + timedelta(minutes=max(1, max_age_minutes)),
            (generated + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        )
        manifest = {
            "signature": signature,
            "image": image_name,
            "snapshot_generated_at": snapshot.generated_at,
            "calendar_date": snapshot.calendar_date,
            "rendered_at": datetime.now(CN_TZ).isoformat(),
            "expires_at": expires.isoformat(),
            "render_engine": str(display_config.get("render_engine", "astrbot")),
        }
        temporary_manifest = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid4().hex}.tmp")
        try:
            temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
            temporary_manifest.replace(self.manifest_path)
        finally:
            try:
                temporary_manifest.unlink(missing_ok=True)
            except OSError:
                pass
        self._manifest_cache = None
        self._prune(max(1, keep_count))
        return target

    def status(self, snapshot: CalendarSnapshot | None, display_config: dict[str, Any]) -> dict[str, Any]:
        manifest = self._load_manifest()
        if not manifest:
            return {"state": "missing"}
        current = datetime.now(CN_TZ)
        # 图片只解析一次并复用于有效性判断，不再调用 lookup()，
        # 否则会重复解析图片一次。
        image = self._image_path(manifest)
        valid = bool(
            snapshot
            and image
            and manifest.get("signature") == self.signature(snapshot, display_config)
            and self._is_manifest_current(manifest, current)
        )
        return {
            "state": "valid" if valid else "stale",
            "image": str(image) if image else "",
            "snapshot_generated_at": str(manifest.get("snapshot_generated_at", "")),
            "rendered_at": str(manifest.get("rendered_at", "")),
            "expires_at": str(manifest.get("expires_at", "")),
        }

    def _load_manifest(self) -> dict[str, Any] | None:
        """读取 manifest，文件未变化时复用已解析的副本。

        每次调用都会重新校验 (mtime_ns, size) 指纹，因此其他进程改写过的
        manifest 仍能被及时发现。
        """
        try:
            stat = self.manifest_path.stat()
        except OSError:
            self._manifest_cache = None
            return None
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        cached = self._manifest_cache
        if cached is not None and cached[0] == fingerprint:
            return dict(cached[1])
        try:
            data = json.loads(self.manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            self._manifest_cache = None
            return None
        if not isinstance(data, dict):
            self._manifest_cache = None
            return None
        self._manifest_cache = (fingerprint, data)
        return dict(data)

    def _image_path(self, manifest: dict[str, Any]) -> Path | None:
        name = str(manifest.get("image", "") or "")
        if not name or Path(name).name != name:
            return None
        image = self.root / name
        try:
            if not image.is_file() or image.stat().st_size <= 8:
                return None
        except OSError:
            return None
        return image if self._has_png_magic(image) else None

    @staticmethod
    def _has_png_magic(path: Path) -> bool:
        return has_png_magic(path)

    @staticmethod
    def _is_manifest_current(manifest: dict[str, Any], now: datetime) -> bool:
        """只校验 manifest 是否在有效期内；图片由调用方解析。"""
        try:
            expiry = parse_iso(str(manifest.get("expires_at", ""))).astimezone(CN_TZ)
        except (TypeError, ValueError):
            return False
        return now < expiry and str(manifest.get("calendar_date", "")) == now.date().isoformat()

    @staticmethod
    def _write_image(rendered: str | Path | bytes, target: Path) -> None:
        write_image(rendered, target)

    def _prune(self, keep_count: int) -> None:
        images = sorted(self.root.glob("calendar-*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
        for image in images[keep_count:]:
            try:
                image.unlink()
            except OSError:
                pass


class HelpImageCache:
    """按自然日和渲染引擎缓存帮助长图。

    AstrBot 沿用历史文件名，Pillow 使用独立的 ``-pillow`` 后缀；切换
    引擎时不会把不同后端生成的长图混用。
    """

    MODES = ("full", "subscribe")
    ENGINE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _image_name(cls, mode: str, calendar_date: str, engine: str) -> str | None:
        if mode not in cls.MODES or not DATE_PATTERN.match(calendar_date):
            return None
        normalized = str(engine).lower().strip()
        if not cls.ENGINE_PATTERN.fullmatch(normalized):
            return None
        suffix = "" if normalized == "astrbot" else f"-{normalized}"
        return f"help-{mode}{suffix}-{calendar_date}.png"

    def image_path(self, mode: str, calendar_date: str, engine: str = "astrbot") -> Path | None:
        """返回指定后端当日缓存图片路径；不存在或无效时返回 None。"""
        name = self._image_name(mode, calendar_date, engine)
        if not name:
            return None
        image = self.root / name
        try:
            if not image.is_file() or image.stat().st_size <= 8:
                return None
        except OSError:
            return None
        return image if has_png_magic(image) else None

    def lookup(self, mode: str, now: datetime | None = None, engine: str = "astrbot") -> Path | None:
        current = now or datetime.now(CN_TZ)
        return self.image_path(mode, current.date().isoformat(), engine)

    def store(
        self,
        rendered: str | Path | bytes,
        mode: str,
        now: datetime | None = None,
        keep_days: int = 2,
        engine: str = "astrbot",
    ) -> Path | None:
        """写入当日缓存；mode 或 engine 非法时直接返回 None，不落盘。"""
        current = now or datetime.now(CN_TZ)
        name = self._image_name(mode, current.date().isoformat(), engine)
        if not name:
            return None
        target = self.root / name
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            write_image(rendered, temporary)
            if not temporary.exists() or temporary.stat().st_size <= 0:
                raise ValueError("帮助长图缓存为空")
            if not has_png_magic(temporary):
                raise ValueError("渲染器未返回 PNG 图片")
            temporary.replace(target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._prune(max(1, keep_days), str(engine).lower().strip())
        return target

    def invalidate(self, now: datetime | None = None) -> None:
        """删除当日所有渲染后端的帮助缓存，用于管理员强制刷新。"""
        current = now or datetime.now(CN_TZ)
        for mode in self.MODES:
            for image in self.root.glob(f"help-{mode}*-{current.date().isoformat()}.png"):
                try:
                    image.unlink(missing_ok=True)
                except OSError:
                    pass

    def status(self, now: datetime | None = None, engine: str = "astrbot") -> dict[str, str]:
        current = now or datetime.now(CN_TZ)
        return {
            mode: str(self.image_path(mode, current.date().isoformat(), engine) or "")
            for mode in self.MODES
        }

    def _prune(self, keep_days: int, engine: str) -> None:
        """每个 mode / engine 仅保留最近 keep_days 天的缓存图。"""
        suffix = "" if engine == "astrbot" else f"-{engine}"
        for mode in self.MODES:
            images = sorted(
                self.root.glob(f"help-{mode}{suffix}-*.png"),
                key=lambda item: item.name,
                reverse=True,
            )
            for image in images[keep_days:]:
                try:
                    image.unlink()
                except OSError:
                    pass
