"""明日方舟公开招募干员数据源。

从游戏数据中提取公招池干员及其标签，支持标签查询和概率计算。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from .http import HttpClient


class RecruitmentSource:
    """公开招募数据源。

    复用游戏数据的公开招募名单与干员表，生成可计算的当前公招池。

    `character_table.itemObtainApproach` 只描述角色的获取途径，不能作为当前
    公招池白名单：已下线或从未进入公开招募的角色也可能包含“招募”字样。
    `gacha_table.recruitDetail` 中“全部可能出现的干员”才是客户端当前展示的
    公招名单；同类开源计算器普遍维护的静态名单也以此为准。
    """

    CHARACTER_TABLE_URL = "https://torappu.prts.wiki/gamedata/latest/excel/character_table.json"
    GACHA_TABLE_URL = "https://torappu.prts.wiki/gamedata/latest/excel/gacha_table.json"

    def __init__(self, http: HttpClient):
        self.http = http
        self._characters_cache: dict[str, dict[str, Any]] | None = None
        self._gacha_table_cache: dict[str, Any] | None = None
        self._characters_cache_expires_at: datetime | None = None
        self._gacha_table_cache_expires_at: datetime | None = None
        self.cache_ttl = timedelta(hours=24)

    async def get_recruitment_pool(self) -> dict[str, Any]:
        """获取公招池干员数据。

        Returns:
            {
                "characters": [
                    {
                        "id": "char_xxx",
                        "name": "干员名",
                        "rarity": 6,  # 2-7
                        "tags": ["标签1", "标签2"],
                    },
                    ...
                ],
                "tags": ["全部标签"],
            }
        """
        chars_table, gacha_table = await asyncio.gather(
            self._fetch_character_table(),
            self._fetch_gacha_table(),
        )
        recruit_names = self._recruit_names(gacha_table.get("recruitDetail", ""))
        if not chars_table or not recruit_names:
            return {"characters": [], "tags": set()}

        recruitment_chars = []
        all_tags = set()

        for char_id, char_data in chars_table.items():
            name = str(char_data.get("name", "") or "")
            if name not in recruit_names:
                continue

            # 解析稀有度（TIER_1 = 1星，TIER_6 = 6星，直接取数字）
            rarity_str = char_data.get("rarity", "")
            if not rarity_str.startswith("TIER_"):
                continue
            try:
                rarity = int(rarity_str.split("_")[1])
            except (ValueError, IndexError):
                continue

            # 提取词缀标签
            tags: list[str] = list(char_data.get("tagList") or [])

            # 附加职业标签（游戏数据里 tagList 只有词缀，职业/位置需从 profession/position 推算）
            _PROFESSION_TAG: dict[str, str] = {
                "WARRIOR": "近卫干员", "SNIPER": "狙击干员", "CASTER": "术师干员",
                "MEDIC": "医疗干员", "DEFENDER": "重装干员", "SUPPORTER": "辅助干员",
                "SPECIALIST": "特种干员", "PIONEER": "先锋干员",
            }
            _POSITION_TAG: dict[str, str] = {
                "MELEE": "近战位", "RANGED": "远程位",
            }
            profession = char_data.get("profession", "")
            position = char_data.get("position", "")
            if profession in _PROFESSION_TAG:
                tags.append(_PROFESSION_TAG[profession])
            if position in _POSITION_TAG:
                tags.append(_POSITION_TAG[position])

            all_tags.update(tags)

            recruitment_chars.append({
                "id": char_id,
                "name": name,
                "rarity": rarity,
                "tags": tags,
            })

        return {
            "characters": recruitment_chars,
            "tags": sorted(all_tags),
        }

    async def _fetch_character_table(self) -> dict[str, Any]:
        """获取角色表，带缓存。"""
        if self._characters_cache is not None and self._characters_cache_valid():
            return self._characters_cache

        try:
            data = await self.http.json(self.CHARACTER_TABLE_URL)
            if not isinstance(data, dict):
                return {}
            self._characters_cache = data
            self._characters_cache_expires_at = datetime.now() + self.cache_ttl
            return data
        except Exception:
            return {}

    async def _fetch_gacha_table(self) -> dict[str, Any]:
        """获取含当前公招白名单的抽卡表，带内存缓存。"""
        if self._gacha_table_cache is not None and self._gacha_table_cache_valid():
            return self._gacha_table_cache

        try:
            data = await self.http.json(self.GACHA_TABLE_URL)
            if not isinstance(data, dict):
                return {}
            self._gacha_table_cache = data
            self._gacha_table_cache_expires_at = datetime.now() + self.cache_ttl
            return data
        except Exception:
            return {}

    @staticmethod
    def _recruit_names(recruit_detail: Any) -> set[str]:
        """从 gacha_table.recruitDetail 解析客户端展示的公招名单。"""
        if not isinstance(recruit_detail, str):
            return set()

        names: set[str] = set()
        # 分隔符是真实换行；每个星级标题与名单之间则是字面量 ``\\n``。
        # 第一段名单前有说明文字，不能假定星级标题就是分段首行。
        pattern = r"^★+\\n(?P<operators>.+?)(?=\r?\n-+|\Z)"
        for match in re.finditer(re.compile(pattern, flags=re.MULTILINE | re.DOTALL), recruit_detail):
            operators = re.sub(r"<[^>]*>", "", match.group("operators"))
            for raw_name in operators.split("/"):
                name = raw_name.strip()
                if name:
                    names.add(name)
        return names

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._characters_cache = None
        self._gacha_table_cache = None
        self._characters_cache_expires_at = None
        self._gacha_table_cache_expires_at = None

    def _characters_cache_valid(self) -> bool:
        return (
            self._characters_cache_expires_at is not None
            and datetime.now() < self._characters_cache_expires_at
        )

    def _gacha_table_cache_valid(self) -> bool:
        return (
            self._gacha_table_cache_expires_at is not None
            and datetime.now() < self._gacha_table_cache_expires_at
        )
