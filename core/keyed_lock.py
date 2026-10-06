"""按键串行化的异步锁，供下载、缩放、子集化、渲染等"同一资源只做一次"的场景复用。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class KeyedLocks:
    """每个键一把锁，按引用计数回收。

    不能用 ``lock.locked()`` 判断能否回收：持有者释放后、等待者真正拿到锁前，
    锁会短暂处于未锁定状态，此时回收会让后来者拿到另一把新锁，失去串行保证。
    取锁与计数之间没有 await，单线程事件循环下无需额外的守护锁。
    """

    def __init__(self) -> None:
        self._locks: dict[Hashable, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        lock, references = self._locks.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._locks[key] = (lock, references + 1)
        try:
            async with lock:
                yield
        finally:
            _, references = self._locks[key]
            if references <= 1:
                self._locks.pop(key, None)
            else:
                self._locks[key] = (lock, references - 1)

    def __len__(self) -> int:
        return len(self._locks)
