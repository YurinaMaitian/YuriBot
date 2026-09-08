"""
联网搜索工具（博查 web-search）+ web_notes 语义缓存。
模型经 tool calling 调用 web_search()，缓存/覆盖写/防重痕迹对模型透明。
"""

import random
import time
from datetime import datetime

from services.http import get_session
from services.embedding import embed_text
from services import vector_store
from core.memory import record_message
from config import (
    BOCHA_API_KEY,
    BOCHA_WEB_SEARCH_URL,
    WEB_SEARCH_ENABLED,
    WEB_SEARCH_MAX_PER_DAY,
    WEB_NOTES_TTL,
)

_state = {"date": "", "count": 0}
_HIT_MIN = 0.9


def _daily_quota_left() -> bool:
    today = time.strftime("%Y-%m-%d")
    if _state["date"] != today:
        _state.update(date=today, count=0)
    return _state["count"] < WEB_SEARCH_MAX_PER_DAY


def _age_seconds(iso_ts: str) -> float:
    try:
        return time.time() - datetime.fromisoformat(iso_ts).timestamp()
    except (ValueError, TypeError):
        return 999999.0


async def _bocha_search(query: str, freshness) -> list[dict]:
    payload = {
        "query": query,
        "freshness": freshness,
        "summary": True,
        "count": 5,
    }
    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }
    session = get_session()
    async with session.post(
        BOCHA_WEB_SEARCH_URL, headers=headers, json=payload, timeout=15
    ) as r:
        data = await r.json()
    if data.get("code") != 200:
        print(f"[web_search] 博查错误 code={data.get('code')}: {str(data)[:150]}")
        return []
    return (data.get("data", {}).get("webPages", {}) or {}).get("value", []) or []


def _compress(results: list[dict], limit: int = 3) -> tuple[str, str]:
    """压成 (给DS的摘要文本, 首个来源URL)。截断防SEO废话淹没prompt"""
    parts, first_url = [], ""
    for i, r in enumerate(results[:limit]):
        summary = (r.get("summary") or r.get("snippet") or "").strip()[:180]
        date = (r.get("datePublished") or "")[:10]
        site = r.get("siteName", "")
        parts.append(
            f"{i + 1}. [{site}{' ' + date if date else ''}] {r.get('name', '')}: {summary}"
        )
        if not first_url:
            first_url = r.get("url", "")
    return "\n".join(parts), first_url


async def web_search(
    query: str,
    freshness: str = "oneMonth",
    force_refresh: bool = False,
    group_id: str = "",
    user_id: str = "",
) -> str:
    """tool calling 入口：缓存命中直接返回；miss才真搜索；过期命中覆盖写"""
    query = (query or "").strip()[:80]
    if not query:
        return "（没有可搜索的内容）"

    vector = None
    hit_id, hit = None, None
    try:
        vector = await embed_text(query)
        cands = await vector_store.search_web_notes(vector, top_k=1)
        if cands and cands[0]["score"] >= _HIT_MIN:
            hit = cands[0]
            age = _age_seconds(hit["created_at"])
            if age < WEB_NOTES_TTL and not force_refresh:
                return f"（{int(age // 86400)}天前搜过）{hit['answer']}"
            hit_id = hit["id"]  # 过期 OR 强制刷新：记住旧ID，搜完覆盖写
    except Exception as e:
        print(f"[web_search] 缓存检查失败: {type(e).__name__}: {e}")

    if not WEB_SEARCH_ENABLED:
        print(f"[web_search][shadow] 想搜: {query!r}")
        return "（联网搜索暂未开放；按你已知的回答，不知道就说不知道）"
    if not _daily_quota_left():
        return "（今天搜索额度用完了；按你已知的回答）"

    _state["count"] += 1
    results = await _bocha_search(query, freshness)
    if not results:
        return "（没搜到相关结果）"
    digest, url = _compress(results)
    answer = digest[:400]

    try:
        if vector is None:
            vector = await embed_text(query)
        pid = hit_id if hit_id is not None else random.getrandbits(63)
        rev = (hit.get("revision", 0) + 1) if hit else 1
        await vector_store.upsert_web_note(
            pid, query, answer, url, rev, vector, group_id
        )
    except Exception as e:
        print(f"[web_search] 入库失败: {type(e).__name__}: {e}")

    try:
        await record_message(
            group_id, user_id, "bot", f"[搜索] {query} → {answer[:50]}"
        )
    except Exception:
        pass
    return (
        "（注意：以下信息来自不同日期的网页，数字打架时以日期最新的为准，别引用过期数据）\n"
        + digest
    )
