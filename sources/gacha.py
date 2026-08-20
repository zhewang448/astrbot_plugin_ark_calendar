from __future__ import annotations

import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")


class GachaSource:
    TORAPPU_URL = "https://torappu.prts.wiki/gamedata/latest/excel/gacha_table.json"
    SERVER_DATA_URL = "https://weedy.prts.wiki/gacha_table.json"
    CHARACTER_URL = "https://torappu.prts.wiki/gamedata/latest/excel/character_table.json"

    def __init__(self, http: HttpClient, pool_info_url: str):
        self.http = http
        self.pool_info_url = pool_info_url
        self.last_source_states: list[dict] = []

    async def pools(self, start: datetime, end: datetime, overview: list[dict]) -> list[dict]:
        labels = (
            "Torappu / gacha_table.json",
            "ArknightsGachaData",
            "PRTS Gacha Server Data",
            "明日方舟角色数据",
        )
        results = await asyncio.gather(
            self.http.json(self.TORAPPU_URL),
            self.http.json(self.pool_info_url),
            self.http.json(self.SERVER_DATA_URL),
            self.http.json(self.CHARACTER_URL),
            return_exceptions=True,
        )
        validators = (
            self._valid_torappu_data,
            self._valid_pool_info,
            self._valid_server_data,
            self._valid_character_table,
        )
        normalized: list[dict] = []
        self.last_source_states = []
        for label, value, validator in zip(labels, results, validators):
            error: Exception | None = value if isinstance(value, Exception) else None
            if error is None and not validator(value):
                error = ValueError(f"{label} 返回的数据结构异常")
            if error is not None:
                normalized.append({})
                self.last_source_states.append({
                    "name": label,
                    "ok": False,
                    "message": str(error),
                    "event_key": self._event_key(label, error),
                    "status": "failed",
                })
            else:
                normalized.append(value)
                self.last_source_states.append({
                    "name": label,
                    "ok": True,
                    "message": "",
                    "event_key": "",
                    "status": "fresh",
                })

        torappu_data, pool_info, server_data, character_table = normalized
        if not self.last_source_states[0]["ok"] and not self.last_source_states[1]["ok"]:
            original = results[0] if isinstance(results[0], Exception) else results[1]
            if isinstance(original, Exception):
                raise original
            raise ValueError("Torappu 与 ArknightsGachaData 均返回了异常数据")
        if not self.last_source_states[0]["ok"] and self.last_source_states[1]["ok"]:
            self.last_source_states[0]["status"] = "fallback"
            self.last_source_states[0]["message"] = "实时数据不可用，已回退到 ArknightsGachaData 时间轴"

        clients = server_data.get("gachaPoolClient", [])
        server_map = {
            item.get("gachaPoolId"): item
            for item in clients
            if isinstance(item, dict) and item.get("gachaPoolId")
        }
        legacy_map = {}
        if isinstance(pool_info, dict):
            legacy_map = {
                item.get("id"): item
                for item in pool_info.get("pool", {}).values()
                if isinstance(item, dict) and item.get("id")
            }
        if self.last_source_states[0]["ok"] and isinstance(torappu_data, dict):
            base_pools = torappu_data.get("gachaPoolClient", [])
        else:
            base_pools = list(legacy_map.values())
        result: list[dict] = []
        for raw_pool in base_pools:
            if not isinstance(raw_pool, dict):
                continue
            pool_id = raw_pool.get("gachaPoolId") or raw_pool.get("id")
            legacy = legacy_map.get(pool_id, {})
            start_value = raw_pool.get("openTime", raw_pool.get("start"))
            end_value = raw_pool.get("endTime", raw_pool.get("end"))
            try:
                pool_start = datetime.fromtimestamp(float(start_value), CN_TZ)
                pool_end = datetime.fromtimestamp(float(end_value), CN_TZ)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            pool_type = raw_pool.get("gachaRuleType") or legacy.get("type", "")
            if pool_end < start or pool_start > end:
                continue
            # 归航寻访按账号回归时间触发，不属于全服统一日历。
            if pool_type == "BACKFLOW":
                continue
            server = server_map.get(pool_id, {})
            six, weighted = self._up_names(server, character_table)
            pool_name = legacy.get("name") or raw_pool.get("gachaPoolName", "")
            match = self._match_overview(pool_start, pool_end, overview, pool_name)
            if match and match.get("six"):
                six = match["six"]
            # ArknightsGachaData 维护了带期数的正式名称（例如“中坚甄选-第一十四期”）。
            # PRTS 的卡池一览名称有时会被压缩成“甄选14”“联合行动23”等短名，
            # 因此只有在正式名称缺失或属于占位值时，才用 PRTS 名称补齐。
            display_name = legacy.get("name") or raw_pool.get("gachaPoolName", "")
            if (
                self._is_placeholder_name(display_name)
                and match
                and match.get("name")
                and not self._is_placeholder_name(match.get("name"))
            ):
                display_name = match["name"]
            unpublished = self._is_placeholder_name(display_name)
            if unpublished:
                display_name = "未知卡池"
            result.append({
                "id": pool_id or "",
                "name": display_name,
                "type": pool_type,
                "start": pool_start,
                "end": pool_end,
                "six": six,
                "weighted": weighted,
                "image": match.get("image", "") if match else "",
                "unpublished": unpublished,
            })
        return sorted(result, key=lambda item: (item["start"], item["end"]))

    @staticmethod
    def _valid_pool_info(data: object) -> bool:
        if not isinstance(data, dict) or not isinstance(data.get("pool"), dict) or not data["pool"]:
            return False
        pools = list(data["pool"].values())
        valid = sum(
            1 for item in pools
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and bool(item.get("id"))
            and isinstance(item.get("start"), (int, float))
            and isinstance(item.get("end"), (int, float))
            and item["end"] >= item["start"]
        )
        return valid > 0 and valid / len(pools) >= 0.8

    @staticmethod
    def _valid_server_data(data: object) -> bool:
        if not isinstance(data, dict):
            return False
        clients = data.get("gachaPoolClient")
        return isinstance(clients, list) and bool(clients) and any(
            isinstance(item, dict) and item.get("gachaPoolId") for item in clients
        )

    @staticmethod
    def _valid_torappu_data(data: object) -> bool:
        if not isinstance(data, dict):
            return False
        clients = data.get("gachaPoolClient")
        return isinstance(clients, list) and bool(clients) and any(
            isinstance(item, dict)
            and item.get("gachaPoolId")
            and item.get("openTime") is not None
            and item.get("endTime") is not None
            for item in clients
        )

    @staticmethod
    def _valid_character_table(data: object) -> bool:
        if not isinstance(data, dict) or len(data) < 100:
            return False
        valid = sum(
            1 for item in data.values()
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
        )
        return valid >= 100 and valid / len(data) >= 0.8

    @staticmethod
    def _event_key(label: str, error: Exception) -> str:
        reason = "data_invalid" if isinstance(error, ValueError) else type(error).__name__.lower()
        stable_label = re.sub(r"[^0-9a-zA-Z一-龥]+", "_", label).strip("_")
        return f"source:{stable_label}:{reason}"

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
    def _is_placeholder_name(name: object) -> bool:
        return (
            not isinstance(name, str)
            or not name.strip()
            or name == "适合多种场合的强力干员"
            or name.strip().isdigit()
        )

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
    def label(pool_type: str, pool_name: str = "", unpublished: bool = False) -> str:
        if isinstance(pool_name, str) and "联合行动" in pool_name:
            return "联合行动"
        label = {
            "LIMITED": "限定寻访", "LINKAGE": "联动寻访", "SINGLE": "单人寻访",
            "DOUBLE": "标准寻访", "CLASSIC_DOUBLE": "中坚寻访",
            "CLASSIC": "中坚寻访", "FESCLASSIC": "中坚甄选", "BACKFLOW": "归航寻访",
            "SPECIAL": "特殊寻访", "ATTAIN": "定向寻访",
        }.get(pool_type, f"未知卡池（{pool_type or '未标注类型'}）")
        if not unpublished:
            return label
        if pool_type == "NORMAL" or label.startswith("未知卡池"):
            return "未公布"
        if label.endswith("寻访"):
            label = label[:-2] + "卡池"
        elif not label.endswith("卡池"):
            label += "卡池"
        return f"未公布{label}"
