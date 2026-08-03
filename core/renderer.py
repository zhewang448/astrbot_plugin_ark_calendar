from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import CalendarSnapshot, Operator, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarRenderer:
    COLORS = {"event": "#087c92", "登录活动": "#f5c335", "限定寻访": "#c83e43", "标准寻访": "#7555a0", "中坚寻访": "#3c6680", "单人寻访": "#8a5c49", "联动寻访": "#c83e43"}

    def __init__(self, plugin, service):
        self.plugin = plugin
        self.service = service
        self.template = (Path(__file__).parent.parent / "templates" / "calendar.html").read_text("utf-8")
        self.birthday_template = (Path(__file__).parent.parent / "templates" / "birthday.html").read_text("utf-8")

    async def calendar(self, snapshot: CalendarSnapshot) -> str:
        start, end = parse_iso(snapshot.timeline_start), parse_iso(snapshot.timeline_end)
        now = parse_iso(snapshot.generated_at)
        items = [self._timeline(x, start, end, now) for x in snapshot.events]
        pools = [self._timeline(x, start, end, now) for x in snapshot.gacha_pools]
        longs = [self._timeline(x, start, end, now) for x in snapshot.long_term_events]
        static = await self._static_assets()
        hero = next((x["image"] for x in items if x["image"]), static["hero"])
        ticks = []
        for offset in (0, 1, 7, 14, 21, 28):
            day = start + timedelta(days=offset)
            ticks.append({"left": min(100, offset / max(1, (end-start).days) * 100), "date": day.strftime("%m.%d"), "label": "TODAY" if day.date() == now.date() else day.strftime("%a").upper(), "today": day.date() == now.date()})
        data = {
            "snapshot": snapshot.to_dict(), "events": items, "pools": pools, "longs": longs, "ticks": ticks,
            "today_left": max(0, min(100, (now-start).total_seconds()/(end-start).total_seconds()*100)),
            "date_cn": now.strftime("%Y / %m / %d"), "generated_text": now.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M"),
            "weekday": "星期" + "一二三四五六日"[now.weekday()],
            "hero": hero, "static": static, "show_footer": self.service.config.get("show_source_footer", True),
        }
        return await self.plugin.html_render(self.template, data, options={"type": "png", "full_page": True, "animations": "disabled", "scale": "css", "timeout": 30000})

    async def birthday(self, operator: Operator) -> str:
        now = self.service._now()
        if operator.birthday_month and operator.birthday_day:
            next_date = datetime(now.year, operator.birthday_month, operator.birthday_day, tzinfo=CN_TZ)
            if next_date.date() < now.date():
                next_date = next_date.replace(year=now.year + 1)
            days = (next_date.date() - now.date()).days
            birthday = f"{operator.birthday_month}月{operator.birthday_day}日"
            status = "今天生日" if days == 0 else f"距离下次生日 {days} 天"
        else:
            birthday, status = "未公开", "当前数据源未记录生日"
        static = await self._static_assets()
        return await self.plugin.html_render(
            self.birthday_template,
            {"operator": operator, "birthday": birthday, "status": status, "font": static["font"]},
            options={"type": "png", "full_page": True, "animations": "disabled", "scale": "css"},
        )

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
        names = {"font":"SourceHanSerifCN-Medium-6.otf", "hero":"event-orange.jpg", "exp":"item-exp.png", "voucher":"item-voucher.png", "lmd":"item-lmd.png", "skill":"item-skill.png", "medic":"chip-medic.png", "defender":"chip-defender.png"}
        return {key: await self.service.assets.data_uri(str(base/name)) for key,name in names.items()}
