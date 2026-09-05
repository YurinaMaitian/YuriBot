import asyncio
from datetime import datetime
import aiosqlite
from services.db import DB_PATH
from config import IMAGE_FAIL_BLOCK

# 内存态等待器：filename → Event（解析完成时 set）
_wait_events: dict[str, asyncio.Event] = {}


async def _column_names(db) -> set:
    async with db.execute("PRAGMA table_info(image_cache)") as cur:
        return {r[1] for r in await cur.fetchall()}


async def init_image_cache_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS image_cache (
                filename TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                fail_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 迁移：旧表补列（线上已有表，不能 DROP）
        cols = await _column_names(db)
        if "status" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN status TEXT DEFAULT 'pending'"
            )
        if "fail_count" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN fail_count INTEGER DEFAULT 0"
            )
        await db.commit()


async def get_image(filename: str) -> dict | None:
    """查完整状态。created_at 解析为 datetime，失败为 None"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT description, status, fail_count, created_at FROM image_cache WHERE filename = ?",
            (filename,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    ts = None
    if row[3]:
        raw_ts = str(row[3])
        try:
            ts = datetime.fromisoformat(raw_ts)  # 新行：本地 ISO
        except ValueError:
            try:
                ts = datetime.strptime(raw_ts[:19], "%Y-%m-%d %H:%M:%S")  # 旧行：UTC
                # 旧行是 UTC，换算到本地，避免老数据继续虚高 8 小时
                from datetime import timezone, timedelta

                ts = (
                    ts.replace(tzinfo=timezone.utc)
                    .astimezone(tz=None)
                    .replace(tzinfo=None)
                )
            except ValueError:
                ts = None
    return {
        "description": row[0],
        "status": row[1],
        "fail_count": row[2],
        "created_at": ts,
    }


async def mark_pending(filename: str):
    """收到图片立刻登记（不等解析）。重解析时清零失败计数、刷新时间戳"""
    now = datetime.now().isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO image_cache (filename, description, status, fail_count, created_at)
            VALUES (?, '', 'pending', 0, ?)
            ON CONFLICT(filename) DO UPDATE
            SET status='pending', fail_count=0, description='', created_at=?
        """,
            (filename, now, now),
        )
        await db.commit()
    _wait_events.pop(filename, None)  # 新一轮解析，旧 Event 作废


async def mark_success(filename: str, description: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE image_cache SET description=?, status='success', fail_count=0
            WHERE filename=?
        """,
            (description, filename),
        )
        await db.commit()
    _notify(filename)


async def mark_failed(filename: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE image_cache
            SET fail_count = fail_count + 1,
                status = CASE WHEN fail_count + 1 >= ? THEN 'blocked' ELSE 'failed' END
            WHERE filename = ?
        """,
            (IMAGE_FAIL_BLOCK, filename),
        )
        await db.commit()
    _notify(filename)


async def subscribe(filename: str) -> asyncio.Event | None:
    """订阅解析完成事件。不存在或已终态(success/blocked)返回 None"""
    info = await get_image(filename)
    if info is None or info["status"] in ("success", "blocked"):
        return None
    ev = _wait_events.get(filename)
    if ev is None:
        ev = asyncio.Event()
        _wait_events[filename] = ev
        # 关掉竞态：建 Event 期间解析可能刚好完成，二次确认
        info2 = await get_image(filename)
        if info2 and info2["status"] in ("success", "blocked"):
            ev.set()
    return ev


def _notify(filename: str):
    ev = _wait_events.pop(filename, None)
    if ev:
        ev.set()
