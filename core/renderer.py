from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .font_subset import FontSubsetError, FontSubsetter, collect_charset
from .models import CalendarSnapshot, parse_iso
from .render_cache import validate_rendered_image

CN_TZ = ZoneInfo("Asia/Shanghai")

# 帮助页头图固定使用打包内的这张图，不随当期活动变化；文件缺失时模板回退到纯 CSS 背景。
HELP_HERO_ASSET = "help-hero.jpg"

# 源字体；实际内嵌的是按当次渲染用到的字形裁出来的 woff2 子集，不是这个完整文件。
SOURCE_FONT_ASSET = "SourceHanSerifCN-Medium-6.otf"


class CalendarRenderer:
    COLORS = {"event": "#087c92", "登录活动": "#f5c335", "限定寻访": "#c83e43", "标准寻访": "#7555a0", "中坚寻访": "#3c6680", "单人寻访": "#8a5c49", "联动寻访": "#c83e43"}

    def __init__(self, plugin, service):
        self.plugin = plugin
        self.service = service
        templates = Path(__file__).parent.parent / "templates"
        self.template = (templates / "calendar.html").read_text("utf-8")
        self.help_template = (templates / "help.html").read_text("utf-8")
        self.bilibili_template = (templates / "bilibili_dynamic.html").read_text("utf-8")
        self.recruitment_template = (templates / "recruitment.html").read_text("utf-8")
        self.template_hash = hashlib.sha256(self.template.encode("utf-8")).hexdigest()[:16]
        self.source_font = Path(__file__).parent.parent / "assets" / SOURCE_FONT_ASSET
        self.font_subsetter = FontSubsetter(
            self.source_font,
            service.data_dir / "render" / "fonts",
            logger=getattr(service, "logger", None),
        )

    async def calendar(self, snapshot: CalendarSnapshot, *, historical: bool = False) -> str:
        start, end = parse_iso(snapshot.timeline_start), parse_iso(snapshot.timeline_end)
        now = parse_iso(snapshot.generated_at)
        items = [self._timeline(x, start, end, now) for x in snapshot.events]
        pools = [self._timeline(x, start, end, now) for x in self._visible_gacha_pools(snapshot.gacha_pools)]
        pool_details = self._pool_details(pools)
        longs = [self._timeline(x, start, end, now) for x in snapshot.long_term_events]
        hero = next(
            (x["image"] for x in [*items, *pools, *longs] if x["image"]),
            "",
        )
        timeline_days = max(1, (end - start).days)
        ticks = self._ticks(start, now, timeline_days)
        data = {
            # 只传模板真正读的字段，不再整份 snapshot.to_dict()。
            # to_dict() 会把 events/gacha_pools/long_term_events 里的图片 base64 再带一份，
            # 而模板读的是下面 _timeline() 加工过的 events/pools/longs，那三个字段从未被使用。
            "today_info": asdict(snapshot.today_info),
            "today_birthdays": [asdict(item) for item in snapshot.today_birthdays],
            "upcoming_birthdays": [
                {
                    "month": group.month,
                    "day": group.day,
                    "operators": [asdict(item) for item in group.operators],
                }
                for group in snapshot.upcoming_birthdays
            ],
            "recent_operators": [asdict(item) for item in snapshot.recent_operators],
            "events": items, "pools": pools, "pool_details": pool_details, "longs": longs, "ticks": ticks,
            "timeline_days": timeline_days,
            "today_left": max(0, min(100, (now-start).total_seconds()/(end-start).total_seconds()*100)),
            "date_cn": now.strftime("%Y / %m / %d"), "data_date_text": snapshot.calendar_date,
            "weekday": "星期" + "一二三四五六日"[now.weekday()],
            "hero": hero,
            "show_footer": self.service.value("basic", "show_source_footer", True, "show_source_footer"),
            "pool_detail_cards": bool(self.service.value("basic", "pool_detail_cards", True, "pool_detail_cards")),
            "historical": historical,
        }
        # 字体子集要按最终数据里出现的字形来裁，所以放在 data 组装之后。
        data["static"] = await self._static_assets(collect_charset(self.template, data))
        return await self._html_render(self.template, data, options=self._render_options())

    async def historical_calendar(self, snapshot: CalendarSnapshot) -> str:
        """保留旧调用入口，但历史测试改用正常日报模板和布局。"""
        return await self.calendar(snapshot, historical=True)

    def _visible_gacha_pools(self, pools):
        if self.service.show_unpublished_pools():
            return list(pools)
        return [pool for pool in pools if not pool.item_type.startswith("未公布")]

    async def bilibili_dynamic(
        self,
        dynamic: dict,
        *,
        include_images: bool,
    ) -> str | Path | bytes:
        """将单条 B 站动态渲染为终端风格图片。"""
        images: list[str] = []
        if include_images:
            assert self.service.assets
            for image in dynamic.get("cached_images", []):
                encoder = getattr(self.service.assets, "data_uri_local", None)
                if encoder is None:
                    encoder = self.service.assets.data_uri
                    uri = await encoder(str(image), box=(1180, 760), quality=86)
                else:
                    uri = await encoder(str(image), box=(1180, 760), quality=86)
                if uri:
                    images.append(uri)
        data = {
            "mode": "detail",
            "dynamic": {
                "title": str(dynamic.get("title", "") or "未命名动态"),
                "description": str(dynamic.get("description_text", "") or "该动态未提供文字内容。"),
                "type": str(dynamic.get("dynamic_type", "text") or "text").upper(),
                "published": self._dynamic_time(dynamic.get("pub_date")),
                "image_count": len(dynamic.get("images") or dynamic.get("cached_images") or []),
            },
            "images": images,
            "static": await self._static_assets(collect_charset(self.bilibili_template, dynamic)),
        }
        return await self._html_render(self.bilibili_template, data, options=self._card_render_options())

    async def bilibili_dynamic_list(self, dynamics: list[dict]) -> str | Path | bytes:
        """将动态索引渲染为可供编号查询的终端列表。"""
        entries = []
        for index, dynamic in enumerate(dynamics, 1):
            entries.append({
                "index": index,
                "title": str(dynamic.get("title", "") or "未命名动态"),
                "type": str(dynamic.get("dynamic_type", "text") or "text").upper(),
                "published": self._dynamic_time(dynamic.get("pub_date")),
                "image_count": len(dynamic.get("images") or dynamic.get("cached_images") or []),
            })
        data = {
            "mode": "list",
            "entries": entries,
            "static": await self._static_assets(collect_charset(self.bilibili_template, {"entries": entries})),
        }
        return await self._html_render(self.bilibili_template, data, options=self._card_render_options())

    async def recruitment_result(
        self,
        results: list[dict],
        selected_tags: list[str],
    ) -> str | Path | bytes:
        """将公开招募计算结果渲染为筛选终端长图。"""
        avatar_urls: dict[str, str] = {}
        prts = getattr(self.service, "prts", None)
        assets = getattr(self.service, "assets", None)
        names = list(dict.fromkeys(
            str(operator.get("name", ""))
            for result in results
            for operator in result.get("operators", [])
            if operator.get("name")
        ))
        if names and prts is not None and hasattr(prts, "resolve_avatar_urls"):
            try:
                avatar_urls = await prts.resolve_avatar_urls(names)
            except Exception:
                logger = getattr(self.service, "logger", None)
                if logger:
                    logger.warning("公招干员头像地址获取失败，继续渲染文字结果。", exc_info=True)

        async def hydrate_operator(operator: dict) -> dict:
            current = dict(operator)
            source = avatar_urls.get(str(operator.get("name", "")), "")
            if source and assets is not None and hasattr(assets, "data_uri"):
                try:
                    current["avatar"] = await assets.data_uri(
                        source,
                        box=(64, 64),
                        quality=82,
                        force_webp=True,
                    )
                except Exception:
                    current["avatar"] = ""
            else:
                current["avatar"] = ""
            return current

        rows = [
            {
                "tags": result.get("tags", []),
                "tag_combinations": [
                    " + ".join(tags)
                    for tags in (result.get("tag_combinations") or [result.get("tags", [])])
                ],
                "operators": result.get("operators", []),
                "min_rarity": int(result.get("min_rarity", 0) or 0),
                "recommended": index == 0,
                "senior": bool(result.get("has_senior")),
                "top_senior": bool(result.get("has_top_senior")),
            }
            for index, result in enumerate(results)
        ]
        for row in rows:
            row["operators"] = await asyncio.gather(
                *(hydrate_operator(operator) for operator in row["operators"])
            )
        data = {
            "mode": "result",
            "selected_tags": selected_tags,
            "rows": rows,
            "static": await self._static_assets(collect_charset(self.recruitment_template, {"selected_tags": selected_tags, "rows": rows})),
        }
        return await self._html_render(self.recruitment_template, data, options=self._card_render_options())

    async def recruitment_help(self, tag_groups: dict[str, list[str]]) -> str | Path | bytes:
        """将公开招募可用标签与用法渲染为终端说明图。"""
        data = {
            "mode": "help",
            "tag_groups": tag_groups,
            "static": await self._static_assets(collect_charset(self.recruitment_template, {"tag_groups": tag_groups})),
        }
        return await self._html_render(self.recruitment_template, data, options=self._card_render_options())

    @staticmethod
    def _dynamic_time(value) -> str:
        if isinstance(value, datetime):
            return value.astimezone(CN_TZ).strftime("%Y.%m.%d  %H:%M")
        return "发布时间未知"

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

    def _render_options(self) -> dict:
        image_type = str(self.service.value("cache_and_render", "render_image_type", "png") or "png").lower()
        scale_level = str(self.service.value("cache_and_render", "render_device_scale_factor_level", "high") or "high").lower()
        if image_type not in {"png", "jpeg"}:
            image_type = "png"
        if scale_level not in {"normal", "high", "ultra"}:
            scale_level = "high"
        return {
            "type": image_type,
            "full_page": True,
            "animations": "disabled",
            "scale": "device",
            "device_scale_factor_level": scale_level,
            "timeout": self._render_timeout_ms(),
        }

    def _card_render_options(self) -> dict:
        """交互卡片始终用 PNG，避免小字和细线受 JPEG 压缩影响。"""
        options = self._render_options()
        options["type"] = "png"
        return options
    def _render_timeout_ms(self) -> int:
        try:
            seconds = int(self.service.value("cache_and_render", "render_timeout_seconds", 300))
        except (AttributeError, TypeError, ValueError):
            seconds = 300
        return min(300, max(5, seconds)) * 1000

    async def _html_render(self, template: str, data: dict, options: dict) -> str | Path | bytes:
        rendered = await self.plugin.html_render(template, data, return_url=False, options=options)
        image_type = str(options.get("type", "png") or "png").lower()
        try:
            validate_rendered_image(rendered, image_type)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            timeout_seconds = max(1, int(options.get("timeout", 0)) // 1000)
            raise RuntimeError(
                f"T2I 渲染未返回有效 {image_type.upper()} 图片（超时设置：{timeout_seconds} 秒）：{exc}"
            ) from exc
        return rendered

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
        color = "#858b91" if item.item_type.startswith("未公布") else self.COLORS.get(item.item_type, self.COLORS.get(item.category, "#4d8a72"))
        return {**base, "left": left, "width": width, "start_text": s.astimezone(CN_TZ).strftime("%m.%d %H:%M"), "end_text": e.astimezone(CN_TZ).strftime("%m.%d %H:%M"), "countdown": countdown, "color": color}

    async def _static_assets(self, charset: str):
        """按本次实际用到的字形提供子集字体，避免把 10.85 MB 完整字体塞进请求体。"""
        return {"font": await self._font_data_uri(charset)}

    @staticmethod
    def _pool_details(pools: list[dict]) -> list[dict]:
        """详情区只展示已经有六星 UP 信息的卡池。"""
        return [pool for pool in pools if pool.get("six_star_up")]

    async def _font_data_uri(self, charset: str) -> str:
        """优先返回子集 woff2；子集化不可用时回退到内嵌完整字体，保证不缺字。"""
        try:
            return await self.font_subsetter.data_uri(charset)
        except FontSubsetError as exc:
            self.font_subsetter.log_unavailable_once(exc)
        assert self.service.assets
        encoder = getattr(self.service.assets, "data_uri_local", None)
        if encoder is None:  # 兼容旧版宿主注入的 Assets 替身。
            return await self.service.assets.data_uri(str(self.source_font))
        return await encoder(self.source_font, trusted_roots=(self.source_font.parent,))

    async def _help_hero(self) -> str:
        """帮助页固定头图；打包里没有这张图时返回空串，模板会退到纯 CSS 背景。"""
        assert self.service.assets
        hero = Path(__file__).parent.parent / "assets" / HELP_HERO_ASSET
        if not hero.is_file():
            return ""
        encoder = getattr(self.service.assets, "data_uri_local", None)
        if encoder is None:
            return await self.service.assets.data_uri(str(hero))
        return await encoder(hero, trusted_roots=(hero.parent,))

    async def help_page(
        self,
        snapshot: CalendarSnapshot,
        user_commands: list,
        admin_commands: list,
        mode: str = "help",
    ) -> str:
        """按日历同一套视觉渲染帮助页；mode="subscribe" 时把可订阅日程放到最前。"""
        now = parse_iso(snapshot.generated_at)
        subscribable_items = self.subscribable_items(snapshot)
        hero = await self._help_hero()
        data = {
            "mode": mode,
            "title": "订阅可用日程" if mode == "subscribe" else "罗德岛终端手册",
            "subtitle_en": "SUBSCRIPTION DIRECTORY" if mode == "subscribe" else "COMMAND MANUAL",
            "lead": (
                "下面是当前可以订阅的活动与寻访，复制卡片里的命令就能订阅；"
                "在结束前一天的设定时间提醒你，不填时间默认中午 12:00。"
                if mode == "subscribe"
                else "罗德岛行动终端的全部指令与当前可订阅日程都在这里，"
                "指令支持别名，订阅提醒在结束前一天送达。"
            ),
            "version": self.service.plugin_version,
            "user_commands": user_commands,
            "admin_commands": admin_commands,
            "subscribable_items": subscribable_items,
            "date_cn": now.strftime("%Y / %m / %d"),
            "weekday": "星期" + "一二三四五六日"[now.weekday()],
            "data_date_text": snapshot.calendar_date,
            "hero": hero,
        }
        data["static"] = await self._static_assets(collect_charset(self.help_template, data))
        # 帮助缓存固定为 PNG，避免跟随日历 JPEG 配置而无法写入 HelpImageCache。
        render_options = self._render_options()
        render_options["type"] = "png"
        return await self._html_render(
            self.help_template,
            data,
            options=render_options,
        )

    def subscribable_items(self, snapshot: CalendarSnapshot) -> list[dict]:
        """未结束的活动、卡池与长期活动，按结束时间排序，供帮助页与订阅提示复用。"""
        now = parse_iso(snapshot.generated_at)
        items: list[dict] = []
        for item in [*snapshot.events, *snapshot.gacha_pools, *snapshot.long_term_events]:
            try:
                start_time, end_time = parse_iso(item.start), parse_iso(item.end)
            except (TypeError, ValueError):
                continue
            if end_time <= now:
                continue
            target, prefix = (start_time, "距开启 ") if now < start_time else (end_time, "距结束 ")
            hours = max(0, int((target - now).total_seconds() // 3600))
            if hours <= 0:
                countdown = prefix + "不足 1 小时"
            elif hours >= 24:
                countdown = prefix + f"{hours // 24} 天 {hours % 24} 时"
            else:
                countdown = prefix + f"{hours} 小时"
            items.append({
                "name": item.name,
                "category": item.category,
                "type_label": "活动" if item.category == "event" else (item.item_type or "寻访"),
                "start_text": start_time.astimezone(CN_TZ).strftime("%m.%d %H:%M"),
                "end_text": end_time.astimezone(CN_TZ).strftime("%m.%d %H:%M"),
                "countdown": countdown,
                "end_sort": end_time.timestamp(),
                "color": self.COLORS.get(item.item_type, self.COLORS.get(item.category, "#4d8a72")),
            })
        items.sort(key=lambda entry: entry["end_sort"])
        return items
