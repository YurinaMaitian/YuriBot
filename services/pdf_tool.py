"""
PDF 工具：接收登记 → 下载 → 状态机（pending/processing/done/failed/rejected）。
D1 下午交付：登记 + 下载。解析触发（/pdf 命令 / read_pdf tool）在 D2。
"""

import hashlib
import os
import time
import math
import numpy as np

import aiosqlite
import random
import asyncio

from services.db import DB_PATH as _DB  # 复用（文件顶部已有 DB_PATH）
from services.embedding import embed_text
from services import vector_store
from core.ai import get_ai_reply, SYSTEM_PROMPT
from services.pdf_parser import extract_blocks, chunk_blocks, ScannedPdfError

from config import (
    DATA_DIR,
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    LIGHT_MODEL_KEY,
)
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
_chunk_cache: dict[
    str, list
] = {}  # doc_id → [(idx,text,page_start,page_end,section_path)]


_SEM = asyncio.Semaphore(3)
SECTION_BUDGET = 1800  # 一个章节桶塞进一次 4B 调用的字符预算
MAX_PAGES = 150


def _trigrams(s: str) -> list[str]:
    s = "".join(s.split()).lower()
    return [s[i : i + 3] for i in range(len(s) - 2)] or [s]


def _bm25(query: str, texts: list[str]) -> np.ndarray:
    docs = [_trigrams(t) for t in texts]
    q = set(_trigrams(query))
    N = len(docs)
    df = {t: sum(1 for d in docs if t in d) for t in q}
    idf = {t: math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5)) for t in q}
    avg = sum(len(d) for d in docs) / max(1, N)
    scores = np.zeros(N, dtype=np.float32)
    for i, d in enumerate(docs):
        s_ = sum(
            idf[t]
            * (d.count(t) * 2.5 / (d.count(t) + 1.5 * (1 - 0.75 + 0.75 * len(d) / avg)))
            for t in q
            if d.count(t)
        )
        scores[i] = s_
    return scores


async def _load_chunks(doc_id: str) -> list:
    if doc_id in _chunk_cache:
        return _chunk_cache[doc_id]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT idx, text, page_start, page_end, section_path"
            " FROM pdf_chunks WHERE doc_id=? ORDER BY idx",
            (doc_id,),
        ) as cur:
            rows = await cur.fetchall()
    _chunk_cache[doc_id] = rows
    return rows


def _is_refs(section_path: str) -> bool:
    return "reference" in (section_path or "").lower()


def _path_of(doc_id: str) -> str:
    return os.path.join(PDF_DIR, f"{doc_id}.pdf")


async def _set_status(doc_id, status, reason="", summary="", page_count=0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pdf_docs SET status=?, reject_reason=?, summary=?, page_count=?"
            " WHERE doc_id=?",
            (status, reason, summary[:2000], page_count, doc_id),
        )
        await db.commit()


async def _find_doc(group_id: str, prefix: str = ""):
    """按群 + 文件名前缀定位；无前缀取该群最新一份"""
    async with aiosqlite.connect(DB_PATH) as db:
        if prefix:
            async with db.execute(
                "SELECT doc_id, filename, status FROM pdf_docs"
                " WHERE group_id=? AND filename LIKE ?"
                " ORDER BY created_at DESC LIMIT 1",
                (group_id, prefix + "%"),
            ) as cur:
                return await cur.fetchone()
        async with db.execute(
            "SELECT doc_id, filename, status FROM pdf_docs"
            " WHERE group_id=? ORDER BY created_at DESC LIMIT 1",
            (group_id,),
        ) as cur:
            return await cur.fetchone()


def _batches_by_section(chunks) -> list[tuple[str, list]]:
    """按 section_path 分桶，每桶再按预算切成若干批"""
    buckets, order = {}, []
    for c in chunks:
        key = c.section_path or "(无章节)"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)
    batches = []
    for key in order:
        buf, size = [], 0
        for c in buckets[key]:
            if buf and size + len(c.text) > SECTION_BUDGET:
                batches.append((key, buf))
                buf, size = [], 0
            buf.append(c)
            size += len(c.text)
        if buf:
            batches.append((key, buf))
    return batches


MAP_SYSTEM = (
    "你是文献要点提取员。用2-4句话概括给定段落的要点。只输出要点，不解释、不寒暄。"
)
REDUCE_USER = (
    "你刚读完群友发的一份PDF。以下是各章节的要点：\n\n{points}\n\n"
    "用你平时的说话方式给群友做总结：先一句话说这是什么文档，然后分章节说核心内容"
    "（每章一两句），最后加一句你自己的看法。总共200字以内，别端着。"
)


async def _map_batch(section: str, chunk_list: list) -> str:
    body = "\n".join(f"[P{c.page_start}] {c.text[:600]}" for c in chunk_list)
    raw = await get_ai_reply(
        user_message=f"【章节】{section}\n\n{body}",
        system_override=MAP_SYSTEM,
        max_tokens=200,
        temperature=0.2,
        model=LIGHT_MODEL_NAME,
        api_url=LIGHT_MODEL_URL,
        api_key=LIGHT_MODEL_KEY,
        timeout=60,
        enable_thinking=False,
        tag="pdf_map",
    )
    return raw or "（该章节未能提取要点）"


