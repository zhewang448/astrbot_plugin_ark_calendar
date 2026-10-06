from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

from .cache import JsonCache
from .models import TimelineItem, parse_iso

CN_TZ = ZoneInfo("Asia/Shanghai")

T = TypeVar("T")


def match_by_name(items: list[T], name: str, key: Callable[[T], str]) -> list[T]:
    """按名称查找：有精确匹配（忽略大小写）只返回精确项，否则返回双向包含的模糊项。

    订阅、取消订阅的指令与 AI 工具共用这一规则，避免同一输入在不同入口命中不同结果。
    """
    needle = name.strip().casefold()
    if not needle:
        return []
    exact = [item for item in items if key(item).casefold() == needle]
    if exact:
        return exact
    return [item for item in items if needle in key(item).casefold() or key(item).casefold() in needle]


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
        """添加订阅，并把本次活动结束时间与实际提醒时间一并固化。

        活动商店晚于活动关闭时，同时添加一条以商店关闭时间为准的派生订阅。
        """
        shop_item = shop_timeline_item(item)
        if shop_item:
            self.add_subscription(shop_item, user_id, session_id, remind_time)
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
        """取消订阅；取消活动时一并取消其活动商店订阅。"""
        subs = self._load_all_subscriptions()
        key = self._subscription_key(item_id, user_id, session_id)
        subs.pop(self._subscription_key(f"{item_id}{SHOP_ITEM_SUFFIX}", user_id, session_id), None)

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
        subs = self._load_all_subscriptions()
        self._drop_expired(subs, datetime.now(CN_TZ))
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
        subs = self._load_all_subscriptions()
        self._drop_expired(subs, current)
        candidates: list[datetime] = []
        for sub in subs.values():
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
        self.mark_notified_many([subscription])

    def mark_notified_many(self, subscriptions: list[Subscription]) -> None:
        """批量标记已通知，一批提醒只重写一次订阅文件。"""
        subs = self._load_all_subscriptions()
        changed = False
        for subscription in subscriptions:
            key = self._subscription_key(
                subscription.item_id,
                subscription.user_id,
                subscription.session_id,
            )
            if key in subs:
                subs[key].notified = True
                subs[key].retry_at = ""
                changed = True
        if changed:
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
        return self._drop_expired(self._load_all_subscriptions(), current)

    def _drop_expired(self, subs: dict[str, Subscription], current: datetime) -> int:
        """从已加载的订阅里删掉过期项并落盘，调用方可继续使用同一份字典。"""
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


SHOP_ITEM_SUFFIX = ":shop"


def shop_timeline_item(item: TimelineItem) -> TimelineItem | None:
    """活动商店晚于活动关闭时，派生一个以商店关闭时间为结束时间的订阅对象。"""
    if item.category != "event" or not item.exchange_end.strip():
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            shop_end = datetime.strptime(item.exchange_end.strip(), fmt).replace(tzinfo=CN_TZ)
            break
        except ValueError:
            continue
    else:
        return None
    try:
        if shop_end <= parse_iso(item.end).astimezone(CN_TZ):
            return None
    except (TypeError, ValueError):
        return None
    return replace(
        item,
        id=f"{item.id}{SHOP_ITEM_SUFFIX}",
        name=f"{item.name}（活动商店）",
        end=shop_end.isoformat(),
    )


def drop_paired_shops(subscriptions: list[Subscription]) -> list[Subscription]:
    """活动与其活动商店同时命中取消订阅时只保留活动；取消活动会一并取消商店。"""
    ids = {sub.item_id for sub in subscriptions}
    return [
        sub for sub in subscriptions
        if not (sub.item_id.endswith(SHOP_ITEM_SUFFIX) and sub.item_id.removesuffix(SHOP_ITEM_SUFFIX) in ids)
    ]


class OperatorWatchManager:
    """干员 UP 蹲池记录：每个卡池对同一条记录只提醒一次。"""

    FILE_NAME = "operator_watches.json"

    def __init__(self, data_dir: Path, logger):
        self.cache = JsonCache(data_dir / "subscriptions")
        self.logger = logger

    def add(self, operator: str, user_id: str, session_id: str) -> None:
        watches = self._load()
        key = f"{operator}:{user_id}:{session_id}"
        watches.setdefault(key, {
            "operator": operator,
            "user_id": user_id,
            "session_id": session_id,
            "notified_pools": [],
        })
        self._save(watches)

    def remove(self, operator: str, user_id: str, session_id: str) -> bool:
        watches = self._load()
        if watches.pop(f"{operator}:{user_id}:{session_id}", None) is None:
            return False
        self._save(watches)
        return True

    def user_operators(self, user_id: str, session_id: str) -> list[str]:
        return sorted(
            watch["operator"] for watch in self._load().values()
            if watch["user_id"] == user_id and watch["session_id"] == session_id
        )

    def pending_hits(self, pools: list[TimelineItem], now: datetime | None = None) -> list[tuple[dict[str, Any], TimelineItem]]:
        """返回尚未提醒过的 (蹲池记录, 命中卡池)；只看未结束且已公布 UP 名单的卡池。"""
        current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
        open_pools: list[TimelineItem] = []
        for pool in pools:
            try:
                if parse_iso(pool.end).astimezone(CN_TZ) > current:
                    open_pools.append(pool)
            except (TypeError, ValueError):
                continue
        hits = []
        for watch in self._load().values():
            for pool in open_pools:
                if pool.id in watch["notified_pools"]:
                    continue
                if watch["operator"] in (*pool.six_star_up, *pool.weighted_up):
                    hits.append((watch, pool))
        return hits

    def mark_notified(self, watch: dict[str, Any], pool_id: str) -> None:
        self.mark_notified_many([(watch, pool_id)])

    def mark_notified_many(self, hits: list[tuple[dict[str, Any], str]]) -> None:
        """批量记录已提醒的 (蹲池记录, 卡池 ID)，只重写一次文件。"""
        watches = self._load()
        changed = False
        for watch, pool_id in hits:
            key = f"{watch['operator']}:{watch['user_id']}:{watch['session_id']}"
            if key in watches and pool_id not in watches[key]["notified_pools"]:
                watches[key]["notified_pools"].append(pool_id)
                changed = True
        if changed:
            self._save(watches)

    def _load(self) -> dict[str, dict[str, Any]]:
        data = self.cache.load(self.FILE_NAME)
        if not isinstance(data, dict):
            return {}
        watches: dict[str, dict[str, Any]] = {}
        for key, item in data.items():
            try:
                watches[key] = {
                    "operator": str(item["operator"]),
                    "user_id": str(item["user_id"]),
                    "session_id": str(item["session_id"]),
                    "notified_pools": [str(pool_id) for pool_id in item.get("notified_pools", [])],
                }
            except (KeyError, TypeError, AttributeError):
                self.logger.warning(f"无法加载蹲池记录：{key}")
        return watches

    def _save(self, watches: dict[str, dict[str, Any]]) -> None:
        self.cache.save(self.FILE_NAME, watches)
