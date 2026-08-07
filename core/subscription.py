from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .cache import JsonCache
from .models import CalendarSnapshot, TimelineItem, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class Subscription:
    """单个订阅记录"""
    item_id: str  # 活动/卡池的 ID
    item_name: str  # 活动/卡池名称
    item_type: str  # "event" 或 "gacha"
    end_time: str  # 结束时间 ISO 格式
    user_id: str  # 订阅用户的 ID（QQ号、用户ID等）
    session_id: str  # 会话 ID（SID）
    remind_time: str = "12:00"  # 提醒时间，默认中午12点
    subscribed_at: str = ""  # 订阅时间
    notified: bool = False  # 是否已通知

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscription:
        return cls(
            item_id=str(data["item_id"]),
            item_name=str(data["item_name"]),
            item_type=str(data["item_type"]),
            end_time=str(data["end_time"]),
            user_id=str(data["user_id"]),
            session_id=str(data["session_id"]),
            remind_time=str(data.get("remind_time", "12:00")),
            subscribed_at=str(data.get("subscribed_at", "")),
            notified=bool(data.get("notified", False)),
        )


class SubscriptionManager:
    """订阅管理器"""

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
        """添加订阅"""
        now = datetime.now(CN_TZ)
        sub = Subscription(
            item_id=item.id,
            item_name=item.name,
            item_type=item.category,
            end_time=item.end,
            user_id=user_id,
            session_id=session_id,
            remind_time=remind_time,
            subscribed_at=now.isoformat(),
            notified=False,
        )

        # 加载现有订阅
        subs = self._load_all_subscriptions()

        # 检查是否已订阅
        key = self._subscription_key(item.id, user_id, session_id)
        if key in subs:
            self.logger.info(f"用户 {user_id} 已订阅 {item.name}，更新提醒时间为 {remind_time}")
            subs[key].remind_time = remind_time
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
        """获取用户的所有订阅"""
        subs = self._load_all_subscriptions()
        result = []
        for sub in subs.values():
            if sub.user_id == user_id:
                if session_id is None or sub.session_id == session_id:
                    result.append(sub)
        return sorted(result, key=lambda s: s.end_time)

    def get_pending_reminders(self, snapshot: CalendarSnapshot) -> list[tuple[Subscription, TimelineItem]]:
        """获取需要提醒的订阅列表（结束前一天且未通知）"""
        now = datetime.now(CN_TZ)
        subs = self._load_all_subscriptions()
        pending: list[tuple[Subscription, TimelineItem]] = []

        # 构建当前活动/卡池索引
        items_map: dict[str, TimelineItem] = {}
        for item in snapshot.events + snapshot.gacha_pools:
            items_map[item.id] = item

        for sub in subs.values():
            if sub.notified:
                continue

            # 检查对应的活动/卡池是否还存在
            item = items_map.get(sub.item_id)
            if not item:
                continue

            try:
                end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
            except (TypeError, ValueError):
                self.logger.warning(f"订阅 {sub.item_id} 的结束时间格式错误：{sub.end_time}")
                continue

            # 计算提醒时间：结束前一天的指定时间
            remind_hour, remind_minute = self._parse_time(sub.remind_time)
            remind_datetime = (end_time - timedelta(days=1)).replace(
                hour=remind_hour,
                minute=remind_minute,
                second=0,
                microsecond=0,
            )

            # 检查是否到了提醒时间
            if now >= remind_datetime and now < end_time:
                pending.append((sub, item))

        return pending

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
            self._save_all_subscriptions(subs)

    def cleanup_expired(self, snapshot: CalendarSnapshot) -> int:
        """清理已过期的订阅"""
        now = datetime.now(CN_TZ)
        subs = self._load_all_subscriptions()

        # 构建当前活动/卡池索引
        active_ids = {item.id for item in snapshot.events + snapshot.gacha_pools}

        expired_keys = []
        for key, sub in subs.items():
            # 如果活动/卡池不再存在，或已过期
            if sub.item_id not in active_ids:
                try:
                    end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
                    if now > end_time:
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
        for key, item in data.items():
            if isinstance(item, dict):
                try:
                    subs[key] = Subscription.from_dict(item)
                except Exception:
                    self.logger.warning(f"无法加载订阅记录：{key}", exc_info=True)
        return subs

    def _save_all_subscriptions(self, subs: dict[str, Subscription]) -> None:
        """保存所有订阅"""
        data = {key: sub.to_dict() for key, sub in subs.items()}
        self.cache.save("subscriptions.json", data)

    @staticmethod
    def _subscription_key(item_id: str, user_id: str, session_id: str) -> str:
        """生成订阅的唯一键"""
        return f"{item_id}:{user_id}:{session_id}"

    @staticmethod
    def _parse_time(time_str: str) -> tuple[int, int]:
        """解析时间字符串，返回 (小时, 分钟)"""
        try:
            parts = time_str.strip().split(":")
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
        except (ValueError, AttributeError):
            pass
        # 默认返回12:00
        return 12, 0
