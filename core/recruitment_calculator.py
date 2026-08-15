"""明日方舟公开招募计算器。

根据用户选择的标签组合，计算每种组合可能招募到的干员及其保底星级。
"""

from __future__ import annotations

from itertools import combinations
from typing import Any


# 游戏内真实存在的全部公招标签（按类别）
ALL_TAGS = {
    # 职业
    "近卫干员", "狙击干员", "术师干员", "医疗干员",
    "重装干员", "辅助干员", "特种干员", "先锋干员",
    # 位置
    "近战位", "远程位",
    # 特殊稀有标签
    "资深干员",     # 保底 5★
    "高级资深干员",  # 保底 6★
    # 词缀标签（与 character_table.json 中的 tagList 对应）
    "位移", "元素", "减速", "削弱", "召唤",
    "快速复活", "控场", "支援", "支援机械", "新手",
    "治疗", "爆发", "生存", "群攻", "费用回复",
    "输出", "防护", "高空",
}

# 标签别名：用户可能输入的非标准写法 -> 标准标签名
TAG_ALIASES: dict[str, str] = {
    # 职业别名
    "近卫": "近卫干员", "guard": "近卫干员",
    "狙": "狙击干员", "狙击": "狙击干员", "sniper": "狙击干员",
    "术": "术师干员", "术师": "术师干员", "术士": "术师干员", "法师": "术师干员", "caster": "术师干员",
    "医": "医疗干员", "医疗": "医疗干员", "medic": "医疗干员",
    "盾": "重装干员", "重装": "重装干员", "坦克": "重装干员", "defender": "重装干员",
    "拐": "辅助干员", "辅助": "辅助干员", "supporter": "辅助干员",
    "特": "特种干员", "特种": "特种干员", "specialist": "特种干员",
    "回费先锋": "先锋干员", "先锋": "先锋干员", "vanguard": "先锋干员",
    # 位置别名
    "近战": "近战位", "近战位": "近战位", "melee": "近战位",
    "远程": "远程位", "远程位": "远程位", "ranged": "远程位",
    # 稀有标签别名
    "高资": "高级资深干员", "顶资": "高级资深干员", "高级资深": "高级资深干员",
    "top": "高级资深干员", "资深": "资深干员", "资深干员": "资深干员", "senior": "资深干员",
    # 词缀别名
    "小车": "支援机械", "机器人": "支援机械", "机械": "支援机械", "robot": "支援机械",
    "推拉": "位移", "位移": "位移",
    "元素损伤": "元素", "元素": "元素",
    "slow": "减速", "减速": "减速",
    "减防": "削弱", "debuff": "削弱", "削弱": "削弱",
    "召唤物": "召唤", "召唤": "召唤",
    "快活": "快速复活", "快复": "快速复活", "快速复活": "快速复活", "fast复活": "快速复活",
    "控制": "控场", "控场": "控场",
    "支援": "支援", "新手": "新手",
    "奶": "治疗", "治疗": "治疗",
    "aoe": "群攻", "群攻": "群攻",
    "dps": "输出", "输出": "输出",
    "回费": "费用回复", "费用回复": "费用回复",
    "防御": "防护", "防护": "防护",
    "对空": "高空", "高空": "高空",
}

# 职业 -> 游戏数据中的英文 profession 字段（仅用于从 char_table 快速过滤）
PROFESSION_TAG_MAP: dict[str, str] = {
    "近卫干员": "WARRIOR",
    "狙击干员": "SNIPER",
    "术师干员": "CASTER",
    "医疗干员": "MEDIC",
    "重装干员": "DEFENDER",
    "辅助干员": "SUPPORTER",
    "特种干员": "SPECIALIST",
    "先锋干员": "PIONEER",
}

# 位置 -> 游戏数据中的 position 字段
POSITION_TAG_MAP: dict[str, str] = {
    "近战位": "MELEE",
    "远程位": "RANGED",
}


