from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import aiohttp

from .assets import AssetCache
from .cache import JsonCache
from .config import config_int, config_value
from .models import (
    BirthdayGroup,
    CalendarSnapshot,
    Operator,
    RefreshOutcome,
    SourceState,
    TimelineItem,
    TodayInfo,
    parse_iso,
)
from ..sources.anything_ics import AnythingIcsSource
from ..sources.gacha import GachaSource
from ..sources.http import HttpClient, PublicResolver
from ..sources.prts import PrtsSource, game_weekday

CN_TZ = ZoneInfo("Asia/Shanghai")

# 各类图片在 calendar.html 里的 CSS 显示尺寸（宽, 高），单位 px。
# 图片资源按 CSS 布局尺寸预缩放；最终截图的设备像素倍率由 render_device_scale_factor_level 控制。
# 内嵌前按这些尺寸等比缩小，避免把远大于显示尺寸的原图整份塞进请求体。
# 数值由 body width:1440px 与各容器的 padding/gap 推算，取整时向上取以留余量。
TIMELINE_IMAGE_BOX = (1022, 84)      # .bar img.bg，时间轴条背景（.bar 宽度可变，按最宽箱体取）
TIMELINE_PORTRAIT_BOX = (122, 122)   # .bar .portrait，height:145% of 84px
POOL_DETAIL_IMAGE_BOX = (640, 360)    # 两列详情卡图片区域，完整显示 16:9 卡面
OPERATOR_AVATAR_BOX = (73, 73)       # .op img，近期新增干员
BIRTHDAY_AVATAR_BOX = (88, 88)       # .birth-op img，当天生日干员
HIGHLIGHT_IMAGE_BOX = (100, 100)     # .highlight-item img，首页亮点（428px 面板 4 列，实测 96px）
STAGE_IMAGE_BOX = (176, 70)          # .stage-media img，物资/芯片关卡（4 列时 170px，取 176 覆盖两种列数）

# _hydrate_home_highlights 一次处理 5 个字段，但它们落在两种不同尺寸的容器里。
HOME_HIGHLIGHT_BOXES = {
    "resource_schedule": STAGE_IMAGE_BOX,
    "chip_schedule": STAGE_IMAGE_BOX,
    "voucher_exchange": HIGHLIGHT_IMAGE_BOX,
    "new_skins": HIGHLIGHT_IMAGE_BOX,
    "new_modules": HIGHLIGHT_IMAGE_BOX,
}


