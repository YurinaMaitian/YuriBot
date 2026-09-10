"""
PDF golden set 命中率评测（离线：不碰 Qdrant / bot / 正式库）。

判分三级（失败定位到哪一阶段）：
    解析缺失(parse_miss)   —— 没有任何 chunk 包含 anchor → 解析层的锅（表格/公式的失败都在这）
    检索未命中(retr_miss)  —— anchor 在库里但 top_k 没检到 → 打印"包含锚的块排名"供调优
    命中(hit)              —— top_k 中某块与包含 anchor 的块页码重叠

匹配用零空白归一化（LaTeX PDF 的数学-文本粘连 artifact："34 vertices" 在文本层是 "34vertices"）。

用法：
    python3 -m test_pdf_rag testset_chebnet.json [top_k]
    python3 -m test_pdf_rag                 # 跑所有 testset_*.json

chunk embedding 按文件哈希缓存到 /tmp（.npy），改问题/改锚重跑秒出。
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from services.embedding import embed_text
from services.http import close_session
from services.pdf_parser import extract_blocks, chunk_blocks

TOP_K = int(sys.argv[2]) if len(sys.argv) > 2 else 3


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a @ b.T


async def _embed_chunks(doc_path: str, chunks) -> np.ndarray:
    raw = Path(doc_path).read_bytes()
    key = hashlib.md5(raw).hexdigest()[:16]
    cache = Path(f"/tmp/pdf_rag_{key}_{len(chunks)}.npy")
    if cache.exists():
        return np.load(cache)
    vecs = [await embed_text(c.text[:800]) for c in chunks]
    arr = np.asarray(vecs, dtype=np.float32)
    np.save(cache, arr)
    return arr


async def run_one(spec_file: str) -> dict:
    spec = json.loads(Path(spec_file).read_text(encoding="utf-8"))
    blocks, pages = extract_blocks(spec["doc_path"])
    chunks = chunk_blocks(blocks)
    chunk_vecs = await _embed_chunks(spec["doc_path"], chunks)
    print(
        f"\n=== {spec['doc_name']}（{pages}页 / {len(chunks)} chunks / {len(spec['questions'])}问）==="
    )

    stats = {"hit": 0, "retr_miss": 0, "parse_miss": 0}
    dim_stats: dict = {}
    for q in spec["questions"]:
        dim = q.get("dimension", "?")
        dim_stats.setdefault(dim, {"hit": 0, "retr_miss": 0, "parse_miss": 0})
        anchor = q["anchor"]
        norm = lambda s: "".join(s.split())  # 零空白归一化：免疫胶合/多空格/换行
        containing = [i for i, c in enumerate(chunks) if norm(anchor) in norm(c.text)]
        if not containing:
            stats["parse_miss"] += 1
            dim_stats[dim]["parse_miss"] += 1
            print(f"  [解析缺失] {q['id']} [{dim}] anchor={anchor!r}")
            continue
        qv = np.asarray([await embed_text(q["question"][:200])], dtype=np.float32)
        scores = _cosine(chunk_vecs, qv).flatten()
        order = scores.argsort()[::-1]
        top_idx = order[:TOP_K]
        tgt_pages = {(chunks[i].page_start, chunks[i].page_end) for i in containing}
        hit = any(
            (chunks[i].page_start, chunks[i].page_end) in tgt_pages
            or not (
                chunks[i].page_end < min(p[0] for p in tgt_pages)
                or chunks[i].page_start > max(p[1] for p in tgt_pages)
            )
            for i in top_idx
        )
        if hit:
            stats["hit"] += 1
            dim_stats[dim]["hit"] += 1
            print(
                f"  [✓] {q['id']} [{dim}] top页={[chunks[i].page_start for i in top_idx]} 期望P{q.get('page', '?')}"
            )
        else:
            stats["retr_miss"] += 1
            dim_stats[dim]["retr_miss"] += 1
            # 包含锚的块排第几：4~10名→提top_k/reranker；很远→chunk语义鸿沟
            rank_of_target = int(min(np.where(np.isin(order, containing))[0])) + 1
            print(
                f"  [✗] {q['id']} [{dim}] top页={[chunks[i].page_start for i in top_idx]}"
                f" 期望P{q.get('page', '?')} 目标块rank={rank_of_target}/{len(chunks)}"
            )

    total = sum(stats.values())
    print(
        f"  小计: 命中 {stats['hit']}/{total}"
        f"  检索未命中 {stats['retr_miss']}  解析缺失 {stats['parse_miss']}"
    )
    for dim, s in dim_stats.items():
        t = sum(s.values())
        print(
            f"    - {dim}: {s['hit']}/{t} 命中"
            + (f"（解析缺失 {s['parse_miss']}）" if s["parse_miss"] else "")
        )
    return stats


async def main():
    files = sys.argv[1:2] or sorted(str(p) for p in Path(".").glob("testset_*.json"))
    if not files:
        print("没找到 testset_*.json")
        return
    grand = {"hit": 0, "retr_miss": 0, "parse_miss": 0}
    try:
        for f in files:
            s = await run_one(f)
            for k in grand:
                grand[k] += s[k]
    finally:
        await close_session()
    total = sum(grand.values())
    print(
        f"\n===== 总计 {grand['hit']}/{total} 命中"
        f"（检索未命中 {grand['retr_miss']} / 解析缺失 {grand['parse_miss']}）====="
    )


if __name__ == "__main__":
    asyncio.run(main())
