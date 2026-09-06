"""
全局 aiohttp.ClientSession：进程内复用连接池，替代各处"用完即弃"。

注意：
- 惰性创建（首次调用才建），不要在模块级直接 new；
- 所有调用方必须在请求级传 timeout（共享 session 没有默认超时）；
- 进程关闭时由 lifespan 调用 close_session() 优雅释放。
"""

import aiohttp

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
