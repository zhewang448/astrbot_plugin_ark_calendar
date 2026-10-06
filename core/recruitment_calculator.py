"""明日方舟公开招募计算器。

根据用户选择的标签组合，计算每种组合可能招募到的干员及其保底星级。
"""

from __future__ import annotations

from itertools import combinations
from typing import Any


RECRUITMENT_EASTER_EGG_MESSAGE = (
    "博士，不要再喊“ALL ALL”了啦！阿米娅已经为您整理好公招计算结果，"
    "请直接查看这份记录吧：\n"
    "https://www.bilibili.com/video/BV1y14y157MD"
)


def is_recruitment_easter_egg_query(text: str) -> bool:
    """判断是否使用公招彩蛋触发词（all 或 *）。"""
    return text.strip().casefold() in {"all", "*"}


# 游戏内一次最多显示 5 个公招标签；超过时组合数会迅速膨胀（29 个标签对应 4089 种组合）。
# 实际上限由配置 basic.recruit_max_tags 决定，0 表示不限制。
DEFAULT_RECRUIT_MAX_TAGS = 5

# 游戏内真实存在的全部公招标签（按类别，顺序即帮助页展示顺序）
TAG_GROUPS: dict[str, tuple[str, ...]] = {
    "职业": ("近卫干员", "狙击干员", "术师干员", "医疗干员", "重装干员", "辅助干员", "特种干员", "先锋干员"),
    "位置": ("近战位", "远程位"),
    # 稀有度栏标签：资深干员保底 5★，高级资深干员保底 6★
    "稀有度": ("新手", "资深干员", "高级资深干员"),
    # 词缀标签（与 character_table.json 中的 tagList 对应）
    "词缀": (
        "输出", "治疗", "生存", "防护", "控场", "爆发", "支援", "减速",
        "削弱", "群攻", "位移", "召唤", "快速复活", "费用回复", "支援机械", "元素",
    ),
}
ALL_TAGS = frozenset(tag for tags in TAG_GROUPS.values() for tag in tags)
RARITY_TAG_NOTES = {"资深干员": "保底5★", "高级资深干员": "保底6★"}


def recruitment_help_groups() -> dict[str, list[str]]:
    """公招帮助图的标签分组；稀有度标签附带保底说明。"""
    return {
        group: [f"{tag}（{RARITY_TAG_NOTES[tag]}）" if tag in RARITY_TAG_NOTES else tag for tag in tags]
        for group, tags in TAG_GROUPS.items()
    }


def recruitment_help_text() -> str:
    """公招帮助图渲染失败时的文字版，标签清单与帮助图同源。"""
    professions = "、".join(tag.removesuffix("干员") for tag in TAG_GROUPS["职业"])
    special = "、".join(f"{tag}（{note}）" for tag, note in RARITY_TAG_NOTES.items())
    affixes = [f"{tag}（小车）" if tag == "支援机械" else tag for tag in TAG_GROUPS["词缀"]]
    affix_lines = ["、".join(affixes[index:index + 8]) for index in range(0, len(affixes), 8)]
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏷️  方舟公招计算器\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "用法：/方舟公招 <标签1> [标签2] [标签3] …\n\n"
        "示例：\n"
        "  /方舟公招 近卫干员 输出 生存\n"
        "  /方舟公招 资深干员 医疗干员\n"
        "  /方舟公招 高级资深干员\n\n"
        "可用职业标签（也可省略「干员」两字）：\n"
        f"  {professions}\n\n"
        f"可用位置标签：{'、'.join(TAG_GROUPS['位置'])}\n\n"
        f"特殊标签：{special}\n\n"
        "词缀标签：" + "、\n          ".join(affix_lines) + "\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


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
    "支援": "支援",
    "奶": "治疗", "治疗": "治疗",
    "aoe": "群攻", "群攻": "群攻",
    "dps": "输出", "输出": "输出",
    "回费": "费用回复", "费用回复": "费用回复",
    "防御": "防护", "防护": "防护",
}

