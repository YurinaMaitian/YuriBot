import aiosqlite
import re
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


_MENTION_RE = re.compile(r"<@!?([0-9A-Fa-f]+)>")


async def normalize_mentions(content: str) -> str:
    """
    把 <@openid> 标签翻译成 @昵称（保留"@了谁"的语义，对 LLM 可读）。
    未知的 openid 统一显示为 @某人。
    """
    if "<@" not in content:
        return content

    async def _repl(m: re.Match) -> str:
        oid = m.group(1)
        try:
            nick = await get_nickname(oid)
            # 查无此人时 get_nickname 会返回 openid 前8位，这种显示成"某人"
            if nick == oid[:8]:
                return "@某位群友"
            return f"@{nick}"
        except Exception:
            return "@某位群友"

    # 顺序替换（get_nickname 是异步的）
    out = content
    for m in list(_MENTION_RE.finditer(content)):
        out = out.replace(m.group(0), await _repl(m), 1)
    return out
