"""管理员通知与健康状态监控。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain

from .platform_utils import platform_supports_proactive_send

CN_TZ = ZoneInfo("Asia/Shanghai")


class NotificationManager:
    """管理员通知与健康监控。

    职责：
    - 向管理员 SID 发送告警和恢复通知
    - 追踪异常状态，避免同一问题在冷却期内重复刷屏
    - 判断数据源状态，提取需要告警的异常事件
    """

    def __init__(self, config, cache, context, logger) -> None:
        self.config = config
        self.cache = cache
        self.context = context
        self.logger = logger
        self._notification_state_lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return bool(self._value("enabled", False)) and bool(self._admin_sids())

    def _admin_sids(self) -> list[str]:
        from .config import config_strings
        return config_strings(self._value("admin_sid_list", []))

    def _value(self, key: str, default: Any) -> Any:
        from .config import config_value
        return config_value(self.config, "admin_notification", key, default)

    def _int_value(self, key: str, default: int, *, minimum: int, maximum: int) -> int:
        from .config import config_int
        return config_int(self.config, "admin_notification", key, default, minimum=minimum, maximum=maximum)

    async def notify(self, text: str, event: str) -> None:
        """向管理员发送文本通知。"""
        if not self.is_enabled():
            return
        self.logger.warning(f"方舟日历管理员通知开始发送：{event}")
        succeeded, failed = await self._send_text(text)
        if succeeded:
            self.logger.info(f"方舟日历管理员通知已送达：{event}（{len(succeeded)} 个 SID）。")
        if failed:
            self.logger.warning(
                f"方舟日历管理员通知未送达：{event}（{len(failed)} 个 SID）。"
                "请确认 admin_sid_list 使用对应会话 /sid 返回的完整 SID，且该消息平台已连接。"
            )

    async def observe_health(self, outcome, origin: str) -> None:
        """检查刷新结果，有新异常时发告警，恢复时发通知。"""
        if not self.is_enabled():
            return
        async with self._notification_state_lock:
            current = self._abnormal_states(outcome)
            stored = self.cache.load("notification_state.json")
            stored = stored if isinstance(stored, dict) else {}
            active = stored.get("active", {}) if isinstance(stored.get("active", {}), dict) else {}
            last_sent = stored.get("last_sent", {}) if isinstance(stored.get("last_sent", {}), dict) else {}
            now = datetime.now(CN_TZ)
            cooldown = self._int_value("cooldown_minutes", 60, minimum=1, maximum=10080)
            alert_keys: list[str] = []
            alert_lines: list[str] = []
            for event_key, item in current.items():
                sent_at = _parse_time(str(last_sent.get(event_key, "")))
                expired = sent_at is None or (now - sent_at).total_seconds() >= cooldown * 60
                if event_key not in active or expired:
                    alert_keys.append(event_key)
                    alert_lines.append(f"- {item['name']}：{item['message']}")
            recovered_keys = [k for k in active if k not in current]
            if alert_lines:
                succeeded, _ = await self._send_text(
                    "【方舟日历异常告警】\n"
                    f"触发来源：{origin}\n"
                    f"时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
                    + "\n".join(alert_lines)
                )
                if succeeded:
                    for event_key in alert_keys:
                        last_sent[event_key] = now.isoformat()
            if recovered_keys and bool(self._value("notify_on_recovery", True)):
                recovered_names = []
                for event_key in recovered_keys:
                    previous = active.get(event_key, {})
                    recovered_names.append(
                        str(previous.get("name", event_key)) if isinstance(previous, dict) else str(event_key)
                    )
                await self._send_text(
                    "【方舟日历恢复通知】\n"
                    f"触发来源：{origin}\n"
                    f"已恢复：{', '.join(recovered_names)}"
                )
            self.cache.save("notification_state.json", {"active": current, "last_sent": last_sent})

    def _abnormal_states(self, outcome) -> dict[str, dict[str, str]]:
        """只依据本次刷新结果判定异常，避免把并发任务的故障算到自己头上。"""
        abnormal: dict[str, dict[str, str]] = {}
        for state in outcome.source_states:
            if state.ok:
                continue
            event_key = state.event_key or f"source:{state.name}:unavailable"
            abnormal[event_key] = {
                "name": state.name,
                "message": state.message or "数据源状态异常",
            }
        if outcome.error and outcome.quality in {"failed", "fallback"}:
            abnormal["calendar_refresh:failed"] = {
                "name": "方舟日历刷新",
                "message": outcome.error,
            }
        return abnormal

    async def notify_refresh_status(
        self, snapshot, refreshed: bool, sent: int, total: int, image_state: str, outcome=None
    ) -> None:
        """定时刷新后发送状态摘要（仅当 refresh_status_mode == "all" 时）。"""
        if not refreshed or not self.is_enabled():
            return
        mode = str(self._value("refresh_status_mode", "abnormal_only") or "abnormal_only")
        if mode != "all":
            return
        from .status_formatter import format_status
        # cache_status 仅用于格式化，这里传空字典，format_status 会显示"暂无"
        status_text = format_status(snapshot, outcome or snapshot, {})
        await self._send_text(
            "【方舟日历刷新状态】\n"
            f"刷新时间：{snapshot.generated_at}\n"
            f"发送结果：{sent}/{total}\n"
            f"图片来源：{image_state_label(image_state)}\n\n"
            f"{status_text}"
        )

    async def _send_text(
        self,
        text: str,
        targets: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        succeeded: list[str] = []
        failed: list[str] = []
        for sid in targets or self._admin_sids():
            if not platform_supports_proactive_send(sid, self.context):
                failed.append(sid)
                self.logger.warning(f"方舟日历管理员 SID {sid} 不支持主动投递。")
                continue
            try:
                delivered = await self.context.send_message(
                    sid, MessageChain([Comp.Plain(text=text)])
                )
                if delivered:
                    succeeded.append(sid)
                else:
                    failed.append(sid)
                    self.logger.warning(
                        f"向方舟日历管理员 SID {sid} 发送通知未被消息平台接收。"
                        "请使用该会话 /sid 返回的完整 SID，并确认对应平台在线。"
                    )
            except Exception:
                failed.append(sid)
                self.logger.error(f"向方舟日历管理员 SID {sid} 发送通知失败。", exc_info=True)
        return succeeded, failed


def image_state_label(state: str) -> str:
    """将图片来源枚举值转为中文标签。"""
    return {"cache": "最终图片缓存", "rendered": "新渲染图片", "fallback": "降级缓存图片"}.get(state, state)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone(CN_TZ)
    except (TypeError, ValueError):
        return None