class RecruitmentCalculator:
    """公开招募标签计算器。

    职责：
    - 将游戏数据格式的干员列表转为可计算的招募池
    - 枚举 1-3 个标签的全部组合，计算每种组合的可招募干员和保底星级
    - 规范化用户输入的标签（容错、别名替换）
    """

    def __init__(self, characters: list[dict[str, Any]]) -> None:
        """初始化计算器。

        Args:
            characters: 公招池干员列表，每条包含 id/name/rarity/tags
        """
        # 1★ 支援机械属于公开招募池，不能在建池时过滤掉。其招募时长规则由
        # 上层交互在支持时长参数后处理；这里保留标签组合的候选结果。
        self._pool = [c for c in characters if c["rarity"] >= 1]

        # 预计算每个干员所有有效的"检索标签"（职业 + 位置 + 词缀）
        # 游戏数据中 tagList 只有词缀，职业和位置需要从 profession/position 推算。
        # 但我们接收的已经是预处理后的 tags，包含了职业标签，所以直接用即可。
        self._pool_with_effective_tags = self._attach_effective_tags(self._pool)

    def normalize_tag(self, raw: str) -> str | None:
        """将用户输入的标签规范化为游戏内标准名称。

        Returns:
            标准标签名；无法识别时返回 None
        """
        raw = raw.strip()
        if raw in ALL_TAGS:
            return raw
        if raw in TAG_ALIASES:
            return TAG_ALIASES[raw]
        folded = raw.casefold()
        if folded in TAG_ALIASES:
            return TAG_ALIASES[folded]
        # 模糊匹配：原始标签是某个标准标签的子串（如"减速"匹配"减速"，"高资"不匹配）
        for tag in ALL_TAGS:
            if raw in tag or tag in raw:
                return tag
        return None

    def normalize_tags(self, raw_tags: list[str]) -> tuple[list[str], list[str]]:
        """批量规范化标签列表。

        Returns:
            (有效标签列表, 无法识别的标签列表)
        """
        valid: list[str] = []
        invalid: list[str] = []
        seen: set[str] = set()
        for raw in raw_tags:
            normalized = self.normalize_tag(raw)
            if normalized is None:
                invalid.append(raw)
            elif normalized not in seen:
                valid.append(normalized)
                seen.add(normalized)
        return valid, invalid

    def calculate(self, selected_tags: list[str]) -> list[dict[str, Any]]:
        """计算所有 1-3 个标签组合的招募结果，按推荐度排序。

        Args:
            selected_tags: 已规范化的标签列表（通常 3-5 个）

        Returns:
            排序后的结果列表，每条包含：
            - tags: 本组合使用的标签列表
            - operators: 该组合下可能出现的干员列表（含 name/rarity）
            - min_rarity: 保底星级
            - has_senior: 是否含"资深干员"标签（保底 5★）
            - has_top_senior: 是否含"高级资深干员"标签（保底 6★）
        """
        results: list[dict[str, Any]] = []
        seen_combos: set[tuple[str, ...]] = set()

        for size in (1, 2, 3):
            for combo in combinations(selected_tags, size):
                key = tuple(sorted(combo))
                if key in seen_combos:
                    continue
                seen_combos.add(key)

                operators = self._match_operators(list(combo))
                if not operators:
                    continue

                has_top_senior = "高级资深干员" in combo
                has_senior = "资深干员" in combo

                # 计算保底星级：
                # - 有"高级资深干员"且时限9小时 -> 必得 6★
                # - 有"资深干员"且无"高级资深干员"且时限9小时 -> 必得 5★
                # - 常规：当前组合中所有可能干员的最低稀有度
                min_rarity = min(op["rarity"] for op in operators)
                if has_top_senior:
                    # 高级资深干员仅出现 6★。
                    operators = [op for op in operators if op["rarity"] == 6]
                    min_rarity = 6 if operators else min_rarity
                elif has_senior:
                    # 资深干员仅出现 5★；6★只能通过高级资深干员出现。
                    operators = [op for op in operators if op["rarity"] == 5]
                    min_rarity = min(op["rarity"] for op in operators) if operators else min_rarity

                results.append({
                    "tags": list(combo),
                    "operators": sorted(operators, key=lambda x: -x["rarity"]),
                    "min_rarity": min_rarity,
                    "has_senior": has_senior,
                    "has_top_senior": has_top_senior,
                })

        # 排序：高保底优先；同保底时词条越多越优先；词条数相同时，命中干员越少越优先。
        results.sort(key=lambda r: (-r["min_rarity"], -len(r["tags"]), len(r["operators"]), tuple(r["tags"])))
        return results

    def _match_operators(self, tags: list[str]) -> list[dict[str, Any]]:
        """找出同时含有所有指定标签的干员。

        "资深干员"和"高级资深干员"不是干员自身的词缀标签，而是稀有度门槛标签，
        需要单独处理。
        """
        # 把特殊稀有标签从匹配列表中分离
        normal_tags = [t for t in tags if t not in ("资深干员", "高级资深干员")]
        has_top_senior = "高级资深干员" in tags
        has_senior = "资深干员" in tags

        operators = []
        for char in self._pool_with_effective_tags:
            effective = char["effective_tags"]
            rarity = char["rarity"]

            # 稀有度门槛过滤
            if has_top_senior and rarity != 6:
                continue
            if has_senior and not has_top_senior and rarity != 5:
                continue
            if not has_top_senior and rarity >= 6:
                continue

            # 普通标签全部命中
            if normal_tags and not all(t in effective for t in normal_tags):
                continue

            operators.append({"name": char["name"], "rarity": char["rarity"]})

        return operators

    @staticmethod
    def _attach_effective_tags(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """为干员列表附加 effective_tags（可参与公招匹配的全部标签）。

        effective_tags = tagList 中的词缀，已经包含职业/位置等标签（由数据源预处理）。
        """
        result = []
        for char in pool:
            effective = set(char.get("tags", []))
            result.append({**char, "effective_tags": effective})
        return result


def format_result(
    results: list[dict[str, Any]],
    *,
    selected_tags: list[str],
    max_operators_per_combo: int | None = None,
) -> str:
    """将计算结果格式化为可读文本。

    Args:
        results: calculate() 的返回值
        selected_tags: 用户选择的原始标签（用于展示）
        max_operators_per_combo: 兼容旧调用方的可选限制；默认 None 表示完整输出

    Returns:
        格式化后的文本
    """
    if not results:
        return (
            f"输入标签：{' / '.join(selected_tags)}\n\n"
            "未找到任何有效的标签组合，请检查标签是否正确。\n"
            "可用标签示例：近卫干员、医疗干员、近战位、输出、治疗、资深干员"
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏷️  输入标签：{' / '.join(selected_tags)}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for i, result in enumerate(results):
        combo_tags = " + ".join(result["tags"])
        min_rarity = result["min_rarity"]
        operators = result["operators"]

        # 星级标识
        stars = "★" * min_rarity
        if result["has_top_senior"]:
            guarantee = f"【{stars}】保底"
        elif result["has_senior"]:
            guarantee = f"【{stars}+】保底"
        elif min_rarity >= 5:
            guarantee = f"【{stars}】保底"
        elif min_rarity == 4:
            guarantee = f"【{stars}】最低"
        else:
            guarantee = f"【{stars}】最低"

        lines.append(f"{'🔥 ' if min_rarity >= 5 else ''}▶ {combo_tags}  {guarantee}")

        # 按星级分组列出干员
        by_rarity: dict[int, list[str]] = {}
        shown_operators = operators if max_operators_per_combo is None else operators[:max_operators_per_combo]
        for op in shown_operators:
            by_rarity.setdefault(op["rarity"], []).append(op["name"])

        for rarity in sorted(by_rarity.keys(), reverse=True):
            names_str = "、".join(by_rarity[rarity])
            rarity_stars = "★" * rarity
            lines.append(f"   {rarity_stars}：{names_str}")

        total = len(operators)
        if max_operators_per_combo is not None and total > max_operators_per_combo:
            lines.append(f"   …共 {total} 位干员")

        if i < len(results) - 1:
            lines.append("")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 使用 /方舟公招 查看帮助")

    return "\n".join(lines)
