"""平台和会话识别工具函数。"""

from __future__ import annotations

from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot.api.platform import MessageType

# 发送侧确认会把 Comp.At 转成平台原生提醒的适配器类型（PlatformMetadata.name）。
# 其余平台要么只降级成纯文本，要么直接忽略 At 组件，因此统一走纯文本前缀。
AT_CAPABLE_PLATFORMS = frozenset({"aiocqhttp", "discord", "kook", "lark", "satori"})


def split_sid(session_id: str) -> tuple[str, str] | None:
    """把完整 SID 拆成 (platform_id, message_type)。

    SID 形如 `platform_id:message_type:session_id`，与 AstrBot 的
    `MessageSession.from_str()` 保持一致的 `split(":", 2)` 语义；
    段数不足说明不是完整 SID，返回 None 由调用方按未知处理。
    """
    parts = session_id.split(":", 2)
    if len(parts) < 3:
        return None
    return parts[0], parts[1]


def is_group_session(session_id: str) -> bool:
    """判断是否为群聊会话；无法解析出完整 SID 时按非群聊处理。"""
    parsed = split_sid(session_id)
    if not parsed:
        return False
    return parsed[1] == MessageType.GROUP_MESSAGE.value


def platform_supports_at(session_id: str, context) -> bool:
    """判断该会话所在平台能否把 Comp.At 转成原生提醒。

    SID 首段是平台实例 id（用户可改名），不是适配器类型，因此要经
    `get_platform_inst()` 取 `meta().name` 才能与白名单比对。取不到实例
    （平台未启用等）时按不支持处理，退回纯文本。
    """
    parsed = split_sid(session_id)
    if not parsed:
        return False
    try:
        platform = context.get_platform_inst(parsed[0])
    except Exception:
        # 解析失败，退回纯文本
        return False
    if platform is None:
        return False
    return platform.meta().name in AT_CAPABLE_PLATFORMS


def platform_supports_proactive_send(session_id: str, context) -> bool:
    """根据适配器 metadata 判断是否支持主动投递。

    新版 AstrBot 由平台 metadata 暴露能力；旧版没有该字段时保留已知
    QQ 官方 API 的兼容判断。无法解析会话或平台实例时失败关闭。
    """
    parsed = split_sid(session_id)
    if not parsed:
        return False
    try:
        platform = context.get_platform_inst(parsed[0])
    except Exception:
        return False
    if platform is None:
        return False
    try:
        metadata = platform.meta()
        capability = getattr(metadata, "support_proactive_message", None)
        if capability is not None:
            return bool(capability)
        return getattr(metadata, "name", "") != "qq_official"
    except Exception:
        return False


async def send_proactive(context, session_id: str, components: list[Any], logger, label: str) -> bool:
    """主动投递一条消息链；平台不支持、发送异常或平台拒收时记日志并返回 False。

    `send_message()` 返回 False 表示 SID 对应的平台适配器没有接收，与异常一样视为失败，
    由调用方决定重试或计入失败列表。
    """
    if not platform_supports_proactive_send(session_id, context):
        logger.warning(f"{label}不支持主动投递至 {session_id}。")
        return False
    try:
        dispatched = await context.send_message(session_id, MessageChain(components))
    except Exception:
        logger.error(f"{label}发送至 {session_id} 失败。", exc_info=True)
        return False
    if dispatched is False:
        logger.warning(f"{label}未投递至 {session_id}：请确认该 SID 对应的平台适配器仍在运行。")
        return False
    return True


def mention_parts(session_id: str, user_id: str, context) -> tuple[list[Any], str]:
    """返回 (前置 At 组件, 正文前缀)。

    白名单平台由 At 组件负责提醒，正文不再拼 @；其他群聊退化为纯文本 @，私聊不提醒。
    """
    if platform_supports_at(session_id, context):
        return [Comp.At(qq=user_id, name=user_id)], ""
    return [], f"@{user_id} " if is_group_session(session_id) else ""
