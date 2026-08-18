from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")
# 明日方舟服务器 04:00 日切，00:00-04:00 期间游戏内仍是前一天的开放表。
GAME_DAILY_RESET_HOUR = 4


def game_weekday(now: datetime | None = None) -> int:
    """按游戏日切（04:00）折算的周几，取值与 datetime.weekday() 一致。"""
    return ((now or datetime.now(CN_TZ)) - timedelta(hours=GAME_DAILY_RESET_HOUR)).weekday()


class PrtsSource:
    RECURRENCE_PAGE = "卡池一览/寻访概率提升"

    def __init__(self, http: HttpClient, base_url: str):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api.php"

    async def home(self, now: datetime | None = None) -> dict:
        html = await self.http.text(f"{self.base_url}/")
        soup = BeautifulSoup(html, "html.parser")
        compact = soup.get_text(" ", strip=True)
        weekday = game_weekday(now)
        resource_schedule = self._resource_schedule(soup, weekday, self.base_url)
        chip_schedule = self._chip_schedule(soup, weekday, self.base_url)
        supplies = [item["name"] for item in resource_schedule if item.get("open")]
        chips = [item["name"] for item in chip_schedule if item.get("open")]
        if not supplies:
            supplies_text = self._between(compact, "物资筹备 分区：", "芯片搜索 分区：")
            supplies = self._slash_items(supplies_text)
        if not chips:
            chips_text = self._between(compact, "芯片搜索 分区：", "职业芯片")
            chips = self._slash_items(chips_text.replace("&", "/"))
        alerts: list[dict[str, str]] = []
        seen_alerts: set[tuple[str, str]] = set()
        for span in soup.select("span[data-time]"):
            container = span.find_parent("p") or span.find_parent("div")
            text = container.get_text(" ", strip=True) if container else ""
            if not any(key in text for key in ("剿灭", "保全派驻", "限时寻访", "网页活动")):
                continue
            ts = str(span.get("data-time", ""))
            key = (text, ts)
            if key in seen_alerts:
                continue
            seen_alerts.add(key)
            try:
                end = datetime.fromtimestamp(int(ts), CN_TZ)
                end_text = end.strftime("%m.%d %H:%M")
            except (TypeError, ValueError, OSError):
                end_text = ""
            kind = "临期事项"
            if "剿灭" in text:
                kind = "周常刷新"
            elif "保全派驻" in text:
                kind = "保全派驻"
            elif "寻访" in text:
                kind = "寻访结束"
            elif "网页活动" in text:
                kind = "网页活动"
            name = re.sub(r"将于.*", "", text).strip(" 。")
            alerts.append({"kind": kind, "name": name, "time": end_text})
        return {
            "supplies": supplies,
            "chips": chips,
            "alerts": alerts[:6],
            "resource_schedule": resource_schedule,
            "chip_schedule": chip_schedule,
            "recent": self._operator_section(soup, "近期新增"),
            "birthday": self._operator_section(soup, "今天生日"),
            "voucher_exchange": self._highlight_section(soup, "凭证兑换", self.base_url),
            "new_skins": self._highlight_section(soup, "新增时装", self.base_url),
            "new_modules": self._highlight_section(soup, "新增模组", self.base_url, split_subtitle=True),
        }

    @classmethod
    def _resource_schedule(cls, soup: BeautifulSoup, weekday: int, base_url: str) -> list[dict]:
        table = cls._resource_table(soup)
        if table is None:
            return []
        rows = table.find_all("tr")
        if len(rows) < 4:
            return []
        resource_cells = cls._direct_cells(rows[0])
        resource_days = cls._direct_cells(rows[1])
        result: list[dict] = []
        for cell, day_cell in zip(resource_cells, resource_days):
            image = cell.select_one("img[src]")
            if not image:
                continue
            src = urljoin(base_url, image.get("src", ""))
            file_name = cls._file_name(src)
            name = cls._resource_name(file_name)
            if not name:
                continue
            day_text = day_cell.get_text(" ", strip=True)
            always_open, allowed = cls._days_from_label(day_text)
            style_open = cls._style_is_open(cell.get("style", ""))
            result.append({
                "name": name,
                "image": src,
                "weekdays": allowed,
                "weekdays_label": day_text,
                "always_open": always_open,
                "style_open": style_open,
                "open": always_open or weekday in allowed or style_open,
            })
        all_open = bool(result) and all(item.get("style_open") for item in result)
        for item in result:
            item["all_open"] = all_open
        return result

    @classmethod
    def _chip_schedule(cls, soup: BeautifulSoup, weekday: int, base_url: str) -> list[dict]:
        table = cls._resource_table(soup)
        if table is None:
            return []
        rows = table.find_all("tr")
        if len(rows) < 4:
            return []
        cells = cls._direct_cells(rows[2])
        day_cells = cls._direct_cells(rows[3])
        result: list[dict] = []
        chip_names = ["术师&狙击", "先锋&辅助", "医疗&重装", "近卫&特种"]
        for index, (cell, day_cell) in enumerate(zip(cells, day_cells)):
            image = cell.select_one("img[src]")
            if not image:
                continue
            src = urljoin(base_url, image.get("src", ""))
            file_name = cls._file_name(src)
            stage_key = cls._chip_stage_key(file_name)
            name = cls._chip_name(stage_key) or (chip_names[index] if index < len(chip_names) else f"芯片组 {index + 1}")
            day_text = day_cell.get_text(" ", strip=True)
            always_open, allowed = cls._days_from_label(day_text)
            style_open = cls._style_is_open(cell.get("style", ""))
            result.append({
                "name": name,
                "image": src,
                "stage": stage_key,
                "weekdays": allowed,
                "weekdays_label": day_text,
                "always_open": always_open,
                "style_open": style_open,
                "open": always_open or weekday in allowed or style_open,
            })
        all_open = bool(result) and all(item.get("style_open") for item in result)
        for item in result:
            item["all_open"] = all_open
        return result

    @staticmethod
    def _resource_table(soup: BeautifulSoup):
        for table in soup.find_all("table"):
            text = table.get_text(" ", strip=True)
            if "常驻" in text and "二三五日" in text and "一四六日" in text:
                rows = table.find_all("tr")
                if len(rows) >= 4 and sum(len(row.select("img[src]")) for row in rows[:3]) >= 5:
                    return table
        return None

    @staticmethod
    def _direct_cells(row) -> list:
        return row.find_all(["td", "th"], recursive=False)

    @staticmethod
    def _file_name(url: str) -> str:
        return unquote(PurePosixPath(urlparse(url).path).name).removeprefix("80px-")

    @staticmethod
    def _resource_name(file_name: str) -> str:
        if "高级作战记录" in file_name:
            return "作战记录"
        if "技巧概要" in file_name:
            return "技巧概要"
        if "龙门币" in file_name:
            return "龙门币"
        if "采购凭证" in file_name:
            return "采购凭证"
        if "碳素" in file_name:
            return "碳&家具零件"
        return ""

    @staticmethod
    def _chip_stage_key(file_name: str) -> str:
        for key in ("摧枯拉朽", "身先士卒", "固若金汤", "势不可挡"):
            if key in file_name:
                return key
        return ""

    @staticmethod
    def _chip_name(stage_key: str) -> str:
        return {
            "摧枯拉朽": "术师&狙击",
            "身先士卒": "先锋&辅助",
            "固若金汤": "医疗&重装",
            "势不可挡": "近卫&特种",
        }.get(stage_key, "")

    @staticmethod
    def _days_from_label(label: str) -> tuple[bool, list[int]]:
        compact = re.sub(r"\s+", "", label)
        if any(token in compact for token in ("常驻", "全开放", "每日")):
            return True, list(range(7))
        mapping = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
        return False, list(dict.fromkeys(mapping[ch] for ch in compact if ch in mapping))

    @staticmethod
    def _style_is_open(style: str) -> bool:
        colors = re.findall(r"#[0-9a-fA-F]{6}", style)
        if not colors:
            return False
        color = colors[-1].lower()
        if color in {"#343434", "#484848"}:
            return False
        if color in {"#585858", "#808080", "#324c65", "#3e5f84"}:
            return True
        try:
            rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            return sum(rgb) / 3 >= 82
        except ValueError:
            return False

    @staticmethod
    def _highlight_section(soup: BeautifulSoup, title: str, base_url: str, split_subtitle: bool = False) -> list[dict]:
        for node in soup.find_all(string=lambda value: value and title in value):
            section = node.find_parent(class_="mp-operators-content")
            if not section:
                continue
            result = []
            for anchor in section.select("a[title]"):
                image = anchor.select_one("img#charicon")
                name = anchor.get("title", "").strip()
                if not name or not image:
                    continue
                subtitle = ""
                if split_subtitle and "#" in name:
                    name, subtitle = name.split("#", 1)
                result.append({
                    "name": name,
                    "subtitle": subtitle,
                    "image": urljoin(base_url, image.get("src", "")),
                    "href": urljoin(base_url, anchor.get("href", "")),
                })
            if result:
                seen = {}
                for item in result:
                    seen[(item["name"], item["subtitle"])] = item
                return list(seen.values())
        return []

    async def operator_index(self) -> dict[str, dict]:
        html = await self.http.text(f"{self.base_url}/w/{quote('干员一览')}")
        soup = BeautifulSoup(html, "html.parser")
        result: dict[str, dict] = {}
        for node in soup.select("#filter-data > div[data-zh]"):
            name = node.get("data-zh", "").strip()
            if not name:
                continue
            rarity = node.get("data-rarity")
            result[name] = {
                "profession": node.get("data-profession", ""),
                "rarity": int(rarity) + 1 if rarity and rarity.isdigit() else None,
            }
        return result

    async def resolve_avatar_urls(self, names: list[str]) -> dict[str, str]:
        unique_names = list(dict.fromkeys(name for name in names if name))
        if not unique_names:
            return {}
        result: dict[str, str] = {}
        # MediaWiki 对单次 titles 数量有限制，分批请求也可避免 URL 过长。
        for offset in range(0, len(unique_names), 20):
            batch = unique_names[offset:offset + 20]
            titles = "|".join(f"File:头像_{name}.png" for name in batch)
            data = await self.http.json(self.api_url, params={
                "action": "query", "titles": titles, "prop": "imageinfo",
                "iiprop": "url", "format": "json", "formatversion": "2",
            })
            for page in data.get("query", {}).get("pages", []):
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                url = infos[0].get("url", "")
                name = self._avatar_name_from_url(url)
                if name:
                    result[name] = url
        return result

    @staticmethod
    def _avatar_name_from_url(url: str) -> str:
        file_name = unquote(PurePosixPath(urlparse(url).path).name)
        match = re.match(r"头像_(.+)\.png$", file_name, re.I)
        return match.group(1) if match else ""

    async def event_detail(self, name: str) -> dict:
        data = await self.http.json(self.api_url, params={
            "action": "parse", "page": name, "prop": "wikitext", "format": "json",
        })
        text = data.get("parse", {}).get("wikitext", {}).get("*", "")
        detail = {
            "type": self._field(text, "类型") or "活动",
            "image_file": self._field(text, "标题图文件名"),
            "exchange_end": self._field(text, "兑换结束时间"),
        }
        if detail["image_file"]:
            detail["image_url"] = await self.resolve_file_url(detail["image_file"])
        return detail

    async def resolve_file_url(self, file_name: str) -> str:
        data = await self.http.json(self.api_url, params={
            "action": "query", "titles": f"File:{file_name}", "prop": "imageinfo",
            "iiprop": "url", "format": "json", "formatversion": "2",
        })
        pages = data.get("query", {}).get("pages", [])
        infos = pages[0].get("imageinfo") if pages else None
        return infos[0].get("url", "") if infos else ""

    async def gacha_overview(self) -> list[dict]:
        data = await self.http.json(self.api_url, params={
            "action": "parse", "page": "卡池一览", "prop": "text", "format": "json",
        })
        html = data.get("parse", {}).get("text", {}).get("*", "")
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict] = []
        for table in soup.select("table.wikitable"):
            header_row = table.find("tr")
            if not header_row:
                continue
            headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all(["th", "td"], recursive=False)]
            name_index = self._header_index(headers, ("卡池一览", "卡池名称", "寻访名称"))
            time_index = self._header_index(headers, ("开启时间", "开放时间", "寻访时间"))
            six_index = self._header_index(headers, ("6星", "六星"))
            if time_index is None or six_index is None:
                continue
            for row in table.select("tr")[1:]:
                cells = row.find_all(["td", "th"], recursive=False)
                if max(time_index, six_index) >= len(cells):
                    continue
                time_text = cells[time_index].get_text(" ", strip=True)
                times = re.findall(r"20\d{2}-\d{2}-\d{2} \d{2}:\d{2}", time_text)
                if len(times) < 2:
                    continue
                six = [a.get("title", "").strip() for a in cells[six_index].select("a[title]")]
                six = list(dict.fromkeys(x for x in six if x and not x.startswith(("文件:", "File:"))))
                name_cell = cells[name_index] if name_index is not None and name_index < len(cells) else cells[0]
                raw_name = name_cell.get_text(" ", strip=True)
                pool_name = self._gacha_name(raw_name)
                image = name_cell.select_one("img") or row.select_one("img")
                rows.append({
                    "name": pool_name, "raw_name": raw_name,
                    "start": times[0], "end": times[1], "six": six,
                    "image": urljoin(self.base_url, image.get("src", "")) if image else "",
                })
        return rows

    async def recurrence_overview(self) -> list[dict]:
        """读取 PRTS 已展开的干员出率提升/商店兑换历史表。"""
        data = await self.http.json(self.api_url, params={
            "action": "parse", "page": self.RECURRENCE_PAGE,
            "prop": "text", "format": "json",
        })
        html = data.get("parse", {}).get("text", {}).get("*", "")
        return self._parse_recurrence_overview(html)

    @classmethod
    def _parse_recurrence_overview(cls, html: str) -> list[dict]:
        """将 PRTS 的两层表头规范化为可排序的干员历史记录。

        页面本身由 Semantic MediaWiki 汇总，直接复用其已计算的历史关系，
        插件只负责校验、缓存和按调用时刻重新计算排序。
        """
        soup = BeautifulSoup(html, "html.parser")
        result: list[dict] = []
        for table in soup.select("table.wikitable"):
            headers = table.get_text(" ", strip=True)
            if not all(token in headers for token in ("实装时间", "出率提升", "商店兑换")):
                continue
            rarity = cls._recurrence_rarity(table)
            pool_type = cls._recurrence_pool_type(table)
            if rarity is None or not pool_type:
                continue
            for row in table.select("tr"):
                cells = []
                for cell in row.find_all("td", recursive=False):
                    try:
                        colspan = max(1, int(cell.get("colspan", 1)))
                    except (TypeError, ValueError):
                        colspan = 1
                    cells.extend([cell] * colspan)
                if len(cells) < 9:
                    continue
                release_date = cls._date_from_cell(cells[0])
                name = cls._recurrence_name(cells[1])
                rate_end = cls._date_from_cell(cells[3])
                shop_end = cls._date_from_cell(cells[6])
                if not release_date or not name or not rate_end:
                    continue
                result.append({
                    "name": name,
                    "rarity": rarity,
                    "pool_type": pool_type,
                    "release_date": release_date,
                    "rate_up_end": rate_end,
                    "rate_up_ongoing": "进行中" in cells[4].get_text(" ", strip=True),
                    "rate_up_count": cls._cell_int(cells[5]),
                    "shop_end": shop_end,
                    "shop_count": cls._cell_int(cells[8]),
                })
        return result

    @staticmethod
    def _date_from_cell(cell) -> str:
        match = re.search(r"20\d{2}-\d{2}-\d{2}", cell.get_text(" ", strip=True))
        return match.group(0) if match else ""

    @staticmethod
    def _cell_int(cell) -> int:
        match = re.search(r"\d+", cell.get_text(" ", strip=True))
        return int(match.group(0)) if match else 0

    @staticmethod
    def _recurrence_name(cell) -> str:
        anchor = cell.select_one("a[title]")
        return (anchor.get("title", "") if anchor else cell.get_text(" ", strip=True)).strip()

    @staticmethod
    def _recurrence_rarity(table) -> int | None:
        heading = table.find_previous("h2")
        text = heading.get_text(" ", strip=True) if heading else ""
        if "六星" in text:
            return 6
        if "五星" in text:
            return 5
        return None

    @staticmethod
    def _recurrence_pool_type(table) -> str:
        heading = table.find_previous("h3")
        text = heading.get_text(" ", strip=True) if heading else ""
        if "中坚" in text:
            return "中坚寻访"
        if "标准" in text:
            return "标准寻访"
        return ""

    @staticmethod
    def _gacha_name(text: str) -> str:
        """从 PRTS 表格单元格中提取卡池正式名称。"""
        # PRTS 可能在正式卡池标题前加上带括号的分类前缀，
        # 去掉前缀后的部分才是其他数据源通用的卡池名称。
        return re.sub(r"^[【『「][^】』」]+[】』」]\s*", "", text.strip()).strip()

    @staticmethod
    def _header_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
        for index, header in enumerate(headers):
            if any(candidate in header for candidate in candidates):
                return index
        return None

    @staticmethod
    def _field(text: str, name: str) -> str:
        match = re.search(rf"^\|{re.escape(name)}\s*=\s*(.*?)\s*$", text, re.M)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _between(text: str, start: str, end: str) -> str:
        match = re.search(re.escape(start) + r"\s*(.*?)\s*" + re.escape(end), text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _slash_items(text: str) -> list[str]:
        return [x.strip() for x in re.split(r"[/／]", text) if x.strip()]

    @staticmethod
    def _operator_section(soup: BeautifulSoup, title: str) -> list[dict]:
        for node in soup.find_all(string=lambda value: value and title in value):
            section = node.find_parent(class_="mp-operators-content")
            if not section:
                continue
            result = []
            for anchor in section.select("a[title]"):
                image = anchor.select_one("img")
                name = anchor.get("title", "").strip()
                if name and image:
                    result.append({"name": name, "avatar": image.get("src", "")})
            if result:
                return list({x["name"]: x for x in result}.values())
        return []
