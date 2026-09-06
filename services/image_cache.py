import asyncio
import re
from datetime import datetime
import aiosqlite
from services.db import DB_PATH
from config import IMAGE_FAIL_BLOCK

# 图片类型闭集：存储值 ↔ 中文标签
TYPE_LABELS = {
    "meme": "表情包",
    "photo": "照片",
    "screenshot": "截图",
    "drawing": "手绘",
    "other": "图片",
}
LABEL_TO_TYPE = {v: k for k, v in TYPE_LABELS.items()}
_TYPE_PREFIX_RE = re.compile(r"^【(.+?)】")

# 内存态等待器：filename → Event（解析完成时 set）
_wait_events: dict[str, asyncio.Event] = {}


def parse_type(label: str) -> str | None:
    """中文标签 → 存储枚举（表情包→meme）；非法返回 None"""
    return LABEL_TO_TYPE.get(label.strip())


def label_of(img_type: str) -> str:
    return TYPE_LABELS.get(img_type, TYPE_LABELS["other"])


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
        # 迁移：旧表补列
        cols = await _column_names(db)
        if "status" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN status TEXT DEFAULT 'pending'"
            )
        if "fail_count" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN fail_count INTEGER DEFAULT 0"
            )
        if "type" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN type TEXT DEFAULT 'other'"
            )
        if "manual" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN manual INTEGER DEFAULT 0"
            )
        if "group_id" not in cols:
            await db.execute(
                "ALTER TABLE image_cache ADD COLUMN group_id TEXT DEFAULT ''"
            )

        await db.commit()
    await _backfill_types()


async def _backfill_types():
    """存量描述里的【类型】前缀 → type 列（一次性迁移，重启时自动跑）"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT filename, description FROM image_cache WHERE type = 'other'"
        ) as cur:
            rows = await cur.fetchall()
        n = 0
        for fn, desc in rows:
            m = _TYPE_PREFIX_RE.match(desc or "")
            if not m:
                continue
            t = LABEL_TO_TYPE.get(m.group(1))
            if not t:
                continue
            new_desc = desc[m.end() :].strip()
            await db.execute(
                "UPDATE image_cache SET type=?, description=? WHERE filename=?",
                (t, new_desc, fn),
            )
            n += 1
        await db.commit()
    if n:
        print(f"[image_cache] 回填 {n} 条历史类型")


async def get_image(filename: str) -> dict | None:
    """查完整状态。created_at 解析为 datetime，失败为 None"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT description, status, fail_count, created_at, type, manual, group_id FROM image_cache WHERE filename = ?",
            (filename,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    ts = None
    if row[3]:
        raw_ts = str(row[3])
        try:
            ts = datetime.fromisoformat(raw_ts)
        except ValueError:
            try:
                ts = datetime.strptime(raw_ts[:19], "%Y-%m-%d %H:%M:%S")
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
        "type": row[4] or "other",
        "manual": bool(row[5]),
        "group_id": row[6] or "",
    }


async def mark_pending(filename: str, group_id: str = ""):
    """收到图片立刻登记（不等解析）。重解析时清零失败计数、刷新时间戳、记录来源群"""
    now = datetime.now().isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO image_cache (filename, description, status, fail_count, created_at, group_id)
            VALUES (?, '', 'pending', 0, ?, ?)
            ON CONFLICT(filename) DO UPDATE
            SET status='pending', fail_count=0, description='', created_at=?, group_id=?
        """,
            (filename, now, group_id, now, group_id),
        )
        await db.commit()
    _wait_events.pop(filename, None)  # 新一轮解析，旧 Event 作废


async def mark_success(filename: str, description: str, img_type: str = "other"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE image_cache SET description=?, status='success', fail_count=0, type=?
            WHERE filename=?
        """,
            (description, img_type if img_type in TYPE_LABELS else "other", filename),
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


async def set_manual(
    filename: str, img_type: str | None = None, description: str | None = None
):
    """主人订正：改类型/描述并锁定（manual=1，自动重解析不再覆盖）"""
    sets, params = ["manual=1"], []
    if img_type:
        sets.append("type=?")
        params.append(img_type)
    if description is not None:
        sets.append("description=?")
        params.append(description[:200])
    params.append(filename)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE image_cache SET {', '.join(sets)} WHERE filename=?", params
        )
        await db.commit()


async def find_by_prefix(prefix: str, limit: int = 10) -> list[dict]:
    """按文件名前缀模糊查（主人订正指令用）"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT rowid AS id, filename, type, description, status, created_at, manual "
            "FROM image_cache WHERE filename LIKE ? ORDER BY created_at DESC LIMIT ?",
            (prefix + "%", limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "type": r[2],
            "description": r[3],
            "status": r[4],
            "created_at": r[5],
            "manual": bool(r[6]),
        }
        for r in rows
    ]


async def subscribe(filename: str) -> asyncio.Event | None:
    """订阅解析完成事件。不存在或已终态(success/blocked)返回 None"""
    info = await get_image(filename)
    if info is None or info["status"] in ("success", "blocked"):
        return None
    ev = _wait_events.get(filename)
    if ev is None:
        ev = asyncio.Event()
        _wait_events[filename] = ev
        info2 = await get_image(filename)
        if info2 and info2["status"] in ("success", "blocked"):
            ev.set()
    return ev


def _notify(filename: str):
    ev = _wait_events.pop(filename, None)
    if ev:
        ev.set()