async def process_doc(doc_id: str, group_id: str, msg_id: str) -> None:
    """完整管线：解析→分块→入库→章节级Map→Reduce→交付。fire-and-forget。"""
    from services.sender import enqueue_chat

    try:
        await _set_status(doc_id, STATUS_PROCESSING)
        blocks, page_count = extract_blocks(_path_of(doc_id))
        if page_count > MAX_PAGES:
            await _set_status(doc_id, STATUS_REJECTED, f"超过{MAX_PAGES}页")
            return
        chunks = chunk_blocks(blocks)
        await _set_status(doc_id, STATUS_PROCESSING, page_count=page_count)

        # 清单落库
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                "INSERT INTO pdf_chunks (doc_id, idx, page_start, page_end,"
                " section_path, char_len, text) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        doc_id,
                        c.idx,
                        c.page_start,
                        c.page_end,
                        c.section_path,
                        len(c.text),
                        c.text[:800],
                    )
                    for c in chunks
                ],
            )
            await db.commit()

        # 向量化入库（Semaphore 限流保护免费 embedding API）
        embed_fail = 0

        async def _embed_one(c):
            nonlocal embed_fail
            try:
                vector = await embed_text(c.text[:800])
                await vector_store.upsert_doc_point(
                    random.getrandbits(63),
                    doc_id,
                    c.text,
                    c.page_start,
                    c.page_end,
                    c.section_path,
                    "chunk",
                    group_id,
                    vector,
                    c.idx,
                )
            except Exception:
                embed_fail += 1

        await asyncio.gather(*[_embed_one(c) for c in chunks])
        if embed_fail > len(chunks) // 2:
            await _set_status(
                doc_id, STATUS_FAILED, f"入库失败{embed_fail}/{len(chunks)}"
            )
            return

        # 章节级 Map
        batches = _batches_by_section(chunks)
        print(f"[PDF] Map 开始: {len(batches)} 批 / {len(chunks)} chunks")

        async def _map_one(i, sec, cls):
            async with _SEM:
                r = await _map_batch(sec, cls)
                print(f"[PDF] Map {i + 1}/{len(batches)} [{sec[:16]}] ok")
                return sec, r

        results = await asyncio.gather(
            *[_map_one(i, s, c) for i, (s, c) in enumerate(batches)]
        )
        points = "\n\n".join(f"【{s}】\n{r}" for s, r in results)

        # Reduce：DS 用人设做最终总结
        summary = await get_ai_reply(
            user_message=REDUCE_USER.format(points=points),
            system_override=SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.7,
            tag="pdf_reduce",
        )
        if not summary:
            await _set_status(doc_id, STATUS_FAILED, "reduce空响应")
            return
        await _set_status(doc_id, STATUS_DONE, summary=summary)

        # 异步交付（发群里；[文件] 前缀让 DS 知道她读过）
        await enqueue_chat(
            group_id,
            "",
            f"[文件] {summary}",
            msg_id,
            is_group=True,
            memory_tag="[文件] ",
        )
        print(f"[PDF] done: {doc_id[:8]} 页数{page_count} chunks{len(chunks)}")
    except ScannedPdfError as e:
        await _set_status(doc_id, STATUS_REJECTED, str(e))
        print(f"[PDF] 拒收: {doc_id[:8]} {e}")
    except Exception as e:
        await _set_status(doc_id, STATUS_FAILED, type(e).__name__)
        print(f"[PDF] 处理失败: {type(e).__name__}: {e}")


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
        async with db.execute("PRAGMA table_info(pdf_chunks)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        if "text" not in cols:
            await db.execute("ALTER TABLE pdf_chunks ADD COLUMN text TEXT DEFAULT ''")

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


async def retrieve_for(group_id: str, query: str, top_k: int = 5) -> str | None:
    """hybrid：向量top10 + BM25(trigram)top10 → RRF融合 → refs过滤 → top_k"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT doc_id, filename FROM pdf_docs"
            " WHERE group_id=? AND status='done'"
            " ORDER BY created_at DESC LIMIT 1",
            (group_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    doc_id, filename = row
    rows = await _load_chunks(doc_id)
    if not rows:
        return None

    page2idx = {(r[2], r[3]): r[0] for r in rows}
    vector = await embed_text(query[:200])
    vec_hits = await vector_store.search_doc_chunks(doc_id, vector, top_k=10)

    rrf: dict[int, float] = {}
    for rank, h in enumerate(vec_hits):
        sec = h.get("section_path", "")
        if _is_refs(sec):
            continue
        idx = h.get("idx") or page2idx.get((h["page_start"], h["page_end"]))
        if idx is None:
            continue
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank + 1)

    bm = _bm25(query, [r[1] for r in rows])
    for rank, i in enumerate(bm.argsort()[::-1][:10]):
        if bm[i] <= 0:
            continue
        idx, _, _, _, sec = rows[int(i)]
        if _is_refs(sec):
            continue
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank + 1)

    if not rrf:
        return None
    picked = sorted(rrf, key=rrf.get, reverse=True)[:top_k]
    by_idx = {r[0]: r for r in rows}
    lines = [f"【文档要点】《{filename}》相关内容："]
    for idx in picked:
        r = by_idx[idx]
        lines.append(f"- (P{r[2]}-{r[3]}) {r[1][:150]}")
    print(f"[PDF] 追问检索: 命中{len(picked)}块 (BM25+向量RRF)")
    return "\n".join(lines)
