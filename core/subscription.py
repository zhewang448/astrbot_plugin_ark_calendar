from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .cache import JsonCache
from .models import TimelineItem, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class Subscription:
    """单个订阅记录"""
    item_id: str  # 活动/卡池的 ID
    item_name: str  # 活动/卡池名称
    item_type: str  # "event" 或 "gacha"
    # 产品约定：活动和卡池的结束时间在用户订阅后视为固定，不再从后续快照同步。
    end_time: str  # 订阅时固化的结束时间，ISO 格式
    user_id: str  # 订阅用户的 ID（QQ号、用户ID等）
    session_id: str  # 会话 ID（SID）
    remind_time: str = "12:00"  # 提醒时间，默认中午12点
    remind_at: str = ""  # 订阅时计算并固化的实际提醒时间，ISO 格式
    retry_at: str = ""  # 上一次投递失败后的下一次尝试时间，ISO 格式
    subscribed_at: str = ""  # 订阅时间
    notified: bool = False  # 是否已通知

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscription:
        end_time = str(data["end_time"])
        remind_time = str(data.get("remind_time", "12:00"))
        return cls(
            item_id=str(data["item_id"]),
            item_name=str(data["item_name"]),
            item_type=str(data["item_type"]),
            end_time=end_time,
            user_id=str(data["user_id"]),
            session_id=str(data["session_id"]),
            remind_time=remind_time,
            # 兼容旧记录：首次加载时按其已保存的结束时间补齐固定提醒时间。
            remind_at=str(data.get("remind_at") or _calculate_remind_at(end_time, remind_time)),
            retry_at=str(data.get("retry_at", "")),
            subscribed_at=str(data.get("subscribed_at", "")),
            notified=bool(data.get("notified", False)),
        )


