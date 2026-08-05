from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import CalendarSnapshot, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarRenderer:
    COLORS = {"event": "#087c92", "登录活动": "#f5c335", "限定寻访": "#c83e43", "标准寻访": "#7555a0", "中坚寻访": "#3c6680", "单人寻访": "#8a5c49", "联动寻访": "#c83e43"}

    def __init__(self, plugin, service):
        self.plugin = plugin
        self.service = service
        templates = Path(__file__).parent.parent / "templates"
        self.template = (templates / "calendar.html").read_text("utf-8")
        self.history_template = (templates / "history_schedule.html").read_text("utf-8")
        self.template_hash = hashlib.sha256(self.template.encode("utf-8")).hexdigest()[:16]

    async def calendar(self, snapshot: CalendarSnapshot) -> str:
        start, end = parse_iso(snapshot.timeline_start), parse_iso(snapshot.timeline_end)
        now = parse_iso(snapshot.generated_at)
        items = [self._timeline(x, start, end, now) for x in snapshot.events]
        pools = [self._timeline(x, start, end, now) for x in snapshot.gacha_pools]
        longs = [self._timeline(x, start, end, now) for x in snapshot.long_term_events]
        static = await self._static_assets()
        hero = next(
            (x["image"] for x in [*items, *pools, *longs] if x["image"]),
            "",
        )
        timeline_days = max(1, (end - start).days)
        ticks = self._ticks(start, now, timeline_days)
        data = {
            "snapshot": snapshot.to_dict(), "events": items, "pools": pools, "longs": longs, "ticks": ticks,
            "timeline_days": timeline_days,
            "today_left": max(0, min(100, (now-start).total_seconds()/(end-start).total_seconds()*100)),
            "date_cn": now.strftime("%Y / %m / %d"), "generated_text": now.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M"),
            "weekday": "星期" + "一二三四五六日"[now.weekday()],
            "hero": hero, "static": static,
            "show_footer": self.service.value("basic", "show_source_footer", True, "show_source_footer"),
        }
        return await self._html_render(self.template, data, options={"type": "png", "full_page": True, "animations": "disabled", "scale": "css", "timeout": self._render_timeout_ms()})

    async def historical_calendar(self, snapshot: CalendarSnapshot) -> str:
        """Render a historical snapshot with the same timeline/image preparation as the main calendar."""
        start, end = parse_iso(snapshot.timeline_start), parse_iso(snapshot.timeline_end)
        now = parse_iso(snapshot.generated_at)
        events = [self._timeline(item, start, end, now) for item in snapshot.events]
        pools = [self._timeline(item, start, end, now) for item in snapshot.gacha_pools]
        total_days = max(1, (end.date() - start.date()).days + 1)
        data = {
            "start_text": start.astimezone(CN_TZ).strftime("%Y-%m-%d"),
            "end_text": end.astimezone(CN_TZ).strftime("%Y-%m-%d"),
            "timeline_days": total_days,
            "ticks": self._range_ticks(start, total_days),
            "events": events,
            "pools": pools,
            "event_count": len(events),
            "pool_count": len(pools),
            "static": await self._static_assets(),
        }
        return await self._html_render(self.history_template, data, options={"type": "png", "full_page": True, "animations": "disabled", "scale": "css", "timeout": self._render_timeout_ms()})

    @staticmethod
    def _ticks(start: datetime, now: datetime, timeline_days: int) -> list[dict]:
        step = 7 if timeline_days <= 35 else 14 if timeline_days <= 63 else 21
        offsets = set(range(0, timeline_days + 1, step))
        offsets.update({0, min(1, timeline_days), timeline_days})
        return [
            {
                "left": offset / timeline_days * 100,
                "date": (start + timedelta(days=offset)).strftime("%m.%d"),
                "label": "TODAY" if (start + timedelta(days=offset)).date() == now.date() else (start + timedelta(days=offset)).strftime("%a").upper(),
                "today": (start + timedelta(days=offset)).date() == now.date(),
            }
            for offset in sorted(offsets)
        ]

    @staticmethod
    def _range_ticks(start: datetime, timeline_days: int) -> list[dict]:
        step = 7 if timeline_days <= 35 else 14 if timeline_days <= 63 else 21
        offsets = set(range(0, timeline_days, step))
        offsets.update({0, timeline_days - 1})
        return [
            {
                "left": offset / max(1, timeline_days - 1) * 100,
                "date": (start + timedelta(days=offset)).strftime("%m.%d"),
                "label": (start + timedelta(days=offset)).strftime("%a").upper(),
            }
            for offset in sorted(offsets)
        ]

    def _render_timeout_ms(self) -> int:
        try:
            seconds = int(self.service.value("cache_and_render", "render_timeout_seconds", 30))
        except (AttributeError, TypeError, ValueError):
            seconds = 30
        return min(300, max(5, seconds)) * 1000

    async def _html_render(self, template: str, data: dict, options: dict) -> str:
        return await self.plugin.html_render(template, data, return_url=False, options=options)

    def _timeline(self, item, start, end, now):
        s, e = parse_iso(item.start), parse_iso(item.end)
        total = (end-start).total_seconds()
        left = max(0, min(100, (s-start).total_seconds()/total*100))
        right = max(0, min(100, (e-start).total_seconds()/total*100))
        if now < s:
            target, prefix = s, "距开启 "
        elif now <= e:
            target, prefix = e, "距结束 "
        else:
            target, prefix = e, "已结束 "
        hours = max(0, int((target - now).total_seconds() // 3600))
        if prefix == "已结束 ":
            countdown = "已结束"
        elif hours <= 0:
            countdown = prefix + "不足1小时"
        else:
            countdown = prefix + (f"{hours // 24}天{hours % 24}时" if hours >= 24 else f"{hours}小时")
        base = {key: getattr(item, key) for key in item.__slots__}
        width = max(1.5, min(100 - left, right - left))
        return {**base, "left": left, "width": width, "start_text": s.astimezone(CN_TZ).strftime("%m.%d %H:%M"), "end_text": e.astimezone(CN_TZ).strftime("%m.%d %H:%M"), "countdown": countdown, "color": self.COLORS.get(item.item_type, self.COLORS.get(item.category, "#4d8a72"))}

    async def _static_assets(self):
        assert self.service.assets
        base = Path(__file__).parent.parent / "assets"
        font = base / "SourceHanSerifCN-Medium-6.otf"
        return {"font": await self.service.assets.data_uri(str(font))}
