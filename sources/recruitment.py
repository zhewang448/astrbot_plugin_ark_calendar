"""明日方舟公开招募干员数据源。

从游戏数据中提取公招池干员及其标签，支持标签查询和概率计算。
"""

from __future__ import annotations

import re
from typing import Any

from .game_data import GameDataSource
from .http import HttpClient


PROFESSION_TAG: dict[str, str] = {
    "WARRIOR": "近卫干员",
    "SNIPER": "狙击干员",
    "CASTER": "术师干员",
    "MEDIC": "医疗干员",
    # 当前 character_table 使用 TANK、SUPPORT、SPECIAL；保留旧值以兼容历史快照。
    "TANK": "重装干员",
    "DEFENDER": "重装干员",
    "SUPPORT": "辅助干员",
    "SUPPORTER": "辅助干员",
    "SPECIAL": "特种干员",
    "SPECIALIST": "特种干员",
    "PIONEER": "先锋干员",
}
POSITION_TAG: dict[str, str] = {
    "MELEE": "近战位",
    "RANGED": "远程位",
}
RECRUIT_SECTION_RE = re.compile(
    r"^★+\\n(?P<operators>.+?)(?=\r?\n-+|\Z)",
    flags=re.MULTILINE | re.DOTALL,
)


class RecruitmentSource:
    """公开招募数据源。

    复用游戏数据的公开招募名单与干员表，生成可计算的当前公招池。

    `character_table.itemObtainApproach` 只描述角色的获取途径，不能作为当前
    公招池白名单：已下线或从未进入公开招募的角色也可能包含“招募”字样。
    `gacha_table.recruitDetail` 中“全部可能出现的干员”才是客户端当前展示的
    公招名单；同类开源计算器普遍维护的静态名单也以此为准。
    """

    def __init__(self, http: HttpClient, game_data: GameDataSource | None = None):
        # 角色表与 gacha_table 由 GameDataSource 统一获取并只缓存所需字段，
        # 与卡池时间轴共用，避免同一份 8 MB 角色表被两处各自下载、整份常驻内存。
        self.game_data = game_data or GameDataSource(http)

    async def get_recruitment_pool(self) -> dict[str, Any]:
        """获取公招池干员数据。

        Returns:
            {
                "characters": [
                    {
                        "id": "char_xxx",
                        "name": "干员名",
                        "rarity": 6,  # 1-6
                        "tags": ["标签1", "标签2"],
                    },
                    ...
                ],
                "tags": ["全部标签"],
            }
        """
        recruit_names = self._recruit_names(await self.game_data.recruit_detail())
        if not recruit_names:
            return {"characters": [], "tags": []}
        chars_table, _ = await self.game_data.characters(required_names=recruit_names)
        if not chars_table:
            return {"characters": [], "tags": []}

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
            profession = char_data.get("profession", "")
            position = char_data.get("position", "")
            if profession in PROFESSION_TAG:
                tags.append(PROFESSION_TAG[profession])
            if position in POSITION_TAG:
                tags.append(POSITION_TAG[position])

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

    @staticmethod
    def _recruit_names(recruit_detail: Any) -> set[str]:
        """从 gacha_table.recruitDetail 解析客户端展示的公招名单。"""
        if not isinstance(recruit_detail, str):
            return set()

        names: set[str] = set()
        # 分隔符是真实换行；每个星级标题与名单之间则是字面量 ``\\n``。
        # 第一段名单前有说明文字，不能假定星级标题就是分段首行。
        for match in RECRUIT_SECTION_RE.finditer(recruit_detail):
            operators = re.sub(r"<[^>]*>", "", match.group("operators"))
            for raw_name in operators.split("/"):
                name = raw_name.strip()
                if name:
                    names.add(name)
        return names

    def clear_cache(self) -> None:
        """清空缓存：下次计算时重新获取角色表与公招名单。"""
        self.game_data.invalidate()
