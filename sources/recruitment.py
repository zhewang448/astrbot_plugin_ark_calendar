"""明日方舟公开招募干员数据源。

从游戏数据中提取公招池干员及其标签，支持标签查询和概率计算。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .http import HttpClient


class RecruitmentSource:
    """公开招募数据源。

    从 torappu.prts.wiki 获取干员表，筛选可公招的干员及其标签和稀有度。
    """

    CHARACTER_TABLE_URL = "https://torappu.prts.wiki/gamedata/latest/excel/character_table.json"

    def __init__(self, http: HttpClient):
        self.http = http
        self._characters_cache: dict[str, dict[str, Any]] | None = None

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
        chars_table = await self._fetch_character_table()
        if not chars_table:
            return {"characters": [], "tags": set()}

        recruitment_chars = []
        all_tags = set()

        for char_id, char_data in chars_table.items():
            # 筛选可公招的干员
            obtain_approach = char_data.get("itemObtainApproach", "") or ""
            if "招募" not in obtain_approach:
                continue

            # 解析稀有度（TIER_1 -> 2星，TIER_6 -> 7星）
            rarity_str = char_data.get("rarity", "")
            if not rarity_str.startswith("TIER_"):
                continue
            try:
                rarity = int(rarity_str.split("_")[1]) + 1
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
                "name": char_data.get("name", ""),
                "rarity": rarity,
                "tags": tags,
            })

        return {
            "characters": recruitment_chars,
            "tags": sorted(all_tags),
        }

    async def _fetch_character_table(self) -> dict[str, Any]:
        """获取角色表，带缓存。"""
        if self._characters_cache is not None:
            return self._characters_cache

        try:
            data = await self.http.json(self.CHARACTER_TABLE_URL)
            self._characters_cache = data if isinstance(data, dict) else {}
            return self._characters_cache
        except Exception:
            self._characters_cache = {}
            return {}

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._characters_cache = None
