from __future__ import annotations

import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")


class GachaSource:
    SERVER_DATA_URL = "https://weedy.prts.wiki/gacha_table.json"
    CHARACTER_URL = "https://torappu.prts.wiki/gamedata/latest/excel/character_table.json"

    def __init__(self, http: HttpClient, pool_info_url: str):
        self.http = http
        self.pool_info_url = pool_info_url
        self.last_source_states: list[dict] = []

    async def pools(self, start: datetime, end: datetime, overview: list[dict]) -> list[dict]:
        labels = (
            "ArknightsGachaData",
            "PRTS Gacha Server Data",
            "明日方舟角色数据",
        )
        results = await asyncio.gather(
            self.http.json(self.pool_info_url),
            self.http.json(self.SERVER_DATA_URL),
            self.http.json(self.CHARACTER_URL),
            return_exceptions=True,
        )
        self.last_source_states = [
            {
                "name": label,
                "ok": not isinstance(value, Exception),
                "message": "" if not isinstance(value, Exception) else str(value),
            }
            for label, value in zip(labels, results)
        ]
        pool_info, server_data, character_table = results
        if isinstance(pool_info, Exception):
            raise pool_info
        if not isinstance(pool_info, dict):
            raise ValueError("ArknightsGachaData 返回格式不正确")
        server_data = server_data if isinstance(server_data, dict) else {}
        character_table = character_table if isinstance(character_table, dict) else {}

        clients = server_data.get("gachaPoolClient", [])
        server_map = {
            item.get("gachaPoolId"): item
            for item in clients
            if isinstance(item, dict) and item.get("gachaPoolId")
        }
        result: list[dict] = []
        for pool in pool_info.get("pool", {}).values():
            if not isinstance(pool, dict) or "start" not in pool or "end" not in pool:
                continue
            pool_start = datetime.fromtimestamp(pool["start"], CN_TZ)
            pool_end = datetime.fromtimestamp(pool["end"], CN_TZ)
            pool_type = pool.get("type", "")
            if pool_end < start or pool_start > end:
                continue
            # 归航寻访按账号回归时间触发，不属于全服统一日历。
            if pool_type == "BACKFLOW":
                continue
            server = server_map.get(pool.get("id"), {})
            six, weighted = self._up_names(server, character_table)
            match = self._match_overview(pool_start, pool_end, overview, pool.get("name", ""))
            if match and match.get("six"):
                six = match["six"]
            result.append({
                "id": pool.get("id", ""),
                "name": pool.get("name", ""),
                "type": pool_type,
                "start": pool_start,
                "end": pool_end,
                "six": six,
                "weighted": weighted,
                "image": match.get("image", "") if match else "",
            })
        return sorted(result, key=lambda item: (item["start"], item["end"]))

    @classmethod
    def _match_overview(
        cls,
        start: datetime,
        end: datetime,
        rows: list[dict],
        pool_name: str = "",
    ) -> dict | None:
        parsed: list[tuple[dict, datetime, datetime]] = []
        for row in rows:
            try:
                row_start = datetime.strptime(row["start"], "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
                row_end = datetime.strptime(row["end"], "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
            except (KeyError, ValueError):
                continue
            parsed.append((row, row_start, row_end))
            if abs((row_start - start).total_seconds()) <= 120 and abs((row_end - end).total_seconds()) <= 120:
                return row

        normalized = cls._normalize_pool_name(pool_name)
        if normalized:
            for row, row_start, row_end in parsed:
                row_name = cls._normalize_pool_name(row.get("name", ""))
                if not row_name or not (row_name == normalized or row_name in normalized or normalized in row_name):
                    continue
                # PRTS 与服务器数据偶尔会对同日开放时间采用不同口径；名称一致且结束时间接近时仍可安全匹配。
                if abs((row_end - end).total_seconds()) <= 6 * 3600:
                    return row
                if row_start.date() == start.date() and row_end.date() == end.date():
                    return row
        return None

    @staticmethod
    def _normalize_pool_name(name: str) -> str:
        return re.sub(r"[\s·・\-—_【】『』「」]", "", name or "").lower()

    @staticmethod
    def _up_names(server: dict, characters: dict) -> tuple[list[str], list[str]]:
        detail = server.get("gachaPoolDetail", {}).get("detailInfo", {}) if isinstance(server, dict) else {}
        up = detail.get("upCharInfo", {}).get("perCharList", []) or []
        six: list[str] = []
        for group in up:
            if not isinstance(group, dict) or group.get("rarityRank") != 5:
                continue
            for char_id in group.get("charIdList", []) or []:
                name = characters.get(char_id, {}).get("name")
                if name:
                    six.append(name)
        weighted: list[str] = []
        for group in detail.get("weightUpCharInfoList", []) or []:
            if not isinstance(group, dict):
                continue
            for char_id in group.get("charIdList", []) or []:
                name = characters.get(char_id, {}).get("name")
                if name:
                    weighted.append(name)
        return list(dict.fromkeys(six)), list(dict.fromkeys(weighted))

    @staticmethod
    def label(pool_type: str) -> str:
        return {
            "LIMITED": "限定寻访", "LINKAGE": "联动寻访", "SINGLE": "单人寻访",
            "DOUBLE": "标准寻访", "CLASSIC_DOUBLE": "中坚寻访",
            "CLASSIC": "中坚寻访", "BACKFLOW": "归航寻访",
            "SPECIAL": "特殊寻访", "ATTAIN": "定向寻访",
        }.get(pool_type, "限时寻访")
