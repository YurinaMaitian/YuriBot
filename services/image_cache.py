import aiosqlite
from services.db import DB_PATH


async def get_cached_desc(filename: str) -> str:
    """查缓存，返回描述或空字符串"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT description FROM image_cache WHERE filename = ?", (filename,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else ""


async def set_cached_desc(filename: str, description: str):
    """写入缓存"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO image_cache (filename, description) VALUES (?, ?)
            ON CONFLICT(filename) DO UPDATE SET description=excluded.description
        """,
            (filename, description),
        )
        await db.commit()


async def init_image_cache_table():
    """初始化表（在 db.py 的 init_db 里调用，或 startup 时调用）"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS image_cache (
                filename TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
