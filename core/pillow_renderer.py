from __future__ import annotations

import asyncio
import base64
import io
import math
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps


class PillowCalendarRenderer:
    """使用 Pillow 绘制日历、历史日程与帮助长图。

    页面数据由 ``CalendarRenderer`` 统一准备，因此 Pillow 与 AstrBot HTML
    渲染器始终使用相同的数据来源、活动主视觉与干员头像。
    """

    VERSION = "pillow-v2"
    WIDTH = 1440
    PADDING = 64
    BACKGROUND = "#f4f5f3"
    TEXT = "#161c20"
    MUTED = "#78848a"
    CYAN = "#00a8c6"
    YELLOW = "#f5c335"
    DARK = "#101517"

    def __init__(self, service: Any):
        self.service = service
        self.assets_dir = Path(__file__).parent.parent / "assets"
        self.output_dir = Path(service.data_dir) / "render" / "pillow"
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    async def calendar(self, data: dict[str, Any]) -> Path:
        return await asyncio.to_thread(self._render, "calendar", self._draw_calendar, data)

    async def historical_calendar(self, data: dict[str, Any]) -> Path:
        return await asyncio.to_thread(self._render, "history", self._draw_history, data)

    async def help_page(self, data: dict[str, Any]) -> Path:
        return await asyncio.to_thread(self._render, f"help-{data.get('mode', 'full')}", self._draw_help, data)

    def _render(
        self,
        page_name: str,
        painter: Callable[[dict[str, Any]], Image.Image],
        data: dict[str, Any],
    ) -> Path:
        image = painter(data)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{page_name}-{uuid4().hex}.png"
        image.convert("RGB").save(target, format="PNG", optimize=True)
        return target

    def _draw_calendar(self, data: dict[str, Any]) -> Image.Image:
        snapshot = data["snapshot"]
        image = self._page(self._calendar_height(snapshot))
        draw = ImageDraw.Draw(image)
        y = self._calendar_hero(image, draw, data)
        y += 16

        y = self._calendar_section_title(draw, y, "今日作战信息", "TODAY  /  OPERATIONAL STATUS")
        y = self._draw_reference_today(image, draw, snapshot.get("today_info", {}), y)
        y += 38

        y = self._calendar_section_title(draw, y, "今日生日干员", "BIRTHDAY RECORD  /  NEXT 9 DAYS")
        y = self._draw_reference_birthday(image, draw, snapshot, y)
        y += 38

        recent = snapshot.get("recent_operators", [])
        if recent:
            y = self._calendar_section_title(draw, y, "近期新增干员", "NEW OPERATOR ARCHIVE")
            y = self._draw_reference_operators(image, draw, recent, y)
            y += 38

        highlights = snapshot.get("today_info", {})
        if any(highlights.get(key) for key in ("voucher_exchange", "new_skins", "new_modules")):
            y = self._calendar_section_title(draw, y, "首页亮点", "PRTS HOME  /  NEW CONTENT")
            y = self._draw_highlights(image, draw, highlights, y)
            y += 38

        y = self._draw_reference_timeline(
            image, draw, y, "活动日程", "EVENT TIMELINE", data.get("events", []),
            data.get("ticks", []), data.get("today_left", 0), data.get("timeline_days", 1),
        )
        longs = data.get("longs", [])
        if longs:
            y += 16
            y = self._draw_reference_longs(image, draw, y, longs)
        y += 38
        y = self._draw_reference_timeline(
            image, draw, y, "寻访日程", "HEADHUNTING TIMELINE  /  6★ UP", data.get("pools", []),
            data.get("ticks", []), data.get("today_left", 0), data.get("timeline_days", 1), show_type=True,
        )
        self._footer(draw, image.height, data.get("show_footer", True), snapshot.get("calendar_date", ""))
        return image

    def _calendar_hero(self, image: Image.Image, draw: ImageDraw.ImageDraw, data: dict[str, Any]) -> int:
        """复刻主日历 HTML 头图的比例、文案层级与日期块。"""
        height = 292
        draw.rectangle((0, 0, self.WIDTH, height), fill="#070b0d")
        hero = self._decode_image(str(data.get("hero", "")))
        if hero:
            image.alpha_composite(self._cover(hero, (self.WIDTH, height)), (0, 0))
            shade = Image.new("RGBA", (self.WIDTH, height), "#05090b00")
            shade_draw = ImageDraw.Draw(shade)
            for x in range(self.WIDTH):
                alpha = int(245 - 190 * min(1, x / self.WIDTH))
                shade_draw.line((x, 0, x, height), fill=(5, 9, 11, alpha))
            image.alpha_composite(shade, (0, 0))
        draw.text((58, 42), "P.R.T.S.  //  RHODES ISLAND", font=self._font(13, True), fill="#75d1df")
        draw.text((58, 79), "罗德岛行动日历", font=self._font(59, True), fill="white")
        draw.text((58, 153), "D A I L Y   O P E R A T I O N S   C A L E N D A R", font=self._font(14, True), fill="#c8d2d5")
        date_x = 985
        draw.rectangle((date_x, 151, date_x + 5, 220), fill=self.YELLOW)
        date_value = str(data.get("date_cn", "")).replace(" ", "")
        draw.text((date_x + 28, 151), date_value, font=self._font(39, True), fill="white")
        weekday = str(data.get("weekday", "")) + " · 北京时间"
        weekday_width = self._measure(weekday, self._font(13, True))
        draw.text((self.WIDTH - 58 - weekday_width, 205), weekday, font=self._font(13, True), fill="white")
        return height

    def _calendar_section_title(self, draw: ImageDraw.ImageDraw, y: int, title: str, code: str) -> int:
        x, right = self.PADDING, self.WIDTH - self.PADDING
        draw.polygon(((x, y + 5), (x + 8, y), (x + 13, y), (x + 5, y + 28), (x - 3, y + 28)), fill=self.CYAN)
        draw.text((x + 20, y - 5), title, font=self._font(26, True), fill=self.TEXT)
        code_width = self._measure(code, self._font(10, True))
        draw.text((right - code_width, y + 11), code, font=self._font(10, True), fill=self.MUTED)
        draw.line((x, y + 39, right, y + 39), fill="#b6bec0", width=1)
        return y + 55

    def _draw_reference_today(self, image: Image.Image, draw: ImageDraw.ImageDraw, today: dict[str, Any], y: int) -> int:
        left_x, left_w, gap = self.PADDING, 680, 16
        right_x, right = left_x + left_w + gap, self.WIDTH - self.PADDING
        height = 378
        self._reference_panel(draw, (left_x, y, left_x + left_w, y + height), self.CYAN)
        self._reference_panel(draw, (right_x, y, right, y + height), self.YELLOW)
        draw.text((left_x + 20, y + 20), "资源收集开放", font=self._font(17, True), fill=self.TEXT)
        draw.text((left_x + left_w - 100, y + 24), "亮色 = 今日开放", font=self._font(10), fill=self.MUTED)
        draw.line((left_x, y + 55, left_x + left_w, y + 55), fill="#d0d6d7")
        draw.text((left_x + 20, y + 76), "副本开放状态依据 PRTS 首页的星期标签与页面亮度判断；活动期间全开放时以页面亮度为准。", font=self._font(10), fill=self.MUTED)
        resources = list(today.get("resource_schedule", []))
        chips = list(today.get("chip_schedule", []))
        self._draw_reference_stage_cards(image, draw, resources, left_x + 20, y + 103, max_cards=5)
        chip_y = y + 215
        draw.line((left_x, chip_y, left_x + left_w, chip_y), fill="#d0d6d7")
        draw.text((left_x + 20, chip_y + 17), "芯片搜索", font=self._font(12), fill=self.MUTED)
        self._draw_reference_stage_cards(image, draw, chips, left_x + 20, chip_y + 49, max_cards=5)

        draw.text((right_x + 20, y + 20), "临时事项", font=self._font(17, True), fill=self.TEXT)
        draw.line((right_x, y + 55, right, y + 55), fill="#d0d6d7")
        alerts = list(today.get("alerts", []))
        if alerts:
            cursor = y + 85
            for alert in alerts[:5]:
                draw.rectangle((right_x + 20, cursor, right_x + 104, cursor + 8), fill="#29383d")
                title = str(alert.get("title") or alert.get("name") or "临时事项")
                detail = str(alert.get("detail") or alert.get("text") or "")
                self._single_line(draw, title, (right_x + 20, cursor + 19), right - right_x - 40, self._font(13, True), self.TEXT)
                self._single_line(draw, detail, (right_x + 20, cursor + 42), right - right_x - 40, self._font(10), self.MUTED)
                cursor += 70
        else:
            draw.rectangle((right_x + 20, y + 84, right_x + 104, y + 92), fill="#29383d")
        return y + height

    def _draw_reference_stage_cards(self, image: Image.Image, draw: ImageDraw.ImageDraw, items: list[dict[str, Any]], x: int, y: int, *, max_cards: int) -> None:
        for index, item in enumerate(items[:max_cards]):
            card_x = x + index * 136
            open_today = bool(item.get("open"))
            fill = "#4f7f9c" if open_today else "#6c6e70"
            draw.rectangle((card_x, y, card_x + 120, y + 102), fill=fill)
            picture = self._decode_image(str(item.get("image", "")))
            if picture:
                image.alpha_composite(self._cover(picture, (120, 102)), (card_x, y))
                image.alpha_composite(Image.new("RGBA", (120, 102), "#102b3f88" if open_today else "#20242688"), (card_x, y))
            badge = "OPEN" if open_today else "CLOSED"
            badge_fill = self.YELLOW if open_today else "#4e5354"
            badge_text = self.TEXT if open_today else "#e5e8e8"
            draw.rectangle((card_x + 84, y + 5, card_x + 116, y + 19), fill=badge_fill)
            draw.text((card_x + 88, y + 7), badge, font=self._font(7, True), fill=badge_text)
            self._single_line(draw, str(item.get("name", "未知作战")), (card_x + 9, y + 75), 102, self._font(12, True), "white")

    def _draw_reference_birthday(self, image: Image.Image, draw: ImageDraw.ImageDraw, snapshot: dict[str, Any], y: int) -> int:
        people = list(snapshot.get("today_birthdays", []))
        height, x, right = max(196, 58 + math.ceil(len(people) / 2) * 100), self.PADDING, self.WIDTH - self.PADDING
        split = x + 550
        draw.rectangle((x, y, split, y + height), fill="#203941")
        draw.rectangle((split, y, right, y + height), fill="white")
        draw.text((x + 28, y + 22), "TODAY  /  BIRTHDAY RECORD", font=self._font(10, True), fill="#75d1df")
        draw.text((split + 20, y + 22), "NEXT 9 DAYS", font=self._font(12, True), fill=self.CYAN)
        if people:
            for index, person in enumerate(people[:2]):
                person_x = x + 28 + index * 205
                avatar = self._decode_image(str(person.get("avatar", "")))
                if avatar:
                    image.alpha_composite(self._cover(avatar, (88, 88)), (person_x, y + 58))
                else:
                    draw.rectangle((person_x, y + 58, person_x + 88, y + 146), fill="#4f7f9c")
                draw.rectangle((person_x, y + 58, person_x + 88, y + 146), outline=self.YELLOW, width=3)
                self._single_line(draw, str(person.get("name", "未知干员")), (person_x + 106, y + 77), 110, self._font(22, True), "#fff1b8")
                self._single_line(draw, "祝干员生日快乐 🎉", (person_x + 106, y + 112), 130, self._font(10), "#ccd7da")
        else:
            draw.text((x + 28, y + 90), "今日无生日记录", font=self._font(26, True), fill="#fff1b8")
        upcoming = list(snapshot.get("upcoming_birthdays", []))
        columns = 3
        cell_w = (right - split) // columns
        if upcoming:
            for index, group in enumerate(upcoming[:6]):
                col, row = index % columns, index // columns
                cell_x = split + col * cell_w
                cell_y = y + 54 + row * 88
                if index and col == 0:
                    draw.line((cell_x, y + 45, cell_x, y + height), fill="#ccd1d3")
                if row:
                    draw.line((cell_x, cell_y - 12, cell_x + cell_w, cell_y - 12), fill="#ccd1d3")
                date_text = f"{int(group.get('month', 0)):02d}.{int(group.get('day', 0)):02d}"
                draw.text((cell_x + 20, cell_y), date_text, font=self._font(11, True), fill="#087c92")
                for name_index, operator in enumerate(group.get("operators", [])[:3]):
                    self._single_line(draw, str(operator.get("name", "")), (cell_x + 20, cell_y + 27 + name_index * 24), cell_w - 35, self._font(13, True), self.TEXT)
        else:
            draw.text((split + 20, y + 68), "暂无生日记录", font=self._font(15, True), fill=self.TEXT)
        return y + height

    def _draw_reference_operators(self, image: Image.Image, draw: ImageDraw.ImageDraw, people: list[dict[str, Any]], y: int) -> int:
        gap, card_h = 12, 94
        card_w = (self.WIDTH - self.PADDING * 2 - gap * 3) // 4
        for index, person in enumerate(people[:4]):
            x = self.PADDING + index * (card_w + gap)
            draw.rectangle((x, y, x + card_w, y + card_h), fill="#20282c")
            avatar = self._decode_image(str(person.get("avatar", "")))
            if avatar:
                image.alpha_composite(self._cover(avatar, (73, 73)), (x + 10, y + 10))
            else:
                draw.rectangle((x + 10, y + 10, x + 83, y + 83), fill="#3b5967")
            self._single_line(draw, str(person.get("name", "未知干员")), (x + 96, y + 25), card_w - 108, self._font(16, True), "white")
            profession = str(person.get("profession", "")) or "新"
            self._single_line(draw, profession + "干员", (x + 96, y + 53), card_w - 108, self._font(11), "#ced7da")
        return y + card_h

    def _draw_reference_timeline(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        y: int,
        title: str,
        code: str,
        items: list[dict[str, Any]],
        ticks: list[dict[str, Any]],
        today_left: float,
        timeline_days: int,
        *,
        show_type: bool = False,
    ) -> int:
        y = self._calendar_section_title(draw, y, title, code)
        x, right, left_w, head_h, row_h = self.PADDING, self.WIDTH - self.PADDING, 290, 70, 118
        chart_x, chart_w = x + left_w, right - (x + left_w)
        total_rows = max(1, len(items))
        draw.rectangle((x, y, right, y + head_h), fill="#151d20")
        draw.text((x + 18, y + 16), "日程信息", font=self._font(13, True), fill="white")
        for day in range(max(1, int(timeline_days)) + 1):
            grid_x = chart_x + chart_w * day / max(1, timeline_days)
            draw.line((grid_x, y + head_h, grid_x, y + head_h + total_rows * row_h), fill="#dfe4e5", width=1)
        for tick in ticks:
            tick_x = chart_x + chart_w * float(tick.get("left", 0)) / 100
            draw.line((tick_x, y, tick_x, y + head_h), fill="#ffffff30", width=1)
            draw.text((tick_x + 8, y + 12), str(tick.get("date", "")), font=self._font(10, True), fill="#ffffff")
            tick_fill = "#ffe484" if tick.get("today") else "#d6e0e3"
            draw.text((tick_x + 8, y + 36), str(tick.get("label", "")), font=self._font(10, True), fill=tick_fill)
        if not items:
            draw.rectangle((x, y + head_h, right, y + head_h + row_h), fill="white")
            draw.text((x + 18, y + head_h + 42), "当前区间没有可展示的日程。", font=self._font(14), fill=self.MUTED)
            return y + head_h + row_h
        today_x = chart_x + chart_w * max(0, min(100, float(today_left))) / 100
        for index, item in enumerate(items):
            row_y = y + head_h + index * row_h
            draw.rectangle((x, row_y, right, row_y + row_h), fill="white")
            draw.line((x, row_y + row_h, right, row_y + row_h), fill="#d7dcde", width=1)
            color = str(item.get("color", "#4d8a72"))
            draw.rectangle((x, row_y, x + 6, row_y + row_h), fill=color)
            if show_type:
                label = str(item.get("item_type", "寻访"))
                label_w = max(55, self._measure(label, self._font(9, True)) + 14)
                draw.rectangle((x + 18, row_y + 15, x + 18 + label_w, row_y + 37), fill=color)
                draw.text((x + 25, row_y + 20), label, font=self._font(9, True), fill="white")
                name_y = row_y + 45
            else:
                name_y = row_y + 17
            self._single_line(draw, str(item.get("name", "未命名日程")), (x + 18, name_y), left_w - 35, self._font(16, True), self.TEXT)
            self._single_line(draw, f"{item.get('start_text', '')} — {item.get('end_text', '')}", (x + 18, name_y + 27), left_w - 35, self._font(9), self.MUTED)
            detail = str(item.get("item_type", "活动"))
            if show_type and item.get("six_star_up"):
                detail = "6★ UP　" + " / ".join(str(name) for name in item.get("six_star_up", [])[:3])
            self._single_line(draw, detail, (x + 18, name_y + 52), left_w - 35, self._font(10), self.TEXT)
            bar_x = chart_x + chart_w * max(0, min(100, float(item.get("left", 0)))) / 100
            bar_w = max(18, chart_w * max(1.5, min(100, float(item.get("width", 1.5))) / 100))
            bar_w = min(bar_w, right - bar_x - 8)
            bar_y = row_y + 17
            picture = self._decode_image(str(item.get("image", "")))
            if not picture:
                alternatives = item.get("images", [])
                if alternatives:
                    picture = self._decode_image(str(alternatives[0]))
            if picture:
                image.alpha_composite(self._cover(picture, (max(1, int(bar_w)), 84)), (int(bar_x), bar_y))
            else:
                draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 84), fill=color)
            image.alpha_composite(Image.new("RGBA", (max(1, int(bar_w)), 84), "#0a0f11c7"), (int(bar_x), bar_y))
            self._single_line(draw, str(item.get("name", "未命名日程")), (int(bar_x) + 16, bar_y + 18), max(1, int(bar_w) - 30), self._font(16, True), "white")
            self._single_line(draw, str(item.get("countdown", "")), (int(bar_x) + 16, bar_y + 51), max(1, int(bar_w) - 30), self._font(10, True), "white")
        if today_left >= 0:
            draw.line((today_x, y + head_h, today_x, y + head_h + len(items) * row_h), fill=self.YELLOW, width=3)
        return y + head_h + len(items) * row_h

    def _draw_reference_longs(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, items: list[dict[str, Any]]) -> int:
        gap = 12
        card_w = (self.WIDTH - self.PADDING * 2 - gap) // 2
        card_h = 132
        for index, item in enumerate(items):
            x = self.PADDING + (card_w + gap) * (index % 2)
            row_y = y + (card_h + gap) * (index // 2)
            draw.rectangle((x, row_y, x + card_w, row_y + card_h), fill="#24373c")
            picture = self._decode_image(str(item.get("image", "")))
            if picture:
                image.alpha_composite(self._cover(picture, (card_w, card_h)), (x, row_y))
            image.alpha_composite(Image.new("RGBA", (card_w, card_h), "#0a0f127a"), (x, row_y))
            draw.text((x + 23, row_y + 20), f"LONG-TERM CONTENT · {item.get('item_type', '长期活动')}", font=self._font(10, True), fill="#d8e2e4")
            self._single_line(draw, str(item.get("name", "未命名活动")), (x + 23, row_y + 50), card_w - 46, self._font(20, True), "white")
            self._single_line(draw, f"{item.get('start_text', '')} — {item.get('end_text', '')}  {item.get('countdown', '')}", (x + 23, row_y + 89), card_w - 46, self._font(10), "#d8e2e4")
        return y + ((len(items) + 1) // 2) * (card_h + gap) - gap


    def _reference_panel(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: str) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 5, y1 + 7, x2 + 5, y2 + 7), fill="#e5e8e7")
        draw.rectangle(box, fill="white")
        draw.rectangle((x1, y1, x2, y1 + 5), fill=accent)

    def _draw_history(self, data: dict[str, Any]) -> Image.Image:
        events = list(data.get("events", []))
        pools = list(data.get("pools", []))
        height = 201 + 38 + 55 + 70 + max(1, len(events)) * 118 + 38 + 55 + 70 + max(1, len(pools)) * 118 + 92
        image = self._page(height)
        draw = ImageDraw.Draw(image)
        self._history_hero(image, draw, data)
        y = 201 + 38
        y = self._draw_reference_timeline(image, draw, y, "活动日程", "EVENT TIMELINE", events, data.get("ticks", []), -1, data.get("timeline_days", 1))
        y += 38
        y = self._draw_reference_timeline(image, draw, y, "寻访日程", "HEADHUNTING TIMELINE", pools, data.get("ticks", []), -1, data.get("timeline_days", 1), show_type=True)
        draw.rectangle((0, image.height - 72, self.WIDTH, image.height), fill=self.DARK)
        draw.text((self.PADDING, image.height - 49), "历史测试图片 · 数据来源：PRTS Wiki · anything-ics · ArknightsGachaData", font=self._font(12), fill="#c6d0d3")
        return image

    def _history_hero(self, image: Image.Image, draw: ImageDraw.ImageDraw, data: dict[str, Any]) -> None:
        height = 201
        draw.rectangle((0, 0, self.WIDTH, height), fill="#0d1519")
        draw.polygon(((850, 0), (self.WIDTH, 0), (self.WIDTH, height), (790, height)), fill="#1c4549")
        draw.text((64, 45), "P.R.T.S.  //  HISTORY CHECK", font=self._font(13, True), fill="#75d1df")
        draw.text((64, 80), "罗德岛历史日程测试", font=self._font(54, True), fill="white")
        self._single_line(draw, f"{data.get('start_text', '')} — {data.get('end_text', '')} · 仅展示活动与寻访时间轴", (64, 148), 720, self._font(17), "#dce5e7")
        draw.rectangle((1121, 104, 1125, 159), fill=self.YELLOW)
        draw.text((1140, 110), str(data.get("event_count", 0)), font=self._font(35, True), fill="white")
        draw.text((1140, 148), "EVENTS", font=self._font(10, True), fill="white")
        draw.rectangle((1258, 104, 1262, 159), fill=self.YELLOW)
        draw.text((1277, 110), str(data.get("pool_count", 0)), font=self._font(35, True), fill="white")
        draw.text((1277, 148), "HEADHUNTING", font=self._font(10, True), fill="white")

    def _draw_help(self, data: dict[str, Any]) -> Image.Image:
        user_rows = len(data.get("user_commands", []))
        admin_rows = len(data.get("admin_commands", []))
        subscription_rows = len(data.get("subscribable_items", []))
        height = 292 + 38
        if data.get("mode") == "subscribe":
            height += 55 + max(1, math.ceil(subscription_rows / 2)) * 164 + 38
            height += 55 + max(1, math.ceil(user_rows / 2)) * 164
        else:
            height += 55 + max(1, math.ceil(user_rows / 2)) * 164
            if admin_rows:
                height += 38 + 55 + max(1, math.ceil(admin_rows / 2)) * 164
            height += 38 + 55 + max(1, math.ceil(subscription_rows / 2)) * 164
        height += 92
        image = self._page(height)
        draw = ImageDraw.Draw(image)
        self._help_hero(image, draw, data)
        y = 292 + 38
        if data.get("mode") == "subscribe":
            y = self._draw_subscribable_section(image, draw, y, data.get("subscribable_items", []))
            y += 38
            self._draw_command_section(image, draw, y, "订阅指令", "SUBSCRIPTION COMMAND INDEX", data.get("user_commands", []))
        else:
            y = self._draw_command_section(image, draw, y, "普通指令", "USER COMMAND INDEX", data.get("user_commands", []))
            if data.get("admin_commands"):
                y += 38
                y = self._draw_command_section(image, draw, y, "管理员指令", "ADMIN COMMAND INDEX", data.get("admin_commands", []), admin=True)
            y += 38
            self._draw_subscribable_section(image, draw, y, data.get("subscribable_items", []))
        self._footer(draw, image.height, True, data.get("data_date_text", ""))
        return image

    def _help_hero(self, image: Image.Image, draw: ImageDraw.ImageDraw, data: dict[str, Any]) -> None:
        height = 292
        draw.rectangle((0, 0, self.WIDTH, height), fill="#070d11")
        draw.polygon(((770, 0), (self.WIDTH, 0), (self.WIDTH, height), (430, height)), fill="#1d484b")
        for offset in range(-height, self.WIDTH, 54):
            draw.line((offset, 0, offset + height, height), fill="#263034", width=24)
        draw.text((64, 46), "P.R.T.S.  //  TERMINAL MANUAL", font=self._font(13, True), fill="#75d1df")
        draw.text((64, 85), str(data.get("title", "罗德岛终端手册")), font=self._font(55, True), fill="white")
        draw.text((64, 159), str(data.get("subtitle_en", "COMMAND MANUAL")), font=self._font(15, True), fill="#d9e3e5")
        self._multiline(draw, str(data.get("lead", "")), (64, 208), 760, self._font(14), "#e6ecee", line_gap=6, max_lines=2)
        draw.rectangle((1188, 166, 1192, 245), fill=self.YELLOW)
        draw.text((1215, 173), f"v{data.get('version', '')}", font=self._font(36, True), fill="white")
        self._single_line(draw, f"{data.get('date_cn', '')} · {data.get('weekday', '')}", (1215, 224), 175, self._font(13), "white")

    def _page(self, height: int) -> Image.Image:
        image = Image.new("RGBA", (self.WIDTH, height), self.BACKGROUND)
        draw = ImageDraw.Draw(image)
        for offset in range(-height, self.WIDTH + height, 84):
            draw.line((offset, 0, offset + height, height), fill="#e7eaea", width=1)
        return image

    def _draw_highlights(self, image: Image.Image, draw: ImageDraw.ImageDraw, today: dict[str, Any], y: int) -> int:
        groups = [("凭证兑换", today.get("voucher_exchange", [])), ("新时装", today.get("new_skins", [])), ("新模组", today.get("new_modules", []))]
        gap = 14
        width = (self.WIDTH - self.PADDING * 2 - gap * 2) // 3
        height = 170
        for index, (title, values) in enumerate(groups):
            x = self.PADDING + index * (width + gap)
            self._panel(draw, (x, y, x + width, y + height), "#29383d")
            draw.text((x + 16, y + 15), title, font=self._font(16, True), fill=self.TEXT)
            items = values[:4] if isinstance(values, list) else []
            if not items:
                draw.text((x + 16, y + 76), "暂无相关情报", font=self._font(13), fill=self.MUTED)
                continue
            item_w = (width - 32 - 8 * (len(items) - 1)) // len(items)
            for item_index, item in enumerate(items):
                ix = x + 16 + item_index * (item_w + 8)
                picture = self._decode_image(str(item.get("image", "")))
                if picture:
                    image.alpha_composite(self._cover(picture, (item_w, 91)), (ix, y + 46))
                else:
                    draw.rectangle((ix, y + 46, ix + item_w, y + 137), fill="#dde4e4")
                self._single_line(draw, str(item.get("name", "未命名")), (ix, y + 143), item_w, self._font(10), self.TEXT)
        return y + height

    def _draw_command_section(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, title: str, code: str, commands: list[dict[str, Any]], admin: bool = False) -> int:
        y = self._calendar_section_title(draw, y, title, code)
        if not commands:
            return y
        card_w = (self.WIDTH - self.PADDING * 2 - 14) // 2
        card_h = 142
        for index, command in enumerate(commands):
            row, col = divmod(index, 2)
            x, card_y = self.PADDING + col * (card_w + 14), y + row * (card_h + 14)
            self._panel(draw, (x, card_y, x + card_w, card_y + card_h), self.YELLOW if admin else self.CYAN)
            name = "/" + str(command.get("name", ""))
            draw.text((x + 18, card_y + 20), name, font=self._font(19, True), fill=self.TEXT)
            hint = str(command.get("argument_hint", ""))
            if hint:
                draw.text((x + 22 + self._measure(name, self._font(19, True)), card_y + 27), hint, font=self._font(11), fill="#087c92")
            self._multiline(draw, str(command.get("summary", "")), (x + 18, card_y + 57), card_w - 36, self._font(12), "#39464b", line_gap=5, max_lines=2)
            example = str(command.get("example", ""))
            if example:
                draw.rectangle((x + 18, card_y + 108, x + card_w - 18, card_y + 132), fill=self.DARK)
                self._single_line(draw, example, (x + 27, card_y + 114), card_w - 54, self._font(10), "#e9eeee")
        return y + math.ceil(len(commands) / 2) * (card_h + 14) - 14

    def _draw_subscribable_section(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, items: list[dict[str, Any]]) -> int:
        y = self._calendar_section_title(draw, y, "可订阅日程", f"SUBSCRIBABLE SCHEDULE / {len(items)} ENTRIES")
        if not items:
            draw.rectangle((self.PADDING, y, self.WIDTH - self.PADDING, y + 88), fill="white")
            draw.text((self.PADDING + 24, y + 33), "当前没有进行中或即将开启的活动与寻访。", font=self._font(15), fill=self.MUTED)
            return y + 88
        card_w, card_h = (self.WIDTH - self.PADDING * 2 - 14) // 2, 150
        for index, item in enumerate(items):
            row, col = divmod(index, 2)
            x, card_y = self.PADDING + col * (card_w + 14), y + row * (card_h + 14)
            color = str(item.get("color", "#4d8a72"))
            self._panel(draw, (x, card_y, x + card_w, card_y + card_h), color)
            label = str(item.get("type_label", "日程"))
            draw.rounded_rectangle((x + 20, card_y + 20, x + 20 + max(72, self._measure(label, self._font(11)) + 16), card_y + 44), radius=3, fill=color)
            draw.text((x + 28, card_y + 26), label, font=self._font(11, True), fill="white")
            self._single_line(draw, str(item.get("name", "")), (x + 20, card_y + 57), card_w - 40, self._font(18, True), self.TEXT)
            self._single_line(draw, f"{item.get('start_text', '')} — {item.get('end_text', '')}", (x + 20, card_y + 86), card_w - 40, self._font(11), self.MUTED)
            draw.text((x + 20, card_y + 108), str(item.get("countdown", "")), font=self._font(12, True), fill="#b36d10")
        return y + math.ceil(len(items) / 2) * (card_h + 14) - 14

    def _footer(self, draw: ImageDraw.ImageDraw, height: int, show_source: bool, date: str) -> None:
        y = height - 72
        draw.rectangle((0, y, self.WIDTH, height), fill=self.DARK)
        text = "数据来源：PRTS Wiki · anything-ics · ArknightsGachaData" if show_source else "罗德岛行动日历"
        draw.text((self.PADDING, y + 20), text, font=self._font(12), fill="#c6d0d3")
        draw.text((self.PADDING, y + 43), f"数据日期：{date}（北京时间） · Generated by astrbot_plugin_ark_calendar", font=self._font(11), fill="#9ba7ac")

    def _calendar_height(self, snapshot: dict[str, Any]) -> int:
        events = len(snapshot.get("events", []))
        pools = len(snapshot.get("gacha_pools", []))
        longs = len(snapshot.get("long_term_events", []))
        height = 292 + 16
        height += 55 + 378 + 38
        birthday_count = len(snapshot.get("today_birthdays", []))
        birthday_height = max(196, 58 + math.ceil(birthday_count / 2) * 100)
        height += 55 + birthday_height + 38
        if snapshot.get("recent_operators"):
            height += 55 + 94 + 38
        today = snapshot.get("today_info", {})
        if any(today.get(key) for key in ("voucher_exchange", "new_skins", "new_modules")):
            height += 55 + 170 + 38
        height += 55 + 70 + max(1, events) * 118
        if longs:
            height += 16 + ((longs + 1) // 2) * 144
        height += 38 + 55 + 70 + max(1, pools) * 118
        return height + 92

    def _panel(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: str) -> None:
        x1, y1, x2, y2 = box
        draw.rounded_rectangle((x1 + 4, y1 + 6, x2 + 4, y2 + 6), radius=2, fill="#e0e5e4")
        draw.rectangle(box, fill="white")
        draw.rectangle((x1, y1, x2, y1 + 5), fill=accent)

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (size, bold)
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        candidates = [
            self.assets_dir / "SourceHanSerifCN-Medium-6.otf",
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
        for path in candidates:
            try:
                if path.is_file():
                    font = ImageFont.truetype(str(path), size=size, index=0)
                    self._fonts[key] = font
                    return font
            except OSError:
                continue
        font = ImageFont.load_default()
        self._fonts[key] = font
        return font

    @staticmethod
    def _decode_image(source: str) -> Image.Image | None:
        if not source or not isinstance(source, str):
            return None
        try:
            if source.startswith("data:image/"):
                _, encoded = source.split(",", 1)
                payload = base64.b64decode(encoded, validate=True)
                with Image.open(io.BytesIO(payload)) as opened:
                    return ImageOps.exif_transpose(opened).convert("RGBA")
            path = Path(source)
            if path.is_file():
                with Image.open(path) as opened:
                    return ImageOps.exif_transpose(opened).convert("RGBA")
        except (OSError, ValueError, base64.binascii.Error):
            return None
        return None

    @staticmethod
    def _cover(source: Image.Image, size: tuple[int, int]) -> Image.Image:
        return ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    def _single_line(self, draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], width: int, font: ImageFont.ImageFont, fill: str) -> None:
        value = self._ellipsis(text, width, font)
        draw.text(xy, value, font=font, fill=fill)

    def _multiline(self, draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], width: int, font: ImageFont.ImageFont, fill: str, *, line_gap: int, max_lines: int) -> int:
        lines = self._wrap(text, width, font)[:max_lines]
        for index, line in enumerate(lines):
            draw.text((xy[0], xy[1] + index * (font.size + line_gap)), line, font=font, fill=fill)
        return len(lines)

    def _wrap(self, text: str, width: int, font: ImageFont.ImageFont) -> list[str]:
        words = re.split(r"(\s+)", str(text).strip())
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = current + word
            if current and self._measure(candidate, font) > width:
                lines.append(current.rstrip())
                current = word.lstrip()
            elif not current and self._measure(word, font) > width:
                fragment = ""
                for char in word:
                    if fragment and self._measure(fragment + char, font) > width:
                        lines.append(fragment)
                        fragment = char
                    else:
                        fragment += char
                current = fragment
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
        return lines or [""]

    def _ellipsis(self, text: str, width: int, font: ImageFont.ImageFont) -> str:
        value = str(text)
        if self._measure(value, font) <= width:
            return value
        suffix = "…"
        while value and self._measure(value + suffix, font) > width:
            value = value[:-1]
        return value + suffix

    @staticmethod
    def _measure(text: str, font: ImageFont.ImageFont) -> int:
        try:
            return int(font.getlength(text))
        except AttributeError:
            return int(font.getbbox(text)[2])