class CalendarService:
    # 类级默认值：绕过 __init__ 构造的实例（例如测试里用 __new__ 创建）
    # 仍然能读到该属性。
    plugin_version = "dev"

    SNAPSHOT_SCHEMA_VERSION = 5
    SOURCE_CACHE_SCHEMA_VERSION = 1
    CRITICAL_SOURCE_NAMES = frozenset({
        "anything-ics / 生日",
        "anything-ics / 活动",
        "PRTS / 首页",
        "Torappu / gacha_table.json",
    })
    SOURCE_CACHE_KIND = "ark_calendar_source_cache"
    MIN_TIMELINE_DAYS = 7
    MAX_TIMELINE_DAYS = 90
    EVENT_DETAIL_TTL = timedelta(hours=12)
    EVENT_DETAIL_MAX_STALE = timedelta(days=7)

    def __init__(self, plugin_dir: Path, data_dir: Path, config: dict, logger):
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir
        self.config = config
        self.logger = logger
        self.plugin_version = self._read_plugin_version()
        self.cache = JsonCache(data_dir / "cache")
        self.session: aiohttp.ClientSession | None = None
        self.http: HttpClient | None = None
        self.assets: AssetCache | None = None
        self.anything: AnythingIcsSource | None = None
        self.prts: PrtsSource | None = None
        self.gacha: GachaSource | None = None
        self.refresh_lock = asyncio.Lock()
        self._event_detail_semaphore = asyncio.Semaphore(4)
        self._birthdays: list[dict] = []
        self._birthday_index_source: list[dict] | None = None
        self._birthday_by_normalized_name: dict[str, dict] = {}
        self._birthday_by_display_name: dict[str, dict] = {}
        self._birthday_search_records: tuple[tuple[str, str, dict], ...] = ()
        self._birthday_names: tuple[str, ...] = ()
        self._birthdays_by_date: dict[tuple[int, int], tuple[dict, ...]] = {}
        self._operator_index: dict[str, dict] = {}
        self._avatar_url_cache: dict[str, str] | None = None
        self.last_snapshot: CalendarSnapshot | None = None
        self.last_known_good_snapshot: CalendarSnapshot | None = None
        self.last_refresh_error = ""
        self.last_refresh_quality = "failed"
        self.last_refresh_used_cache = False
        self.last_refresh_source_states: list[SourceState] = []
        self.last_refresh_finished_at = ""
        self.last_refresh_outcome = RefreshOutcome()

    async def initialize(self) -> None:
        timeout_seconds = self.int_value(
            "data_sources", "request_timeout_seconds", 15,
            minimum=5, maximum=300, legacy_key="request_timeout",
        )
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        proxy = self._http_proxy()
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=aiohttp.TCPConnector(resolver=PublicResolver()),
            trust_env=True,
            headers={
                "User-Agent": f"AstrBot-ArkCalendar/{self.plugin_version}",
                "Accept-Encoding": "identity",
            },
        )
        self.http = HttpClient(self.session, proxy=proxy)
        self.assets = AssetCache(self.data_dir / "assets", self.session, proxy=proxy, logger=self.logger)
        if proxy:
            self.logger.info("方舟日历网络请求已启用 AstrBot HTTP 代理。")
        self.anything = AnythingIcsSource(
            self.http,
            self.value("data_sources", "anything_ics_base_url", "https://proxy.avgt.ink/ics", "anything_ics_base_url"),
        )
        self.prts = PrtsSource(
            self.http,
            self.value("data_sources", "prts_base_url", "https://prts.wiki", "prts_base_url"),
        )
        self.gacha = GachaSource(
            self.http,
            self.value(
                "data_sources",
                "gacha_data_url",
                "https://raw.githubusercontent.com/s-yh-china/ArknightsGachaData/master/data/pool_info.json",
                "gacha_data_url",
            ),
        )
        cached = self.cache.load("snapshot.json")
        if isinstance(cached, dict):
            try:
                self.last_snapshot = CalendarSnapshot.from_dict(cached)
            except Exception:
                self.logger.warning("无法读取日历快照缓存。", exc_info=True)
        known_good = self.cache.load("last_known_good_snapshot.json")
        if isinstance(known_good, dict):
            try:
                self.last_known_good_snapshot = CalendarSnapshot.from_dict(known_good)
            except Exception:
                self.logger.warning("无法读取最近一次完整日历快照。", exc_info=True)
        if self.last_known_good_snapshot is None and self.last_snapshot is not None:
            if self._snapshot_refresh_quality(self.last_snapshot.source_states) == "fresh":
                self.last_known_good_snapshot = self.last_snapshot
        if self.last_snapshot is not None:
            self._publish_refresh_outcome(RefreshOutcome(
                quality=self.last_snapshot.refresh_quality,
                error=self.last_refresh_error,
                used_cache=any(state.used_cache for state in self.last_snapshot.source_states),
                source_states=list(self.last_snapshot.source_states),
                finished_at=self.last_refresh_finished_at,
            ))

    def _http_proxy(self) -> str:
        plugin_proxy = str(self.value("data_sources", "http_proxy", "", "http_proxy") or "").strip()
        if plugin_proxy:
            return plugin_proxy
        try:
            from astrbot.core import astrbot_config

            return str(astrbot_config.get("http_proxy", "") or "").strip()
        except Exception:
            return ""

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    def value(self, section: str, key: str, default: Any, legacy_key: str | None = None) -> Any:
        return config_value(self.config, section, key, default, legacy_key)

    def int_value(
        self,
        section: str,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
        legacy_key: str | None = None,
    ) -> int:
        return config_int(
            self.config, section, key, default,
            minimum=minimum, maximum=maximum, legacy_key=legacy_key,
        )

    def cache_ttl(self) -> timedelta:
        return timedelta(minutes=self.int_value(
            "cache_and_render", "data_cache_ttl_minutes", 120,
            minimum=1, maximum=10080, legacy_key="cache_ttl_minutes",
        ))

    def timeline_days(self) -> int:
        return self.int_value(
            "basic", "timeline_days", 28,
            minimum=self.MIN_TIMELINE_DAYS, maximum=self.MAX_TIMELINE_DAYS,
            legacy_key="timeline_days",
        )

    def show_unpublished_pools(self) -> bool:
        return bool(self.value("basic", "show_unpublished_pools", True, "show_unpublished_pools"))

    def _snapshot_data_config(self) -> dict[str, Any]:
        return {
            "timeline_days": self.timeline_days(),
            "include_recent_operators": bool(self.value("basic", "include_recent_operators", True, "include_recent_operators")),
            "include_long_term": bool(self.value("basic", "include_long_term", True, "include_long_term")),
            "pool_detail_cards": bool(self.value("basic", "pool_detail_cards", True, "pool_detail_cards")),
            "show_unpublished_pools": self.show_unpublished_pools(),
            "anything_ics_base_url": str(self.value("data_sources", "anything_ics_base_url", "", "anything_ics_base_url") or ""),
            "prts_base_url": str(self.value("data_sources", "prts_base_url", "", "prts_base_url") or ""),
            "gacha_data_url": str(self.value("data_sources", "gacha_data_url", "", "gacha_data_url") or ""),
        }

    def _data_config_hash(self) -> str:
        raw = json.dumps(self._snapshot_data_config(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_plugin_version(self) -> str:
        """从 metadata.yaml 读取版本号，仅读一次并缓存到 self.plugin_version。"""
        try:
            metadata = (self.plugin_dir / "metadata.yaml").read_text("utf-8")
            match = re.search(r"^version:\s*v?(.+?)\s*$", metadata, re.MULTILINE)
            return match.group(1) if match else "dev"
        except OSError:
            return "dev"

    def snapshot_fallback_max_age(self) -> timedelta:
        return timedelta(hours=self.int_value(
            "cache_and_render", "snapshot_fallback_max_age_hours", 12,
            minimum=1, maximum=168,
        ))

    def _can_use_snapshot(self, snapshot: CalendarSnapshot | None, max_age: timedelta) -> bool:
        if not snapshot:
            return False
        try:
            generated = parse_iso(snapshot.generated_at).astimezone(CN_TZ)
        except (TypeError, ValueError):
            return False
        now = self._now()
        return (
            snapshot.schema_version == self.SNAPSHOT_SCHEMA_VERSION
            and snapshot.data_config_hash == self._data_config_hash()
            and generated <= now
            and now.date() == generated.date()
            and now - generated <= max_age
        )

    def _save_source_cache(self, cache_name: str, data: Any, fetched_at: datetime | None = None) -> None:
        fetched = fetched_at or self._now()
        self.cache.save(cache_name, {
            "_cache_kind": self.SOURCE_CACHE_KIND,
            "schema_version": self.SOURCE_CACHE_SCHEMA_VERSION,
            "fetched_at": fetched.isoformat(),
            "data": data,
        })

    def _load_source_cache(self, cache_name: str) -> tuple[Any | None, datetime | None]:
        stored = self.cache.load(cache_name)
        if isinstance(stored, dict) and stored.get("_cache_kind") == self.SOURCE_CACHE_KIND:
            if stored.get("schema_version") != self.SOURCE_CACHE_SCHEMA_VERSION:
                return None, None
            try:
                fetched_at = parse_iso(str(stored.get("fetched_at", ""))).astimezone(CN_TZ)
            except (TypeError, ValueError):
                return stored.get("data"), None
            return stored.get("data"), fetched_at
        if stored is None:
            return None, None
        try:
            fetched_at = datetime.fromtimestamp(self.cache.path(cache_name).stat().st_mtime, CN_TZ)
        except OSError:
            fetched_at = None
        return stored, fetched_at

    @staticmethod
    def _cache_age_text(age: timedelta) -> str:
        seconds = max(0, int(age.total_seconds()))
        if seconds < 3600:
            return f"{max(1, seconds // 60)} 分钟"
        if seconds < 86400:
            return f"{seconds // 3600} 小时"
        return f"{seconds // 86400} 天"

    @staticmethod
    def _valid_birthdays(data: Any) -> bool:
        if not isinstance(data, list) or len(data) < 100:
            return False
        valid = 0
        for item in data:
            birthday = item.get("birthday") if isinstance(item, dict) else None
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip() or not isinstance(birthday, dict):
                continue
            month, day = birthday.get("month"), birthday.get("day")
            if isinstance(month, int) and isinstance(day, int) and 1 <= month <= 12 and 1 <= day <= 31:
                valid += 1
        return valid >= 100 and valid / len(data) >= 0.9

    @staticmethod
    def _valid_events(data: Any) -> bool:
        if not isinstance(data, list) or not data:
            return False
        valid = 0
        for item in data:
            try:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                    continue
                if parse_iso(str(item["end"])) < parse_iso(str(item["start"])):
                    continue
                valid += 1
            except (KeyError, TypeError, ValueError):
                continue
        return valid > 0 and valid / len(data) >= 0.8

    @staticmethod
    def _valid_home(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        resource_schedule = data.get("resource_schedule")
        chip_schedule = data.get("chip_schedule")
        if not isinstance(resource_schedule, list) or not isinstance(chip_schedule, list):
            return False

        expected_resources = {"作战记录", "技巧概要", "龙门币", "采购凭证", "碳&家具零件"}
        expected_chips = {"术师&狙击", "先锋&辅助", "医疗&重装", "近卫&特种"}

        def valid_schedule(items: list[Any], expected: set[str], minimum: int) -> bool:
            valid_names = {
                str(item.get("name", ""))
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("weekdays", []), list)
                and isinstance(item.get("open"), bool)
            }
            return len(items) >= minimum and len(valid_names & expected) >= minimum

        return (
            valid_schedule(resource_schedule, expected_resources, 4)
            and valid_schedule(chip_schedule, expected_chips, 4)
        )

    @staticmethod
    def _valid_operator_index(data: Any) -> bool:
        if not isinstance(data, dict) or len(data) < 100:
            return False
        valid = sum(1 for name, info in data.items() if isinstance(name, str) and name.strip() and isinstance(info, dict))
        return valid / len(data) >= 0.9

    @staticmethod
    def _valid_gacha_overview(data: Any) -> bool:
        if not isinstance(data, list) or not data:
            return False
        valid = 0
        for item in data:
            try:
                if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                    continue
                if datetime.strptime(str(item["end"]), "%Y-%m-%d %H:%M") < datetime.strptime(str(item["start"]), "%Y-%m-%d %H:%M"):
                    continue
                valid += 1
            except (KeyError, TypeError, ValueError):
                continue
        return valid > 0 and valid / len(data) >= 0.8

    @classmethod
    def _snapshot_refresh_quality(cls, states: list[SourceState]) -> str:
        if all(state.ok for state in states):
            return "fresh"
        if any(
            state.name in cls.CRITICAL_SOURCE_NAMES and state.status == "failed"
            for state in states
        ):
            return "failed"
        return "degraded"

    @staticmethod
    def _source_event_key(label: str, exc: Exception) -> str:
        root: BaseException = exc
        while root.__cause__ is not None:
            root = root.__cause__
        if isinstance(root, asyncio.TimeoutError):
            reason = "timeout"
        elif isinstance(root, aiohttp.ClientResponseError):
            reason = f"http_{root.status}"
        elif isinstance(exc, ValueError):
            reason = "data_invalid"
        else:
            reason = re.sub(r"(?<!^)(?=[A-Z])", "_", type(root).__name__).lower()
        stable_label = re.sub(r"[^0-9a-zA-Z一-龥]+", "_", label).strip("_")
        return f"source:{stable_label}:{reason}"

    def snapshot_is_fresh(self) -> bool:
        return self._snapshot_is_fresh(self.cache_ttl())

    def _publish_refresh_outcome(self, outcome: RefreshOutcome) -> RefreshOutcome:
        """把本次刷新结果同步到全局 last_refresh_*，供状态展示等旧调用方使用。"""
        self.last_refresh_outcome = outcome
        self.last_refresh_quality = outcome.quality
        self.last_refresh_error = outcome.error
        self.last_refresh_used_cache = outcome.used_cache
        self.last_refresh_source_states = list(outcome.source_states)
        self.last_refresh_finished_at = outcome.finished_at
        return outcome

    async def snapshot(self, force: bool = False) -> CalendarSnapshot:
        result, _ = await self.snapshot_with_outcome(force=force)
        return result

    async def snapshot_with_outcome(self, force: bool = False) -> tuple[CalendarSnapshot, RefreshOutcome]:
        """返回快照与"本次"刷新结果。

        调用方要判定异常告警时必须用这里返回的 outcome：全局 last_refresh_*
        会被其他并发任务的刷新覆盖，据此判定会把别的任务的故障算到自己头上。
        """
        ttl = self.cache_ttl()
        if not force and self._snapshot_is_fresh(ttl):
            self.logger.debug("方舟日历快照缓存命中。")
            return self.last_snapshot, self.last_refresh_outcome  # type: ignore[return-value]
        async with self.refresh_lock:
            if not force and self._snapshot_is_fresh(ttl):
                self.logger.debug("方舟日历快照缓存已由并发请求刷新。")
                return self.last_snapshot, self.last_refresh_outcome  # type: ignore[return-value]
            started = time.monotonic()
            self.logger.info(f"开始{'强制刷新' if force else '刷新'}方舟日历数据。")
            try:
                result = await self._build_snapshot()
                quality = self._snapshot_refresh_quality(result.source_states)
                result.refresh_quality = quality
                outcome = RefreshOutcome(
                    quality=quality,
                    used_cache=any(state.used_cache for state in result.source_states),
                    source_states=list(result.source_states),
                    finished_at=self._now().isoformat(),
                )

                if quality == "failed" and self._can_use_snapshot(
                    self.last_known_good_snapshot,
                    self.snapshot_fallback_max_age(),
                ):
                    self.cache.save("snapshot-degraded.json", result.to_dict())
                    outcome.quality = "fallback"
                    outcome.error = "关键数据源不可用"
                    outcome.used_cache = True
                    self.last_snapshot = self.last_known_good_snapshot
                    self.logger.warning("关键数据源不可用，已使用最近一次完整快照。")
                    self._publish_refresh_outcome(outcome)
                    return self.last_known_good_snapshot, outcome  # type: ignore[return-value]

                self.last_snapshot = result
                if quality == "fresh":
                    self.last_known_good_snapshot = result
                    self.cache.save("snapshot.json", result.to_dict())
                    self.cache.save("last_known_good_snapshot.json", result.to_dict())
                else:
                    self.cache.save("snapshot-degraded.json", result.to_dict())
                    outcome.error = "关键数据源不可用" if quality == "failed" else ""
                self.logger.info(
                    f"方舟日历数据刷新完成（{quality}），耗时 {time.monotonic() - started:.2f} 秒。"
                )
                self._publish_refresh_outcome(outcome)
                return result, outcome
            except Exception as exc:
                outcome = RefreshOutcome(
                    quality="failed",
                    error=self._short_error(exc),
                    finished_at=self._now().isoformat(),
                )
                self.logger.error(f"方舟日历刷新失败：{outcome.error}", exc_info=True)
                if self._can_use_snapshot(self.last_known_good_snapshot, self.snapshot_fallback_max_age()):
                    outcome.quality = "fallback"
                    outcome.used_cache = True
                    self.last_snapshot = self.last_known_good_snapshot
                    self.logger.warning("方舟日历刷新失败，已使用最近一次完整快照。")
                    self._publish_refresh_outcome(outcome)
                    return self.last_known_good_snapshot, outcome  # type: ignore[return-value]
                self._publish_refresh_outcome(outcome)
                raise

    def _snapshot_is_fresh(self, ttl: timedelta) -> bool:
        return self._can_use_snapshot(self.last_snapshot, ttl)

    async def find_operator(self, query: str) -> tuple[Operator | None, list[str]]:
        await self._ensure_reference_data()
        if getattr(self, "_birthday_index_source", None) is not self._birthdays:
            self._set_birthdays(self._birthdays)
        normalized = self.normalize_name(query)
        record = self._birthday_by_normalized_name.get(normalized)
        if record:
            return self._operator_summary(record), []
        candidates = [
            name
            for name, normalized_name, _ in self._birthday_search_records
            if normalized and normalized in normalized_name
        ]
        if not candidates:
            candidates = difflib.get_close_matches(
                query,
                self._birthday_names,
                n=5,
                cutoff=0.45,
            )
        if len(candidates) == 1:
            record = self._birthday_by_display_name.get(candidates[0])
            if record is not None:
                return self._operator_summary(record), []
        return None, candidates[:8]

    def _set_birthdays(self, data: Any) -> None:
        """保存生日数据并一次性建立查询与日期索引。"""
        birthdays = data if isinstance(data, list) else []
        by_normalized_name: dict[str, dict] = {}
        by_display_name: dict[str, dict] = {}
        search_records: list[tuple[str, str, dict]] = []
        names: list[str] = []
        by_date: dict[tuple[int, int], list[dict]] = {}
        for item in birthdays:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            normalized_name = self.normalize_name(name)
            if not normalized_name:
                continue
            by_normalized_name[normalized_name] = item
            by_display_name.setdefault(name, item)
            search_records.append((name, normalized_name, item))
            names.append(name)
            birthday = item.get("birthday") or {}
            month, day = birthday.get("month"), birthday.get("day")
            if isinstance(month, int) and isinstance(day, int):
                by_date.setdefault((month, day), []).append(item)
        self._birthdays = birthdays
        self._birthday_index_source = birthdays
        self._birthday_by_normalized_name = by_normalized_name
        self._birthday_by_display_name = by_display_name
        self._birthday_search_records = tuple(search_records)
        self._birthday_names = tuple(names)
        self._birthdays_by_date = {key: tuple(items) for key, items in by_date.items()}

    def _operator_summary(self, record: dict) -> Operator:
        birthday = record.get("birthday") or {}
        name = record["name"]
        info = self._operator_index.get(name, {})
        return Operator(
            name=name,
            birthday_month=birthday.get("month"),
            birthday_day=birthday.get("day"),
            profession=info.get("profession", ""),
            rarity=info.get("rarity"),
        )

    async def _ensure_reference_data(self) -> None:
        assert self.anything and self.prts
        if not self._birthdays:
            data, _ = await self._fetch_cached(
                "birthdays.json", "anything-ics / 生日", self.anything.birthdays(), [],
                self._valid_birthdays, timedelta(days=7),
            )
            self._set_birthdays(data)
        if not self._operator_index:
            data, _ = await self._fetch_cached(
                "operators.json", "PRTS / 干员一览", self.prts.operator_index(), {},
                self._valid_operator_index, timedelta(days=7),
            )
            self._operator_index = data

    async def _build_snapshot(self) -> CalendarSnapshot:
        assert self.anything and self.prts and self.gacha and self.assets
        now = self._now()
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=self.timeline_days())

        source_results = await asyncio.gather(
            self._fetch_cached("birthdays.json", "anything-ics / 生日", self.anything.birthdays(), [], self._valid_birthdays, timedelta(days=7)),
            self._fetch_cached("events.json", "anything-ics / 活动", self.anything.events(), [], self._valid_events, timedelta(hours=24)),
            self._fetch_cached("prts_home.json", "PRTS / 首页", self.prts.home(now), {}, self._valid_home, timedelta(hours=6)),
            self._fetch_cached("operators.json", "PRTS / 干员一览", self.prts.operator_index(), {}, self._valid_operator_index, timedelta(days=7)),
            self._fetch_cached("gacha_overview.json", "PRTS / 卡池一览", self.prts.gacha_overview(), [], self._valid_gacha_overview, timedelta(hours=24)),
        )
        birthdays, events_raw, home, operator_index, overview = [item[0] for item in source_results]
        source_states = [item[1] for item in source_results]
        self._set_birthdays(birthdays)
        self._operator_index = operator_index
        home = self._refresh_home_status(home, now)
        home = await self._hydrate_home_highlights(home)

        today_records = self._birthdays_by_date.get((now.month, now.day), ())
        upcoming_groups: list[BirthdayGroup] = []
        required_names = [item["name"] for item in today_records]
        for offset in range(1, 10):
            day = now + timedelta(days=offset)
            records = self._birthdays_by_date.get((day.month, day.day), ())
            if records:
                upcoming_groups.append(BirthdayGroup(
                    day.month,
                    day.day,
                    [Operator(name=item["name"], birthday_month=day.month, birthday_day=day.day) for item in records],
                ))
        required_names.extend(item.get("name", "") for item in home.get("recent", []))
        avatar_urls = await self._safe_avatar_urls(required_names)
        today_birthdays = [await self._operator_from_record(item, avatar_urls) for item in today_records]

        recent_operators: list[Operator] = []
        if self.value("basic", "include_recent_operators", True, "include_recent_operators"):
            for item in home.get("recent", [])[:4]:
                name = item.get("name", "")
                if not name:
                    continue
                info = operator_index.get(name, {})
                avatar = await self.assets.data_uri(
                    item.get("avatar") or avatar_urls.get(name, ""),
                    box=OPERATOR_AVATAR_BOX,
                )
                recent_operators.append(Operator(
                    name,
                    profession=info.get("profession", ""),
                    rarity=info.get("rarity"),
                    avatar=avatar,
                ))

        # 活动与卡池两条链路互不依赖，并行执行可缩短慢网络下的总耗时。
        # 任一条抛错时立即取消另一条：裸 gather 只会向上传播异常而不取消兄弟任务，
        # 会留下继续持有信号量、且异常无人取回的孤儿任务。
        events_task = asyncio.ensure_future(self._build_events(events_raw, start, end))
        gacha_task = asyncio.ensure_future(self._build_gacha_timeline(start, end, overview))
        branch_tasks = (events_task, gacha_task)
        _, pending = await asyncio.wait(set(branch_tasks), return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)
        # 必须先取回每个任务的异常再抛出：两条链路在同一次事件循环迭代中同时失败时
        # pending 为空、谁都不会被取消，若直接抛出第一个错误，另一个异常将无人取回，
        # asyncio 会在回收时打印 "Task exception was never retrieved"。
        errors = [self._task_error(task) for task in branch_tasks]
        first_error = next((error for error in errors if error is not None), None)
        if first_error is not None:
            raise first_error
        event_items, long_items = events_task.result()
        gacha_items, gacha_states = gacha_task.result()
        source_states.extend(gacha_states)

        today_info = TodayInfo(
            home.get("supplies", []),
            home.get("chips", []),
            home.get("alerts", []),
            home.get("resource_schedule", []),
            home.get("chip_schedule", []),
            home.get("voucher_exchange", []),
            home.get("new_skins", []),
            home.get("new_modules", []),
        )
        return CalendarSnapshot(
            generated_at=now.isoformat(),
            calendar_date=now.date().isoformat(),
            timeline_start=start.isoformat(),
            timeline_end=end.isoformat(),
            today_info=today_info,
            today_birthdays=today_birthdays,
            upcoming_birthdays=upcoming_groups,
            recent_operators=recent_operators,
            events=event_items,
            gacha_pools=gacha_items,
            long_term_events=long_items if self.value("basic", "include_long_term", True, "include_long_term") else [],
            source_states=source_states,
            schema_version=self.SNAPSHOT_SCHEMA_VERSION,
            data_config_hash=self._data_config_hash(),
        )

    @staticmethod
    def _refresh_home_status(home: dict, now: datetime) -> dict:
        result = dict(home)
        weekday = game_weekday(now)
        for key in ("resource_schedule", "chip_schedule"):
            schedules = []
            for item in home.get(key, []) or []:
                current = dict(item)
                allowed = current.get("weekdays", []) or []
                current["open"] = bool(
                    current.get("always_open")
                    or current.get("all_open")
                    or weekday in allowed
                )
                schedules.append(current)
            result[key] = schedules
        if result.get("resource_schedule"):
            result["supplies"] = [item["name"] for item in result["resource_schedule"] if item.get("open")]
        if result.get("chip_schedule"):
            result["chips"] = [item["name"] for item in result["chip_schedule"] if item.get("open")]
        return result

    async def _hydrate_home_highlights(self, home: dict) -> dict:
        assert self.assets
        result = dict(home)

        async def hydrate(item: dict, box: tuple[int, int]) -> dict:
            current = dict(item)
            current["image"] = await self.assets.data_uri(current.get("image", ""), box=box)
            return current

        for key in ("resource_schedule", "chip_schedule", "voucher_exchange", "new_skins", "new_modules"):
            items = [item for item in home.get(key, []) or [] if isinstance(item, dict)]
            box = HOME_HIGHLIGHT_BOXES[key]
            result[key] = list(await asyncio.gather(*(hydrate(item, box) for item in items)))
        return result

    async def _build_events(
        self,
        events_raw: list[dict],
        start: datetime,
        end: datetime,
    ) -> tuple[list[TimelineItem], list[TimelineItem]]:
        assert self.prts and self.assets
        selected: list[tuple[dict, datetime, datetime]] = []
        for raw in events_raw:
            try:
                item_start = parse_iso(raw["start"]).astimezone(CN_TZ)
                item_end = parse_iso(raw["end"]).astimezone(CN_TZ)
            except (KeyError, TypeError, ValueError):
                continue
            if item_end < start or item_start > end:
                continue
            selected.append((raw, item_start, item_end))
        details = await asyncio.gather(
            *(self._event_detail(raw["name"]) for raw, _, _ in selected),
        )
        normalized_details = [detail if isinstance(detail, dict) else {} for detail in details]
        images = await asyncio.gather(
            *(
                self.assets.data_uri(detail.get("image_url", ""), box=TIMELINE_IMAGE_BOX)
                for detail in normalized_details
            ),
        )
        event_items: list[TimelineItem] = []
        long_items: list[TimelineItem] = []
        for (raw, item_start, item_end), detail, image in zip(selected, normalized_details, images):
            duration = item_end - item_start
            model = TimelineItem(
                id=str(raw.get("id", raw["name"])),
                name=raw["name"],
                category="event",
                item_type=detail.get("type", "活动"),
                start=item_start.isoformat(),
                end=item_end.isoformat(),
                exchange_end=detail.get("exchange_end", ""),
                image=image,
                is_long_term=duration > timedelta(days=45),
            )
            (long_items if model.is_long_term else event_items).append(model)
        return event_items, long_items

    async def _build_gacha_timeline(
        self,
        start: datetime,
        end: datetime,
        overview: list[dict],
    ) -> tuple[list[TimelineItem], list[SourceState]]:
        """把加载卡池和构造时间轴条目串成一条可等待链路。"""
        pools_raw, states = await self._load_gacha_pools(start, end, overview)
        return await self._build_gacha_items(pools_raw), states

    @staticmethod
    def _task_error(task: "asyncio.Task[Any]") -> BaseException | None:
        """返回任务的异常；被取消的兄弟任务视为无异常。"""
        if task.cancelled():
            return None
        return task.exception()

    async def historical_snapshot(self, target_day: date) -> CalendarSnapshot:
        """按指定日期和配置长度构造历史快照；仅首页即时区块保留为空。"""
        assert self.anything and self.prts and self.gacha and self.assets
        target_now = datetime.combine(target_day, datetime.min.time(), CN_TZ).replace(hour=12)
        start = datetime.combine(target_day - timedelta(days=1), datetime.min.time(), CN_TZ)
        end = start + timedelta(days=self.timeline_days())
        await self._ensure_reference_data()
        events_raw, overview = await asyncio.gather(
            self.anything.events(),
            self.prts.gacha_overview(),
        )
        if not self._valid_events(events_raw):
            raise ValueError("anything-ics 活动数据格式异常")
        if not self._valid_gacha_overview(overview):
            raise ValueError("PRTS 卡池一览数据格式异常")
        events, long_events = await self._build_events(events_raw, start, end)
        pools_raw = await self.gacha.pools(start, end, overview)
        pools = await self._build_gacha_items(pools_raw)
        today_records = self._birthdays_by_date.get((target_day.month, target_day.day), ())
        upcoming_groups: list[BirthdayGroup] = []
        for offset in range(1, 10):
            day = target_now + timedelta(days=offset)
            records = self._birthdays_by_date.get((day.month, day.day), ())
            if records:
                upcoming_groups.append(BirthdayGroup(
                    day.month,
                    day.day,
                    [Operator(name=item["name"], birthday_month=day.month, birthday_day=day.day) for item in records],
                ))
        avatar_urls = await self._safe_avatar_urls([item["name"] for item in today_records])
        today_birthdays = [await self._operator_from_record(item, avatar_urls) for item in today_records]
        return CalendarSnapshot(
            generated_at=target_now.isoformat(),
            calendar_date=target_now.date().isoformat(),
            timeline_start=start.isoformat(),
            timeline_end=end.isoformat(),
            today_info=TodayInfo(),
            today_birthdays=today_birthdays,
            upcoming_birthdays=upcoming_groups,
            events=events,
            gacha_pools=pools,
            long_term_events=long_events,
            schema_version=self.SNAPSHOT_SCHEMA_VERSION,
            data_config_hash=self._data_config_hash(),
        )

    async def _load_gacha_pools(
        self,
        start: datetime,
        end: datetime,
        overview: list[dict],
    ) -> tuple[list[dict], list[SourceState]]:
        assert self.gacha
        now = self._now()
        try:
            pools = await self.gacha.pools(start, end, overview)
            self._save_source_cache("gacha_pools.json", [self._serialize_pool(item) for item in pools], now)
            states = [
                SourceState(
                    name=item["name"],
                    ok=bool(item["ok"]),
                    updated_at=now.isoformat(),
                    message=str(item.get("message", "") or ""),
                    event_key=str(item.get("event_key", "") or ""),
                    status=str(item.get("status", "fresh" if item.get("ok") else "failed")),
                )
                for item in self.gacha.last_source_states
            ]
            return pools, states
        except Exception as exc:
            states = [
                SourceState(
                    name=item["name"],
                    ok=bool(item["ok"]),
                    updated_at=now.isoformat(),
                    message=str(item.get("message", "") or ""),
                    event_key=str(item.get("event_key", "") or ""),
                    status=str(item.get("status", "fresh" if item.get("ok") else "failed")),
                )
                for item in self.gacha.last_source_states
            ]
            if not states:
                states.append(SourceState(
                    "ArknightsGachaData",
                    False,
                    now.isoformat(),
                    self._short_error(exc),
                    event_key=self._source_event_key("ArknightsGachaData", exc),
                    status="failed",
                ))
            cached, fetched_at = self._load_source_cache("gacha_pools.json")
            if isinstance(cached, list) and fetched_at and now - fetched_at <= timedelta(hours=24):
                pools = []
                for item in cached:
                    try:
                        pool = dict(item)
                        pool["start"] = parse_iso(pool["start"]).astimezone(CN_TZ)
                        pool["end"] = parse_iso(pool["end"]).astimezone(CN_TZ)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if pool["end"] >= start and pool["start"] <= end:
                        pools.append(pool)
                if pools:
                    age = self._cache_age_text(now - fetched_at)
                    for state in states:
                        if not state.ok:
                            state.updated_at = fetched_at.isoformat()
                            state.message = f"实时更新失败，已使用 {age} 前缓存：{state.message or self._short_error(exc)}"
                            state.status = "fallback"
                            state.used_cache = True
                    return pools, states
            raise

    async def _build_gacha_items(self, pools_raw: list[dict]) -> list[TimelineItem]:
        assert self.prts and self.gacha and self.assets
        previous = {item.id: item for item in self.last_snapshot.gacha_pools} if self.last_snapshot else {}
        primary_images = await asyncio.gather(
            *(
                self.assets.data_uri(pool.get("image", ""), box=TIMELINE_IMAGE_BOX)
                for pool in pools_raw
            ),
        )
        detail_images = []
        if self.value("basic", "pool_detail_cards", True, "pool_detail_cards"):
            detail_images = await asyncio.gather(
                *(self.assets.data_uri(pool.get("image", ""), box=POOL_DETAIL_IMAGE_BOX, quality=100, fit="contain", force_webp=True) for pool in pools_raw),
            )
        else:
            detail_images = [""] * len(pools_raw)
        result: list[TimelineItem] = []
        for pool, image, detail_image in zip(pools_raw, primary_images, detail_images):
            cached = previous.get(pool.get("id", ""))
            six = list(pool.get("six", [])) or (list(cached.six_star_up) if cached else [])
            weighted = list(pool.get("weighted", [])) or (list(cached.weighted_up) if cached else [])
            unpublished = bool(pool.get("unpublished")) or pool.get("name") == "未知卡池"
            item_type = self.gacha.label(pool.get("type", ""), pool.get("name", ""), unpublished)
            display_name = item_type if unpublished else pool.get("name", "")
            images: list[str] = []
            if not image and six:
                urls = await self._safe_avatar_urls(six[:2])
                images = list(await asyncio.gather(
                    *(
                        self.assets.data_uri(urls.get(name, ""), box=TIMELINE_PORTRAIT_BOX)
                        for name in six[:2]
                    ),
                ))
                images = [item for item in images if item]
            result.append(TimelineItem(
                id=pool.get("id", ""),
                name=display_name,
                category="gacha",
                item_type=item_type,
                start=pool["start"].isoformat(),
                end=pool["end"].isoformat(),
                image=image,
                detail_image=detail_image,
                images=images,
                six_star_up=six,
                weighted_up=weighted,
            ))
        return result

    async def _operator_from_record(
        self,
        record: dict,
        avatar_urls: dict[str, str] | None = None,
    ) -> Operator:
        assert self.assets
        name = record["name"]
        birthday = record.get("birthday") or {}
        info = self._operator_index.get(name, {})
        urls = avatar_urls or await self._safe_avatar_urls([name])
        avatar = await self.assets.data_uri(urls.get(name, ""), box=BIRTHDAY_AVATAR_BOX)
        return Operator(
            name=name,
            birthday_month=birthday.get("month"),
            birthday_day=birthday.get("day"),
            profession=info.get("profession", ""),
            rarity=info.get("rarity"),
            avatar=avatar,
        )

    async def _safe_avatar_urls(self, names: list[str]) -> dict[str, str]:
        assert self.prts
        unique_names = list(dict.fromkeys(name for name in names if name))
        cached = getattr(self, "_avatar_url_cache", None)
        if cached is None:
            loaded = self.cache.load("avatar_urls.json")
            cached = dict(loaded) if isinstance(loaded, dict) else {}
            self._avatar_url_cache = cached
        missing = [name for name in unique_names if not cached.get(name)]
        if missing:
            try:
                resolved = await self.prts.resolve_avatar_urls(missing)
                if resolved:
                    cached.update(resolved)
                    self.cache.save("avatar_urls.json", cached)
            except Exception:
                self.logger.warning("获取干员头像地址失败，已尝试使用缓存。", exc_info=True)
        return {name: cached[name] for name in unique_names if cached.get(name)}

    async def _event_detail(self, name: str) -> dict:
        assert self.prts
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]
        cache_name = f"event_detail_{digest}.json"
        cached, fetched_at = self._load_source_cache(cache_name)
        now = self._now()
        if isinstance(cached, dict) and fetched_at and now - fetched_at <= self.EVENT_DETAIL_TTL:
            return cached
        async with self._event_detail_semaphore:
            try:
                detail = await self.prts.event_detail(name)
                if isinstance(detail, dict) and detail:
                    self._save_source_cache(cache_name, detail, now)
                    return detail
            except Exception:
                self.logger.warning(f"获取活动“{name}”详情失败，已尝试使用缓存。", exc_info=True)
        if isinstance(cached, dict) and fetched_at and now - fetched_at <= self.EVENT_DETAIL_MAX_STALE:
            return cached
        return {}

    async def _fetch_cached(
        self,
        cache_name: str,
        label: str,
        request: Awaitable[Any],
        default: Any,
        validator: Callable[[Any], bool] | None = None,
        max_stale: timedelta = timedelta(hours=24),
    ) -> tuple[Any, SourceState]:
        now = self._now()
        try:
            data = await request
            if validator and not validator(data):
                raise ValueError(f"{label} 返回的数据为空或格式异常")
            self._save_source_cache(cache_name, data, now)
            return data, SourceState(label, True, now.isoformat(), "", status="fresh")
        except Exception as exc:
            cached, fetched_at = self._load_source_cache(cache_name)
            message = self._short_error(exc)
            event_key = self._source_event_key(label, exc)
            if cached is not None and fetched_at and (validator is None or validator(cached)):
                age = now - fetched_at
                if age <= max_stale:
                    return cached, SourceState(
                        label,
                        False,
                        fetched_at.isoformat(),
                        f"实时更新失败，已使用 {self._cache_age_text(age)} 前缓存：{message}",
                        event_key=event_key,
                        status="fallback",
                        used_cache=True,
                    )
                return default, SourceState(
                    label,
                    False,
                    fetched_at.isoformat(),
                    f"当前不可用，缓存已陈旧 {self._cache_age_text(age)}：{message}",
                    event_key=event_key,
                    status="failed",
                )
            return default, SourceState(
                label,
                False,
                "",
                f"当前不可用且无有效缓存：{message}",
                event_key=event_key,
                status="failed",
            )

    def _now(self) -> datetime:
        return datetime.now(CN_TZ)

    @staticmethod
    def _serialize_pool(pool: dict) -> dict:
        return {
            **pool,
            "start": pool["start"].isoformat(),
            "end": pool["end"].isoformat(),
        }

    @staticmethod
    def _short_error(exc: Exception) -> str:
        text = " ".join(str(exc).split())
        return text[:220] or type(exc).__name__

    @staticmethod
    def normalize_name(name: str) -> str:
        return "".join(name.strip().lower().replace("・", "·").split()).replace("·", "")
