"""
PDF golden set 命中率评测（离线：不碰 Qdrant / bot / 正式库）。

判分三级（失败定位到哪一阶段）：
    解析缺失(parse_miss)   —— 没有任何 chunk 包含 anchor → 解析层的锅
    检索未命中(retr_miss)  —— anchor 在库里但 top_k 没检到 → 打印"目标块rank"
    命中(hit)              —— top_k 中某块与包含 anchor 的块页码重叠

用法：
    python3 -m test_pdf_rag testset_chebnet.json [top_k]          # 基线（纯向量）
    python3 -m test_pdf_rag testset_chebnet.json 5 --hybrid       # 升级版（BM25+向量RRF+refs过滤）
    python3 -m test_pdf_rag                                       # 跑所有 testset_*.json

--hybrid 相比基线的改动：
    1. BM25(trigram) 一路：对缩写/专有名词（GAE/STGNN）弥补纯向量盲区；176 chunks 毫秒级
    2. RRF 融合：两路各取 top10，score=Σ1/(60+rank)
    3. references 过滤：section_path 含 REFERENCE 的块不参与检索（治 P9 参考文献漂移）
A/B 规矩：hybrid 必须在同一份 golden set 上跑赢基线（24/30），才准进生产 retrieve_for。
"""

import asyncio
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from services.embedding import embed_text
from services.http import close_session
from services.pdf_parser import extract_blocks, chunk_blocks

args = [a for a in sys.argv[1:] if not a.startswith("--")]
TOP_K = int(args[1]) if len(args) > 1 else 3
HYBRID = "--hybrid" in sys.argv


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a @ b.T


# ---------- BM25(trigram)：零依赖，中文按三字滑窗 ----------


def _trigrams(s: str) -> list[str]:
    s = "".join(s.split()).lower()
    return [s[i : i + 3] for i in range(len(s) - 2)] or [s]


def _bm25(query: str, chunks) -> np.ndarray:
    docs = [_trigrams(c.text) for c in chunks]
    q = set(_trigrams(query))
    N = len(docs)
    df = {t: sum(1 for d in docs if t in d) for t in q}
    idf = {t: math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5)) for t in q}
    avg = sum(len(d) for d in docs) / max(1, N)
    scores = np.zeros(N, dtype=np.float32)
    for i, d in enumerate(docs):
        tf = sum(d.count(t) for t in q)
        if not tf:
            continue
        dl = len(d)
        s_ = sum(
            idf[t]
            * (
                d.count(t)
                * (1.5 + 1)
                / (d.count(t) + 1.5 * (1 - 0.75 + 0.75 * dl / avg))
            )
            for t in q
        )
        scores[i] = s_
    return scores


def _rrr_fuse(
    vec_order: np.ndarray, bm25_scores: np.ndarray, k: int = 10
) -> np.ndarray:
    """两路各取 topk，RRF 融合返回全局排序索引"""
    rrf = np.zeros(len(vec_order), dtype=np.float32)
    for rank, i in enumerate(vec_order[:k]):
        rrf[i] += 1.0 / (60 + rank + 1)
    bm_order = bm25_scores.argsort()[::-1][:k]
    for rank, i in enumerate(bm_order):
        if bm25_scores[i] > 0:
            rrf[i] += 1.0 / (60 + rank + 1)
    return rrf.argsort()[::-1]


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


def _is_refs(c) -> bool:
    return "reference" in (c.section_path or "").lower()


async def run_one(spec_file: str) -> dict:
    spec = json.loads(Path(spec_file).read_text(encoding="utf-8"))
    blocks, pages = extract_blocks(spec["doc_path"])
    chunks = chunk_blocks(blocks)
    chunk_vecs = await _embed_chunks(spec["doc_path"], chunks)
    mode = "hybrid(BM25+向量RRF+refs过滤)" if HYBRID else "基线(纯向量)"
    print(f"\n=== {spec['doc_name']}（{pages}页 / {len(chunks)} chunks / {mode}）===")

    stats = {"hit": 0, "retr_miss": 0, "parse_miss": 0}
    dim_stats: dict = {}
    bm25_cache = _bm25_all = None
    for q in spec["questions"]:
        dim = q.get("dimension", "?")
        dim_stats.setdefault(dim, {"hit": 0, "retr_miss": 0, "parse_miss": 0})
        anchor = q["anchor"]
        norm = lambda s: "".join(s.split())
        containing = [i for i, c in enumerate(chunks) if norm(anchor) in norm(c.text)]
        if not containing:
            stats["parse_miss"] += 1
            dim_stats[dim]["parse_miss"] += 1
            print(f"  [解析缺失] {q['id']} [{dim}] anchor={anchor!r}")
            continue
        qv = np.asarray([await embed_text(q["question"][:200])], dtype=np.float32)
        vec_scores = _cosine(chunk_vecs, qv).flatten()
        vec_order = vec_scores.argsort()[::-1]
        if HYBRID:
            pool = [i for i in range(len(chunks)) if not _is_refs(chunks[i])]
            if bm25_cache is None:
                bm25_cache = True
                bm25_all = _bm25_all = None
            bm25_scores = _bm25(q["question"], chunks)
            # refs 过滤：两路都 mask 掉
            mask = np.array([_is_refs(c) for c in chunks])
            vec_scores_m = np.where(mask, -1e9, vec_scores)
            bm25_scores_m = np.where(mask, -1e9, bm25_scores)
            order = _rrr_fuse(vec_scores_m.argsort()[::-1], bm25_scores_m)
        else:
            order = vec_order
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
    files = args[0:1] or sorted(str(p) for p in Path(".").glob("testset_*.json"))
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