# `/方舟公招` 固定按游戏内 9 小时招募计算。1★、2★不计入保底星级。
NINE_HOUR_MIN_RARITY = 3


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
        # 1★ 支援机械仍需保留在完整公招池中；计算保底时才按 9 小时规则忽略 1★、2★。
        self._pool = list(characters)

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
        # 模糊匹配：先找包含输入的标准标签，再找被输入包含的标准标签；
        # 任一层命中多个（如"近卫干员输出"同时含"近卫干员"与"输出"）视为无法识别，
        # 避免按集合遍历顺序随机选中其中一个。
        if not raw:
            return None
        for matches in (
            [tag for tag in ALL_TAGS if raw in tag],
            [tag for tag in ALL_TAGS if tag in raw],
        ):
            if len(matches) == 1:
                return matches[0]
            if matches:
                return None
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
            - tag_combinations: 候选结果相同的全部标签组合
            - operators: 该组合下可能出现的干员列表（含 name/rarity）
            - min_rarity: 保底星级
            - robot: 是否只命中 1★ 支援机械（时限 3:50 可锁定）
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

                guarantee_operators = [
                    operator
                    for operator in operators
                    if operator["rarity"] >= NINE_HOUR_MIN_RARITY
                ]

                has_top_senior = "高级资深干员" in combo
                has_senior = "资深干员" in combo

                # 计算保底星级：
                # - 有"高级资深干员"且时限9小时 -> 必得 6★
                # - 有"资深干员"且无"高级资深干员"且时限9小时 -> 必得 5★
                # - 常规：忽略 1★、2★后的候选最低稀有度
                has_guarantee = bool(guarantee_operators)
                min_rarity = min((op["rarity"] for op in guarantee_operators), default=0)
                # 9 小时招募不会出 1★、2★；有 3★+ 候选时只列这些，避免低星干员干扰合并和排序。
                # 只命中 1★ 支援机械的组合单独标记：招募时限设为 3:50 可锁定 1★ 支援机械。
                robot = (
                    not has_guarantee
                    and "支援机械" in combo
                    and all(op["rarity"] == 1 for op in operators)
                )
                if has_guarantee:
                    operators = guarantee_operators
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
                    "tag_combinations": [list(combo)],
                    "operators": sorted(operators, key=lambda x: -x["rarity"]),
                    "min_rarity": min_rarity,
                    "has_guarantee": has_guarantee,
                    "robot": robot,
                    "has_senior": has_senior,
                    "has_top_senior": has_top_senior,
                })

        results = self._merge_results(results)
        # 排序：高保底优先；无保底时 1★ 支援机械组合在前；同保底时词条越多越优先；
        # 词条数相同时，命中干员越少越优先。
        results.sort(key=lambda r: (
            -r["min_rarity"], not r["robot"], -len(r["tags"]), len(r["operators"]), tuple(r["tags"]),
        ))
        return results

    @staticmethod
    def _merge_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并候选干员和保底语义完全相同的词条组合。"""
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        for result in results:
            operator_key = tuple(sorted(
                (str(operator.get("name", "")), int(operator.get("rarity", 0) or 0))
                for operator in result.get("operators", [])
            ))
            key = (
                operator_key,
                int(result.get("min_rarity", 0) or 0),
                bool(result.get("has_guarantee", True)),
                bool(result.get("robot")),
                bool(result.get("has_senior")),
                bool(result.get("has_top_senior")),
            )
            current = merged.get(key)
            combinations_for_result = result.get("tag_combinations") or [result.get("tags", [])]
            if current is None:
                current = {**result, "tag_combinations": [list(item) for item in combinations_for_result]}
                merged[key] = current
            else:
                current["tag_combinations"].extend(list(item) for item in combinations_for_result)
        for result in merged.values():
            result["tag_combinations"].sort(key=lambda tags: (-len(tags), tuple(tags)))
            result["tags"] = result["tag_combinations"][0]
        return list(merged.values())

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
) -> str:
    """将计算结果格式化为可读文本。

    Args:
        results: calculate() 的返回值
        selected_tags: 用户选择的原始标签（用于展示）

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
        tag_combinations = result.get("tag_combinations") or [result["tags"]]
        combo_tags = " / ".join(" + ".join(tags) for tags in tag_combinations)
        min_rarity = result["min_rarity"]
        operators = result["operators"]
        has_guarantee = bool(result.get("has_guarantee", True))

        # 星级标识
        stars = "★" * min_rarity
        if result.get("robot"):
            guarantee = "【★】支援机械：时限设为 3:50 可锁定"
        elif not has_guarantee:
            guarantee = "【无3★保底】"
        elif result["has_top_senior"]:
            guarantee = f"【{stars}】保底"
        elif result["has_senior"]:
            guarantee = f"【{stars}+】保底"
        elif min_rarity >= 5:
            guarantee = f"【{stars}】保底"
        else:
            guarantee = f"【{stars}】最低"

        lines.append(f"{'🔥 ' if min_rarity >= 5 else ''}▶ {combo_tags}  {guarantee}")

        # 按星级分组列出干员
        by_rarity: dict[int, list[str]] = {}
        for op in operators:
            by_rarity.setdefault(op["rarity"], []).append(op["name"])

        for rarity in sorted(by_rarity.keys(), reverse=True):
            names_str = "、".join(by_rarity[rarity])
            rarity_stars = "★" * rarity
            lines.append(f"   {rarity_stars}：{names_str}")

        if i < len(results) - 1:
            lines.append("")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 使用 /方舟公招 查看帮助")

    return "\n".join(lines)
