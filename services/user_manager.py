import aiosqlite
from services.db import DB_PATH


async def get_nickname(openid: str) -> str:
    """查昵称，不存在则自动创建默认记录"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT nickname FROM users WHERE openid = ?", (openid,)
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            return row[0]
        default = openid[:8]
        await db.execute(
            "INSERT OR IGNORE INTO users (openid, nickname) VALUES (?, ?)",
            (openid, default),
        )
        await db.commit()
        return default


async def set_nickname(openid: str, nickname: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (openid, nickname) VALUES (?, ?)
            ON CONFLICT(openid) DO UPDATE SET nickname=excluded.nickname
        """,
            (openid, nickname),
        )
        await db.commit()


async def get_group_name(openid: str) -> str:
    """查群名，不存在则自动创建默认记录"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM groups WHERE openid = ?", (openid,)
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            return row[0]
        default = openid[:12]
        await db.execute(
            "INSERT OR IGNORE INTO groups (openid, name) VALUES (?, ?)",
            (openid, default),
        )
        await db.commit()
        return default


async def set_group_name(openid: str, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO groups (openid, name) VALUES (?, ?)
            ON CONFLICT(openid) DO UPDATE SET name=excluded.name
        """,
            (openid, name),
        )
        await db.commit()
