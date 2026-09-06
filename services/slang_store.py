"""
梗百科：群内黑话/新用法的 RAG 知识库。

教学：私聊 /梗 词 = 解释（重复教学=修改，/梗del 删除，/梗list 列表）
使用：Router slang 模块 → embed 消息 → Qdrant 检索 → 注入主 prompt【梗百科】块
设计原则：公共知识缺口走联网搜索（将来），群内黑话只有人教 RAG 一条路。
"""

import hashlib
import re

import aiosqlite

from services.db import DB_PATH
from services.embedding import embed_text
from services.vector_store import (
    delete_slang_point,
    search_slang_points,
    upsert_slang_point,
)

_RECALL_MIN = 0.6  # 检索命中门槛（先定死，日志攒了再调）


def _point_id(term: str) -> int:
    return int.from_bytes(hashlib.md5(term.encode()).digest()[:8], "big")


async def init_slang_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS slang (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                explanation TEXT NOT NULL,
                group_id TEXT DEFAULT '',
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                UNIQUE(term, group_id)
            )
        """)
        await db.commit()


async def add_slang(term: str, explanation: str, group_id: str = "") -> bool:
    """新增或修改（同词重复教学即覆盖）"""
    term, explanation = term.strip(), explanation.strip()
    if not term or not explanation:
        return False
    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO slang (term, explanation, group_id, created_at, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(term, group_id) DO UPDATE
               SET explanation=excluded.explanation, updated_at=excluded.updated_at""",
            (term, explanation, group_id, now, now),
        )
        await db.commit()
    try:
        vector = await embed_text(f"{term}：{explanation}"[:200])
        await upsert_slang_point(_point_id(term), term, explanation, group_id, vector)
    except Exception as e:
        print(f"[梗百科索引失败] {type(e).__name__}: {e}")
    print(f"[梗百科] 收录/更新：{term}")
    return True


async def delete_slang(term: str, group_id: str = "") -> bool:
    term = term.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM slang WHERE term=? AND group_id=?", (term, group_id)
        )
        await db.commit()
        deleted = cursor.rowcount > 0
    if deleted:
        try:
            await delete_slang_point(_point_id(term))
        except Exception as e:
            print(f"[梗百科删索引失败] {type(e).__name__}: {e}")
    return deleted


async def list_slang(group_id: str = "", limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT term, explanation, updated_at FROM slang "
            "WHERE group_id=? ORDER BY updated_at DESC LIMIT ?",
            (group_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [{"term": r[0], "explanation": r[1], "updated_at": r[2]} for r in rows]


async def search_slang_entries(query_text: str, top_k: int = 2) -> list[dict]:
    """给主 prompt 用：embed 消息 → 检索 → 命中门槛过滤"""
    try:
        vector = await embed_text(query_text[:200])
        results = await search_slang_points(vector, top_k=top_k)
        return [r for r in results if r["score"] >= _RECALL_MIN]
    except Exception as e:
        print(f"[梗百科检索失败] {type(e).__name__}: {e}")
        return []
