"""
PDF 解析与分块（第一阶段：纯本地，不依赖 bot 运行）。

管线：
    extract_blocks(pdf_path)  → (Block[], 页数)     # 解析层
    chunk_blocks(blocks)      → Chunk[]             # 分块层

Block 是解析层与下游（分块/摘要/入库）之间的唯一契约——
v1 由 PyMuPDF 填充；将来换 Docling/MinerU 只需换一个填充器，下游零改动。

拒收分类学（extract_blocks 抛 ScannedPdfError 的两种情况）：
    - 无文字层（纯扫描版）
    - 文字层乱码（老 PDF 字体无 ToUnicode 映射，extract 出字形编码）

依赖：pip install PyMuPDF
本地测试（从项目根目录）：
    python3 -m services.pdf_parser lecun-98.pdf
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf as fitz  # 新版推荐导入名（旧名 fitz 已弃用）


# ============ 中间表示（契约，勿随意改字段名） ============


@dataclass
class Block:
    text: str
    type: str  # "title" | "text"
    level: int  # 标题层级（正文 = 0）
    page: int  # 物理页码，1 起
    section_path: str  # 所属章节路径，如 "1 引言 / 1.1 背景"


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    section_path: str
    idx: int = 0


class ScannedPdfError(Exception):
    """无可用文字层（扫描版/乱码）——调用方应拒收，不硬啃"""


# ============ 解析层 ============

_PAGE_NUM_RE = re.compile(r"^\s*-?\s*\d+\s*-?\s*$")
# 文字质量闸门：字母/汉字/数字/常用标点/空白 = 正常字符
_GOOD_CHAR_RE = re.compile(
    r"[0-9A-Za-z一-鿿，。、；：？！" "''（）《》〈〉\s\.,;:\?!\-\(\)\[\]/%]"
)
_MIN_TEXT_QUALITY = 0.6  # 可读字符占比低于此值 → 判定文字层乱码


def _text_quality(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if _GOOD_CHAR_RE.match(ch)) / len(text)


def _clean_page_text(raw: str, drop_lines: set[str]) -> str:
    """页眉页脚粗清洗：剔除纯页码行 + 跨页重复短行"""
    out = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or _PAGE_NUM_RE.match(s) or s in drop_lines:
            continue
        out.append(s)
    return "\n".join(out)


def _detect_running_headers(page_texts: list[str]) -> set[str]:
    """出现在 >=40% 页面首/尾行的相同短行 → 页眉页脚"""
    first, last = Counter(), Counter()
    for t in page_texts:
        ls = [l.strip() for l in t.splitlines() if l.strip()]
        if not ls:
            continue
        first[ls[0]] += 1
        if len(ls) > 1:
            last[ls[-1]] += 1
    n = max(1, len(page_texts))
    return {
        c
        for c, cnt in (first + last).items()
        if len(c) <= 40 and cnt >= max(3, n * 0.4)
    }


def _toc_headings(toc) -> list[tuple[int, int, str]]:
    """书签树 → [(page, level, title)]，滤掉无锚点项"""
    out = []
    for lvl, title, page in toc:
        if page and page >= 1:
            out.append((int(page), int(lvl), str(title).strip()))
    return sorted(out)


def _font_headings(doc, body_ratio: float = 1.3) -> list[tuple[int, int, str]]:
    """无 TOC 兜底：字号明显大于正文的短行视为标题（层级按字号倍数粗分）"""
    sizes: Counter = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    if s.get("text", "").strip():
                        sizes[round(s["size"], 1)] += len(s["text"])
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    heads = []
    for pno, page in enumerate(doc, start=1):
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = [s for s in l.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                txt = "".join(s["text"] for s in spans).strip()
                if not txt:
                    continue
                big = max(round(s["size"], 1) for s in spans)
                if big >= body * body_ratio and len(txt) <= 40:
                    lvl = 0 if big >= body * 1.7 else 1
                    heads.append((pno, lvl, txt))
    return heads


def _section_path_at(headings: list[tuple[int, int, str]], page: int) -> str:
    """该页生效的章节路径 = 各级最近标题拼接"""
    active: dict[int, str] = {}
    for p, lvl, title in headings:
        if p > page:
            break
        for k in list(active):
            if k > lvl:
                del active[k]
        active[lvl] = title
    return " / ".join(active[k] for k in sorted(active))


def extract_blocks(pdf_path: str) -> tuple[list[Block], int]:
    """解析 PDF → (Block 流, 页数)。无可用文字层抛 ScannedPdfError。"""
    doc = fitz.open(pdf_path)
    try:
        page_count = doc.page_count
        page_texts = [doc[i].get_text("text") for i in range(page_count)]

        total_chars = sum(len(t.strip()) for t in page_texts)
        # 文字层探测①：总量不足 → 纯扫描版
        if total_chars < 200 * (page_count // 10 + 1):
            raise ScannedPdfError("无文字层（疑似扫描版）")
        # 文字层探测②：质量闸门 → 老 PDF 字体编码损坏（extract 出乱码字形）
        sample = "".join(page_texts[:5])[:4000]
        if _text_quality(sample) < _MIN_TEXT_QUALITY:
            raise ScannedPdfError(
                f"文字层乱码（可读字符占比 {_text_quality(sample):.0%}，"
                "疑似老 PDF 字体无 ToUnicode 映射）"
            )

        headings = _toc_headings(doc.get_toc()) or _font_headings(doc)
        drop = _detect_running_headers(page_texts)
        heads_by_page: dict[int, list] = {}
        for h in headings:
            heads_by_page.setdefault(h[0], []).append(h)

        blocks: list[Block] = []
        for pno in range(1, page_count + 1):
            for hp, lvl, title in heads_by_page.get(pno, []):
                blocks.append(
                    Block(
                        text=title,
                        type="title",
                        level=lvl,
                        page=hp,
                        section_path=_section_path_at(headings, hp),
                    )
                )
            txt = _clean_page_text(page_texts[pno - 1], drop)
            if txt.strip():
                blocks.append(
                    Block(
                        text=txt,
                        type="text",
                        level=0,
                        page=pno,
                        section_path=_section_path_at(headings, pno),
                    )
                )
        return blocks, page_count
    finally:
        doc.close()


# ============ 分块层 ============

# 分隔符优先级：先保段落，再保句子（递归分隔的核心思想）
_SEPARATORS = ["\n\n", "\n", "。", "；", ";", "，", " "]


def _split_keep_sep(text: str) -> list[str]:
    """按优先级逐级找分隔符切分，片段保留句尾分隔符"""
    for sep in _SEPARATORS:
        if sep in text:
            parts, buf, i = [], "", 0
            while i < len(text):
                j = text.find(sep, i)
                if j == -1:
                    buf += text[i:]
                    break
                buf += text[i : j + len(sep)]
                if buf.strip():
                    parts.append(buf)
                buf = ""
                i = j + len(sep)
            if buf.strip():
                parts.append(buf)
            return parts
    return [text]


def _overlap_prefix(prev_text: str, overlap: int) -> str:
    """上一块尾部截 overlap 字，句对齐（从最近的句号/换行后开始）"""
    if not prev_text or overlap <= 0:
        return ""
    tail = prev_text[-overlap * 2 :]
    snap = max(tail.rfind("。"), tail.rfind("\n"))
    prefix = tail[snap + 1 :] if snap != -1 else tail[-overlap:]
    return prefix.strip()


def chunk_blocks(
    blocks: list[Block], max_chars: int = 750, overlap: int = 80
) -> list[Chunk]:
    """
    结构感知分块：
    - 标题强制起新块（标题随下一段正文走）；
    - 段落内按分隔符优先级递归切（不切半句）；
    - 相邻块重叠 overlap 字（句对齐，防页边界语义腰斩）；
    - chunk 继承 section_path / 页码区间（引用溯源的元数据）。
    """
    chunks: list[Chunk] = []
    buf, buf_pages, buf_sec = "", [], ""

    def flush() -> None:
        nonlocal buf, buf_pages, buf_sec
        if not buf.strip():
            buf, buf_pages, buf_sec = "", [], ""
            return
        prefix = _overlap_prefix(chunks[-1].text, overlap) if chunks else ""
        chunks.append(
            Chunk(
                text=(prefix + buf).strip(),
                page_start=buf_pages[0],
                page_end=buf_pages[-1],
                section_path=buf_sec,
            )
        )
        buf, buf_pages, buf_sec = "", [], ""

    for b in blocks:
        if b.type == "title":
            flush()
            buf = b.text + "\n"
            buf_pages = [b.page]
            if b.section_path:
                buf_sec = b.section_path
            continue
        if not buf:
            buf_sec = b.section_path
        buf_pages.append(b.page)
        for piece in _split_keep_sep(b.text):
            if buf.strip() and len(buf) + len(piece) > max_chars:
                flush()
                buf_sec = b.section_path
                buf_pages = [b.page]  # flush 清空了页码记录，重建
            buf += piece
    flush()

    for i, c in enumerate(chunks):
        c.idx = i
    return chunks


# ============ 本地测试 ============

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python3 -m services.pdf_parser <pdf路径> [max_chars] [overlap]")
        raise SystemExit(1)
    max_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 750
    overlap = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    blocks, n = extract_blocks(sys.argv[1])
    chunks = chunk_blocks(blocks, max_chars, overlap)
    print(f"页数 {n} | blocks {len(blocks)} | chunks {len(chunks)}")
    secs = {c.section_path for c in chunks}
    print(f"章节路径数 {len(secs)}")
    for c in chunks[:6]:
        head = c.text[:120].replace("\n", " ")
        print(
            f"\n--- chunk#{c.idx} P{c.page_start}-{c.page_end} [{c.section_path}] len={len(c.text)}"
        )
        print(head)
