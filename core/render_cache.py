from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import CalendarSnapshot, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
    ) -> tuple[Path, dict[str, Any]] | None:
        manifest = self._load_manifest()
        if not manifest:
            return None
        image = self._image_path(manifest)
        if not image:
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
        """只读文件头校验 PNG 签名，避免把整张图片读入内存。"""
        try:
            with path.open("rb") as handle:
                return handle.read(8) == PNG_MAGIC
        except OSError:
            return False

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
        if isinstance(rendered, bytes):
            target.write_bytes(rendered)
            return
        source = Path(rendered)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"渲染器未返回可用图片文件：{rendered}")
        shutil.copyfile(source, target)

    def _prune(self, keep_count: int) -> None:
        images = sorted(self.root.glob("calendar-*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
        for image in images[keep_count:]:
            try:
                image.unlink()
            except OSError:
                pass
