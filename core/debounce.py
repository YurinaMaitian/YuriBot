import asyncio
from typing import Callable, Coroutine

_tasks: dict[str, asyncio.Task] = {}
_generations: dict[str, int] = {}

DEBOUNCE_DELAY = 1.5


def schedule(key: str, factory: Callable[[], Coroutine], delay: float = DEBOUNCE_DELAY):
    """
    按 key 防抖（代际机制，不取消在跑任务）：
    - 同 key 新调用会抬高代际，旧任务 sleep 醒来发现代际过期则静默退出
    - 已通过检查、正在执行/发送的任务不受任何影响（杜绝截断）
    """
    gen = _generations.get(key, 0) + 1
    _generations[key] = gen

    async def _run():
        await asyncio.sleep(delay)
        if _generations.get(key) != gen:
            return  # 已有更新的消息接管，本任务过期
        await factory()

    _tasks[key] = asyncio.create_task(_run())
