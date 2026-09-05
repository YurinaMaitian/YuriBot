import json
import aiosqlite
from datetime import datetime
from services.db import DB_PATH


async def init_scene_queue_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scene_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                msg_time TEXT NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scene_queue_group ON scene_queue(group_id, id)"
        )
        await db.commit()


async def enqueue(
    group_id: str, speaker: str, user_id: str, content: str, msg_time: datetime = None
):
    ts = (msg_time or datetime.now()).isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scene_queue (group_id, speaker, user_id, content, msg_time) VALUES (?,?,?,?,?)",
            (group_id, speaker, user_id, content, ts),
        )
        await db.commit()


async def get_queue(group_id: str) -> list[dict]:
    """取整队，按入队顺序（队头在前）"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT speaker, user_id, content, msg_time FROM scene_queue WHERE group_id = ? ORDER BY id",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
    out = []
    for speaker, user_id, content, msg_time in rows:
        try:
            ts = datetime.fromisoformat(msg_time)
        except (ValueError, TypeError):
            ts = datetime.now()
        out.append(
            {"speaker": speaker, "user_id": user_id, "content": content, "time": ts}
        )
    return out


async def dequeue(group_id: str, n: int):
    """从队头出队 n 条"""
    if n <= 0:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM scene_queue WHERE id IN "
            "(SELECT id FROM scene_queue WHERE group_id = ? ORDER BY id LIMIT ?)",
            (group_id, n),
        )
        await db.commit()


async def clear_group(group_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scene_queue WHERE group_id = ?", (group_id,))
        await db.commit()


async def all_group_ids() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT group_id FROM scene_queue") as cur:
            return [r[0] for r in await cur.fetchall()]
