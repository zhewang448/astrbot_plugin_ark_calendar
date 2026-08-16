from __future__ import annotations

from typing import Iterable

# 全角冒号/空格来自中文输入法，统一成半角再解析。
_FULL_WIDTH = {"：": ":", "　": " "}
# 触发前缀：不同平台的唤醒符号不一样，取并集剥掉。
_PREFIXES = "/!！#."


def normalize_text(text: str) -> str:
    """把全角冒号与全角空格换成半角，并压掉首尾空白。"""
    for source, target in _FULL_WIDTH.items():
        text = text.replace(source, target)
    return text.strip()


def parse_hhmm(text: str) -> str | None:
    """把 HH:MM 解析成规范化的零填充形式；不合法返回 None。"""
    candidate = normalize_text(text)
    if ":" not in candidate:
        return None
    hour_text, _, minute_text = candidate.partition(":")
    if not hour_text.isdigit() or not minute_text.isdigit():
        return None
    if not 1 <= len(hour_text) <= 2 or len(minute_text) != 2:
        return None
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def strip_command_prefix(message: str, invocations: Iterable[str]) -> str:
    """剥掉唤醒符号与命令名/别名，返回后面剩下的参数原文。

    命令名按长度倒序匹配，避免「方舟订阅」先命中而把「方舟订阅列表」切坏。
    """
    text = normalize_text(message).lstrip(_PREFIXES).lstrip()
    for invocation in sorted(invocations, key=len, reverse=True):
        if not invocation:
            continue
        if text == invocation:
            return ""
        if text.startswith(invocation) and (
            len(text) == len(invocation) or text[len(invocation)].isspace()
        ):
            return text[len(invocation):].strip()
    return text


def split_name_and_time(argument_text: str) -> tuple[str, str | None]:
    """把参数原文拆成（名称, 提醒时间）。

    名称本身可以带空格（例如「危机合约 · 熔火行动」），所以只有当最后一个
    token 是合法 HH:MM 时才把它当提醒时间摘出来，其余全部算名称。
    """
    text = normalize_text(argument_text)
    if not text:
        return "", None
    parts = text.split()
    if len(parts) >= 2:
        remind_time = parse_hhmm(parts[-1])
        if remind_time:
            return " ".join(parts[:-1]), remind_time
    # 只给了一个 token 且它是时间：视为没写名称，交给调用方走帮助分支。
    if len(parts) == 1:
        remind_time = parse_hhmm(parts[0])
        if remind_time:
            return "", remind_time
    return " ".join(parts), None
