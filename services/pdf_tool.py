"""
PDF 工具：接收登记 → 下载 → 状态机（pending/processing/done/failed/rejected）。
D1 下午交付：登记 + 下载。解析触发（/pdf 命令 / read_pdf tool）在 D2。
"""

import hashlib
import os
import time

import aiosqlite

from config import DATA_DIR
from services.db import DB_PATH
from services.http import get_session

PDF_DIR = os.path.join(DATA_DIR, "pdfs")
PDF_MAX_BYTES = 20 * 1024 * 1024  # 20MB 上限，超出走拒收
PDF_KEEP_DAYS = 3  # 磁盘滚动缓存

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"


async def init_pdf_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS pdf_docs (
            doc_id TEXT PRIMARY KEY,
            group_id TEXT, filename TEXT,
            page_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            reject_reason TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            created_at TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS pdf_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT, idx INTEGER,
            page_start INTEGER, page_end INTEGER,
            section_path TEXT DEFAULT '', char_len INTEGER DEFAULT 0
        )""")
        await db.commit()


async def _insert(group_id, filename, page_count, status, reason, now, doc_id=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO pdf_docs"
            " (doc_id, group_id, filename, page_count, status, reject_reason, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (doc_id, group_id, filename, page_count, status, reason, now),
        )
        await db.commit()


def _prune():
    """磁盘滚动：删超期且未 pin 的文件"""
    now = time.time()
    try:
        for fn in os.listdir(PDF_DIR):
            p = os.path.join(PDF_DIR, fn)
            if now - os.path.getmtime(p) > PDF_KEEP_DAYS * 86400:
                os.remove(p)
    except OSError:
        pass


async def download_and_register(
    url: str, filename: str, size, group_id: str, user_id: str = ""
) -> None:
    """fire-and-forget 入口（main.py 调用）：下载→内容哈希→登记。
    超大/下载失败/空文件也落一条记录，拒收分类学有档可查。"""
    size = int(size or 0)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    filename = (filename or "file.pdf")[:100]
    try:
        if size and int(size) > PDF_MAX_BYTES:
            await _insert(
                group_id,
                filename,
                0,
                STATUS_REJECTED,
                f"超过{PDF_MAX_BYTES // 1048576}MB",
                now,
            )
            print(f"[PDF] 拒收(过大): {filename} ({size // 1024}KB)")
            return
        session = get_session()
        async with session.get(url, timeout=60) as r:
            if r.status != 200:
                raise ValueError(f"下载HTTP {r.status}")
            data = await r.read()
        if not data:
            raise ValueError("空文件")

        doc_id = hashlib.md5(data).hexdigest()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT status FROM pdf_docs WHERE doc_id=?", (doc_id,)
            ) as cur:
                row = await cur.fetchone()
        if row:
            print(f"[PDF] 已存在({row[0]})，跳过: {filename}")
            return

        os.makedirs(PDF_DIR, exist_ok=True)
        with open(os.path.join(PDF_DIR, f"{doc_id}.pdf"), "wb") as f:
            f.write(data)
        await _insert(group_id, filename, 0, STATUS_PENDING, "", now, doc_id)
        _prune()
        print(
            f"[PDF] 登记 pending: {filename} doc={doc_id[:8]} ({len(data) // 1024}KB)"
        )
    except Exception as e:
        print(f"[PDF] 下载/登记失败: {type(e).__name__}: {e}")
        await _insert(group_id, filename, 0, STATUS_FAILED, type(e).__name__, now)
