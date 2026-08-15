"""平台和会话识别工具函数。"""

from __future__ import annotations

from astrbot.api.platform import MessageType

# 发送侧确认会把 Comp.At 转成平台原生提醒的适配器类型（PlatformMetadata.name）。
# 其余平台要么只降级成纯文本，要么直接忽略 At 组件，因此统一走纯文本前缀。
AT_CAPABLE_PLATFORMS = frozenset({"aiocqhttp", "discord", "kook", "lark", "satori"})
# Context.send_message() 当前不能向 QQ 官方 API 平台主动发消息；命令的被动回复
# 不受此限制，因此平台仍保留在 metadata 的 support_platforms 中。
PROACTIVE_SEND_UNSUPPORTED_PLATFORMS = frozenset({"qq_official"})


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
    """判断当前适配器是否支持 Context.send_message() 主动投递。"""
    parsed = split_sid(session_id)
    if not parsed:
        return True
    try:
        platform = context.get_platform_inst(parsed[0])
    except Exception:
        return True
    if platform is None:
        return True
    return platform.meta().name not in PROACTIVE_SEND_UNSUPPORTED_PLATFORMS
