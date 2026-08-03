from __future__ import annotations

import asyncio
import difflib
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import aiohttp

from .assets import AssetCache
from .cache import JsonCache
from .models import BirthdayGroup, CalendarSnapshot, Operator, SourceState, TimelineItem, TodayInfo, parse_iso
from ..sources.anything_ics import AnythingIcsSource
from ..sources.gacha import GachaSource
from ..sources.http import HttpClient
from ..sources.prts import PrtsSource

CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarService:
    def __init__(self, plugin_dir: Path, data_dir: Path, config: dict, logger):
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir
        self.config = config
        self.logger = logger
        self.cache = JsonCache(data_dir / "cache")
        self.session: aiohttp.ClientSession | None = None
        self.http: HttpClient | None = None
        self.assets: AssetCache | None = None
        self.anything: AnythingIcsSource | None = None
        self.prts: PrtsSource | None = None
        self.gacha: GachaSource | None = None
        self.refresh_lock = asyncio.Lock()
        self._birthdays: list[dict] = []
        self._operator_index: dict[str, dict] = {}
        self.last_snapshot: CalendarSnapshot | None = None

    async def initialize(self) -> None:
        timeout = aiohttp.ClientTimeout(total=max(5, int(self.config.get("request_timeout", 15))))
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "AstrBot-ArkCalendar/0.2"},
        )
        self.http = HttpClient(self.session)
        self.assets = AssetCache(self.data_dir / "assets", self.session)
        self.anything = AnythingIcsSource(
            self.http,
            self.config.get("anything_ics_base_url", "https://proxy.avgt.ink/ics"),
        )
        self.prts = PrtsSource(
            self.http,
            self.config.get("prts_base_url", "https://prts.wiki"),
        )
        self.gacha = GachaSource(
            self.http,
            self.config.get(
                "gacha_data_url",
                "https://raw.githubusercontent.com/s-yh-china/ArknightsGachaData/master/data/pool_info.json",
            ),
        )
        cached = self.cache.load("snapshot.json")
        if isinstance(cached, dict):
            try:
                self.last_snapshot = CalendarSnapshot.from_dict(cached)
            except Exception:
                self.logger.warning("无法读取日历快照缓存。", exc_info=True)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def snapshot(self, force: bool = False) -> CalendarSnapshot:
        ttl = timedelta(minutes=max(1, int(self.config.get("cache_ttl_minutes", 30))))
        if not force and self._snapshot_is_fresh(ttl):
            return self.last_snapshot  # type: ignore[return-value]
        async with self.refresh_lock:
            if not force and self._snapshot_is_fresh(ttl):
                return self.last_snapshot  # type: ignore[return-value]
            try:
                result = await self._build_snapshot()
                self.last_snapshot = result
                self.cache.save("snapshot.json", result.to_dict())
                return result
            except Exception:
                self.logger.error("方舟日历刷新失败。", exc_info=True)
                if self.last_snapshot:
                    return self.last_snapshot
                raise

    def _snapshot_is_fresh(self, ttl: timedelta) -> bool:
        if not self.last_snapshot:
            return False
        try:
            generated = parse_iso(self.last_snapshot.generated_at).astimezone(CN_TZ)
        except (TypeError, ValueError):
            return False
        return self._now() - generated < ttl

    async def find_operator(self, query: str) -> tuple[Operator | None, list[str]]:
        await self._ensure_reference_data()
        normalized = self.normalize_name(query)
        by_name = {self.normalize_name(item["name"]): item for item in self._birthdays}
        record = by_name.get(normalized)
        if record:
            return await self._operator_from_record(record), []
        candidates = [
            item["name"]
            for item in self._birthdays
            if normalized and normalized in self.normalize_name(item["name"])
        ]
        if not candidates:
            candidates = difflib.get_close_matches(
                query,
                [item["name"] for item in self._birthdays],
                n=5,
                cutoff=0.45,
            )
        if len(candidates) == 1:
            record = next(item for item in self._birthdays if item["name"] == candidates[0])
            return await self._operator_from_record(record), []
        return None, candidates[:8]

    async def _ensure_reference_data(self) -> None:
        assert self.anything and self.prts
        if not self._birthdays:
            data, _ = await self._fetch_cached(
                "birthdays.json",
                "anything-ics / 生日",
                self.anything.birthdays(),
                [],
                lambda value: isinstance(value, list) and len(value) > 100,
            )
            self._birthdays = data
        if not self._operator_index:
            data, _ = await self._fetch_cached(
                "operators.json",
                "PRTS / 干员一览",
                self.prts.operator_index(),
                {},
                lambda value: isinstance(value, dict) and len(value) > 100,
            )
            self._operator_index = data

    async def _build_snapshot(self) -> CalendarSnapshot:
        assert self.anything and self.prts and self.gacha and self.assets
        now = self._now()
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(7, int(self.config.get("timeline_days", 28))))

        source_results = await asyncio.gather(
            self._fetch_cached(
                "birthdays.json", "anything-ics / 生日", self.anything.birthdays(), [],
                lambda value: isinstance(value, list) and len(value) > 100,
            ),
            self._fetch_cached(
                "events.json", "anything-ics / 活动", self.anything.events(), [],
                lambda value: isinstance(value, list) and bool(value),
            ),
            self._fetch_cached(
                "prts_home.json", "PRTS / 首页", self.prts.home(), {},
                lambda value: isinstance(value, dict) and bool(value.get("supplies")),
            ),
            self._fetch_cached(
                "operators.json", "PRTS / 干员一览", self.prts.operator_index(), {},
                lambda value: isinstance(value, dict) and len(value) > 100,
            ),
            self._fetch_cached(
                "gacha_overview.json", "PRTS / 卡池一览", self.prts.gacha_overview(), [],
                lambda value: isinstance(value, list) and bool(value),
            ),
        )
        birthdays, events_raw, home, operator_index, overview = [item[0] for item in source_results]
        source_states = [item[1] for item in source_results]
        self._birthdays = birthdays
        self._operator_index = operator_index

        today_records = [
            item for item in birthdays
            if (item.get("birthday") or {}).get("month") == now.month
            and (item.get("birthday") or {}).get("day") == now.day
        ]
        upcoming_groups: list[BirthdayGroup] = []
        required_names = [item["name"] for item in today_records]
        for offset in range(1, 8):
            day = now + timedelta(days=offset)
            records = [
                item for item in birthdays
                if (item.get("birthday") or {}).get("month") == day.month
                and (item.get("birthday") or {}).get("day") == day.day
            ]
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
        if self.config.get("include_recent_operators", True):
            for item in home.get("recent", [])[:6]:
                name = item.get("name", "")
                if not name:
                    continue
                info = operator_index.get(name, {})
                avatar = await self.assets.data_uri(item.get("avatar") or avatar_urls.get(name, ""))
                recent_operators.append(Operator(
                    name,
                    profession=info.get("profession", ""),
                    rarity=info.get("rarity"),
                    avatar=avatar,
                ))

        event_items, long_items = await self._build_events(events_raw, start, end)
        pools_raw, gacha_states = await self._load_gacha_pools(start, end, overview)
        source_states.extend(gacha_states)
        gacha_items = await self._build_gacha_items(pools_raw)

        today_info = TodayInfo(
            home.get("supplies", []),
            home.get("chips", []),
            home.get("alerts", []),
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
            long_term_events=long_items if self.config.get("include_long_term", True) else [],
            source_states=source_states,
        )

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
            *(self.prts.event_detail(raw["name"]) for raw, _, _ in selected),
            return_exceptions=True,
        )
        event_items: list[TimelineItem] = []
        long_items: list[TimelineItem] = []
        for (raw, item_start, item_end), detail_result in zip(selected, details):
            detail = detail_result if isinstance(detail_result, dict) else {}
            image = await self.assets.data_uri(detail.get("image_url", ""))
            if not image:
                image = await self.assets.data_uri(str(self._event_fallback(raw["name"])))
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

    async def _load_gacha_pools(
        self,
        start: datetime,
        end: datetime,
        overview: list[dict],
    ) -> tuple[list[dict], list[SourceState]]:
        assert self.gacha
        now_text = self._now().isoformat()
        try:
            pools = await self.gacha.pools(start, end, overview)
            self.cache.save("gacha_pools.json", [self._serialize_pool(item) for item in pools])
            states = [
                SourceState(
                    name=item["name"],
                    ok=item["ok"],
                    updated_at=now_text,
                    message=item.get("message", ""),
                )
                for item in self.gacha.last_source_states
            ]
            return pools, states
        except Exception as exc:
            states = [
                SourceState(
                    name=item["name"],
                    ok=item["ok"],
                    updated_at=now_text,
                    message=item.get("message", ""),
                )
                for item in self.gacha.last_source_states
            ]
            if not states:
                states.append(SourceState("ArknightsGachaData", False, now_text, self._short_error(exc)))
            cached = self.cache.load("gacha_pools.json")
            if isinstance(cached, list):
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
                    for state in states:
                        if not state.ok:
                            state.message = f"实时更新失败，已使用缓存：{state.message or self._short_error(exc)}"
                    return pools, states
            raise

    async def _build_gacha_items(self, pools_raw: list[dict]) -> list[TimelineItem]:
        assert self.prts and self.gacha and self.assets
        fallback_map = {
            "LIMITED": "gacha-limited.jpg",
            "LINKAGE": "gacha-limited.jpg",
            "SINGLE": "gacha-rerun.jpg",
            "DOUBLE": "gacha-standard.png",
            "CLASSIC_DOUBLE": "gacha-kernel.jpg",
            "CLASSIC": "gacha-kernel.jpg",
        }
        previous = {item.id: item for item in self.last_snapshot.gacha_pools} if self.last_snapshot else {}
        result: list[TimelineItem] = []
        for pool in pools_raw:
            cached = previous.get(pool.get("id", ""))
            six = list(pool.get("six", [])) or (list(cached.six_star_up) if cached else [])
            weighted = list(pool.get("weighted", [])) or (list(cached.weighted_up) if cached else [])
            image = await self.assets.data_uri(pool.get("image", ""))
            images: list[str] = []
            if not image and six:
                urls = await self._safe_avatar_urls(six[:2])
                images = [await self.assets.data_uri(urls.get(name, "")) for name in six[:2]]
                images = [item for item in images if item]
            if not image:
                local = self.plugin_dir / "assets" / fallback_map.get(pool.get("type", ""), "gacha-kernel.jpg")
                image = await self.assets.data_uri(str(local))
            result.append(TimelineItem(
                id=pool.get("id", ""),
                name=pool.get("name", ""),
                category="gacha",
                item_type=self.gacha.label(pool.get("type", "")),
                start=pool["start"].isoformat(),
                end=pool["end"].isoformat(),
                image=image,
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
        avatar = await self.assets.data_uri(urls.get(name, ""))
        if not avatar:
            avatar = await self.assets.data_uri(self._local_avatar(name))
        return Operator(
            name=name,
            birthday_month=birthday.get("month"),
            birthday_day=birthday.get("day"),
            profession=info.get("profession", ""),
            rarity=info.get("rarity"),
            avatar=avatar,
        )

    def _local_avatar(self, name: str) -> str:
        files = {
            "卡缇": "operator-cardigan.png",
            "安洁莉娜": "operator-angelina.png",
            "森蚺": "operator-eunectes.png",
            "远牙": "operator-fartooth.png",
            "嘉辛塔": "operator-jacinta.png",
            "时隙": "operator-timeslot.png",
        }
        file_name = files.get(name)
        return str(self.plugin_dir / "assets" / file_name) if file_name else ""

    async def _safe_avatar_urls(self, names: list[str]) -> dict[str, str]:
        assert self.prts
        try:
            return await self.prts.resolve_avatar_urls(names)
        except Exception:
            self.logger.warning("获取干员头像地址失败。", exc_info=True)
            return {}

    async def _fetch_cached(
        self,
        cache_name: str,
        label: str,
        request: Awaitable[Any],
        default: Any,
        validator: Callable[[Any], bool] | None = None,
    ) -> tuple[Any, SourceState]:
        now_text = self._now().isoformat()
        try:
            data = await request
            if validator and not validator(data):
                raise ValueError(f"{label} 返回的数据为空或格式异常")
            self.cache.save(cache_name, data)
            return data, SourceState(label, True, now_text, "")
        except Exception as exc:
            cached = self.cache.load(cache_name)
            message = self._short_error(exc)
            if cached is not None and (validator is None or validator(cached)):
                return cached, SourceState(label, False, now_text, f"实时更新失败，已使用缓存：{message}")
            return default, SourceState(label, False, now_text, f"当前不可用且无缓存：{message}")

    def _event_fallback(self, name: str) -> Path:
        assets = self.plugin_dir / "assets"
        mappings = {
            "黑流树海": "event-blackforest.png",
            "重启锚点": "event-relaunch.jpg",
            "信使": "event-letter.jpg",
            "展览": "event-exhibition.jpg",
        }
        for keyword, file_name in mappings.items():
            if keyword in name:
                return assets / file_name
        choices = ["event-orange.jpg", "event-relaunch.jpg", "event-letter.jpg", "event-exhibition.jpg"]
        index = hashlib.sha256(name.encode("utf-8")).digest()[0] % len(choices)
        return assets / choices[index]

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