class SubscriptionManager:
    """订阅管理器。活动结束时间在订阅时固化，后续不再读取快照校正。"""

    def __init__(self, data_dir: Path, logger):
        self.cache = JsonCache(data_dir / "subscriptions")
        self.logger = logger

    def add_subscription(
        self,
        item: TimelineItem,
        user_id: str,
        session_id: str,
        remind_time: str = "12:00",
    ) -> Subscription:
        """添加订阅，并把本次活动结束时间与实际提醒时间一并固化。"""
        now = datetime.now(CN_TZ)
        remind_at = _calculate_remind_at(item.end, remind_time)
        sub = Subscription(
            item_id=item.id,
            item_name=item.name,
            item_type=item.category,
            end_time=item.end,
            user_id=user_id,
            session_id=session_id,
            remind_time=remind_time,
            remind_at=remind_at,
            subscribed_at=now.isoformat(),
            notified=False,
        )

        # 加载现有订阅
        subs = self._load_all_subscriptions()

        # 检查是否已订阅
        key = self._subscription_key(item.id, user_id, session_id)
        if key in subs:
            self.logger.info(f"用户 {user_id} 已订阅 {item.name}，更新提醒时间为 {remind_time}")
            # 产品约定：重新订阅是一次新的显式确认，因此用本次快照重新固化时间。
            subs[key].end_time = item.end
            subs[key].remind_time = remind_time
            subs[key].remind_at = remind_at
            subs[key].retry_at = ""
            subs[key].notified = False  # 重置通知状态
        else:
            subs[key] = sub
            self.logger.info(f"用户 {user_id} 订阅了 {item.name}，提醒时间 {remind_time}")

        self._save_all_subscriptions(subs)
        return subs[key]

    def remove_subscription(
        self,
        item_id: str,
        user_id: str,
        session_id: str,
    ) -> bool:
        """取消订阅"""
        subs = self._load_all_subscriptions()
        key = self._subscription_key(item_id, user_id, session_id)

        if key in subs:
            item_name = subs[key].item_name
            del subs[key]
            self._save_all_subscriptions(subs)
            self.logger.info(f"用户 {user_id} 取消订阅了 {item_name}")
            return True
        return False

    def get_user_subscriptions(
        self,
        user_id: str,
        session_id: str | None = None,
    ) -> list[Subscription]:
        """获取用户的所有未过期订阅。"""
        self.cleanup_expired()
        subs = self._load_all_subscriptions()
        result = []
        for sub in subs.values():
            if sub.user_id == user_id:
                if session_id is None or sub.session_id == session_id:
                    result.append(sub)
        return sorted(result, key=lambda s: s.end_time)

    def get_due_reminders(self, now: datetime | None = None) -> list[Subscription]:
        """返回已到投递时间且未通知的订阅，不读取活动快照。"""
        current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
        due: list[Subscription] = []
        for sub in self._load_all_subscriptions().values():
            if sub.notified:
                continue
            try:
                end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
                attempt_at = _effective_attempt_at(sub)
            except (TypeError, ValueError):
                self.logger.warning(f"订阅 {sub.item_id} 的时间格式错误，已跳过。")
                continue
            if attempt_at <= current < end_time:
                due.append(sub)
        return due

    def get_next_reminder_at(self, now: datetime | None = None) -> datetime | None:
        """返回下一次需要投递的时间；已到期记录会在这里被清理。"""
        current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
        self.cleanup_expired(current)
        candidates: list[datetime] = []
        for sub in self._load_all_subscriptions().values():
            if sub.notified:
                continue
            try:
                end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
                attempt_at = _effective_attempt_at(sub)
            except (TypeError, ValueError):
                self.logger.warning(f"订阅 {sub.item_id} 的时间格式错误，已跳过。")
                continue
            if attempt_at < end_time:
                candidates.append(max(attempt_at, current))
        return min(candidates, default=None)

    def mark_notified(self, subscription: Subscription) -> None:
        """标记订阅已通知"""
        subs = self._load_all_subscriptions()
        key = self._subscription_key(
            subscription.item_id,
            subscription.user_id,
            subscription.session_id,
        )
        if key in subs:
            subs[key].notified = True
            subs[key].retry_at = ""
            self._save_all_subscriptions(subs)

    def defer_reminders(self, subscriptions: list[Subscription], retry_at: datetime) -> None:
        """把投递失败的订阅推迟到指定时间重试。"""
        subs = self._load_all_subscriptions()
        for subscription in subscriptions:
            key = self._subscription_key(
                subscription.item_id, subscription.user_id, subscription.session_id
            )
            if key in subs and not subs[key].notified:
                subs[key].retry_at = retry_at.astimezone(CN_TZ).isoformat()
        self._save_all_subscriptions(subs)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """清理已过结束时间的订阅，不读取活动快照。"""
        current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
        subs = self._load_all_subscriptions()
        expired_keys: list[str] = []
        for key, sub in subs.items():
            try:
                end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
                if current >= end_time:
                    expired_keys.append(key)
            except (TypeError, ValueError):
                expired_keys.append(key)

        for key in expired_keys:
            del subs[key]

        if expired_keys:
            self._save_all_subscriptions(subs)
            self.logger.info(f"已清理 {len(expired_keys)} 个过期订阅")

        return len(expired_keys)

    def _load_all_subscriptions(self) -> dict[str, Subscription]:
        """加载所有订阅"""
        data = self.cache.load("subscriptions.json")
        if not isinstance(data, dict):
            return {}

        subs: dict[str, Subscription] = {}
        dropped = 0
        migrated = False
        for key, item in data.items():
            if isinstance(item, dict):
                try:
                    sub = Subscription.from_dict(item)
                except Exception:
                    self.logger.warning(f"无法加载订阅记录：{key}", exc_info=True)
                    continue
                if not self._is_full_sid(sub.session_id):
                    # 0.4.1 及更早版本存的是裸会话号，Context.send_message() 无法解析，
                    # 这类记录永远发不出提醒，且无法反推平台，只能丢弃。
                    dropped += 1
                    continue
                migrated = migrated or not item.get("remind_at")
                subs[key] = sub
        if dropped:
            self.logger.warning(
                f"已丢弃 {dropped} 条旧版订阅记录：其会话标识不是完整 SID，无法投递提醒。"
                "请重新发送 /方舟订阅 建立订阅。"
            )
        if dropped or migrated:
            self._save_all_subscriptions(subs)
        return subs

    @staticmethod
    def _is_full_sid(session_id: str) -> bool:
        """完整 SID 形如 `platform_id:message_type:session_id`，至少三段且各段非空。"""
        parts = session_id.split(":", 2)
        return len(parts) == 3 and all(part.strip() for part in parts)

    def _save_all_subscriptions(self, subs: dict[str, Subscription]) -> None:
        """保存所有订阅"""
        data = {key: sub.to_dict() for key, sub in subs.items()}
        self.cache.save("subscriptions.json", data)

    @staticmethod
    def _subscription_key(item_id: str, user_id: str, session_id: str) -> str:
        """生成订阅的唯一键"""
        return f"{item_id}:{user_id}:{session_id}"


def _calculate_remind_at(end_time: str, remind_time: str) -> str:
    """按订阅时记录的结束时间计算唯一的提醒时刻。"""
    end = parse_iso(end_time).astimezone(CN_TZ)
    try:
        hour_text, minute_text = remind_time.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
    except (AttributeError, ValueError):
        hour, minute = 12, 0
    return (end - timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).isoformat()


def _effective_attempt_at(subscription: Subscription) -> datetime:
    """失败重试优先；没有重试计划时使用固化的提醒时间。"""
    return parse_iso(subscription.retry_at or subscription.remind_at).astimezone(CN_TZ)
