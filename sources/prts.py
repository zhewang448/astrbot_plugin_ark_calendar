from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")


class PrtsSource:
    def __init__(self, http: HttpClient, base_url: str):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api.php"

    async def home(self) -> dict:
        html = await self.http.text(f"{self.base_url}/")
        soup = BeautifulSoup(html, "html.parser")
        compact = soup.get_text(" ", strip=True)
        supplies = self._between(compact, "物资筹备 分区：", "芯片搜索 分区：")
        chips = self._between(compact, "芯片搜索 分区：", "职业芯片")
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
            "supplies": self._slash_items(supplies),
            "chips": self._slash_items(chips.replace("&", "/")),
            "alerts": alerts[:6],
            "recent": self._operator_section(soup, "近期新增"),
            "birthday": self._operator_section(soup, "今天生日"),
        }

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
        titles = "|".join(f"File:头像_{name}.png" for name in dict.fromkeys(names) if name)
        if not titles:
            return {}
        data = await self.http.json(self.api_url, params={
            "action": "query", "titles": titles, "prop": "imageinfo",
            "iiprop": "url", "format": "json", "formatversion": "2",
        })
        result: dict[str, str] = {}
        for page in data.get("query", {}).get("pages", []):
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            url = infos[0].get("url", "")
            file_name = unquote(PurePosixPath(urlparse(url).path).name)
            match = re.match(r"头像_(.+)\.png$", file_name, re.I)
            if match:
                result[match.group(1)] = url
        return result

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
                image = row.select_one("img")
                rows.append({
                    "start": times[0], "end": times[1], "six": six,
                    "image": urljoin(self.base_url, image.get("src", "")) if image else "",
                })
        return rows

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
