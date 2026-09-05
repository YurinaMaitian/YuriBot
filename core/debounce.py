import asyncio
from typing import Callable, Coroutine

_tasks: dict[str, asyncio.Task] = {}

# 防抖窗口：静默这么久没有新消息才真正执行
DEBOUNCE_DELAY = 1.5


def schedule(key: str, factory: Callable[[], Coroutine], delay: float = DEBOUNCE_DELAY):
    """
    按 key 防抖：同 key 的新调用取消旧任务，重新计时。
    factory 是协程工厂（lambda），不是协程本身——取消后重建才干净。
    """
    old = _tasks.get(key)
    if old is not None and not old.done():
        old.cancel()

    async def _run():
        try:
            await asyncio.sleep(delay)
            await factory()
        except asyncio.CancelledError:
            pass

    _tasks[key] = asyncio.create_task(_run())
