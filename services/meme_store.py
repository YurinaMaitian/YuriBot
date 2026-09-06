"""
表情包系统 v2：求图协议（主路径）+ 指代回发（echo）。

主路径（DS 驱动）：
  DS 回复末尾单独一行写【求图:内容描述】→ 管线剥离标记 → embed 描述检索 top3
  → 免费选择器二次调用（纯 JSON 点选）→ 命中入队发送（排在文字气泡后）
  全程单回合工具循环，无跨轮漂移。

echo：群友说"再发一遍这张/同样的"→ 从近期对话找图原样回发。

存储：memes/（永久，type=meme） + images_recent/（全图滚动3天，echo素材）
"""

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime

import aiosqlite

from config import (
    DATA_DIR,
    MEME_COOLDOWN,
    MEME_MIN_REPLY_LEN,
)
from core.ai import get_ai_reply
from core.memory import get_context
from services import image_cache
from services.db import DB_PATH
from services.embedding import embed_text
from services.vector_store import delete_meme, search_memes, upsert_meme

MEME_DIR = os.path.join(DATA_DIR, "memes")
RECENT_DIR = os.path.join(DATA_DIR, "images_recent")
RECENT_KEEP_DAYS = 3

_ECHO_RE = re.compile(r"(同样|同款|这张|这个|原图|还发|再来)")
_MEME_REQ_RE = re.compile(r"【求图[:：]([^】]+)】")

CHOOSER_SYSTEM = """你是表情包挑选器。给定"她刚说的话"、她的配图意图和候选表情包列表，选出最贴合的一张。
标准：图文气质要合，图是对她的话的补充而不是重复；候选都不合就放弃。
只输出严格 JSON：{"pick": 编号} 或 {"pick": 0}，不要解释。"""

_last_sent: dict[str, float] = {}


def point_id_of(filename: str) -> int:
    return int.from_bytes(hashlib.md5(filename.encode()).digest()[:8], "big")


def _path(filename: str) -> str:
    return os.path.join(MEME_DIR, filename)


def resolve_file(filename: str) -> str | None:
    p = _path(filename)
    if os.path.exists(p):
        return p
    rp = os.path.join(RECENT_DIR, filename)
    if os.path.exists(rp):
        return rp
    return None


# ========== 求图协议 ==========


def extract_meme_request(text: str) -> tuple[str, str | None]:
    """从回复里剥离【求图:内容】标记。返回 (干净文本, 求图内容或None)"""
    matches = list(_MEME_REQ_RE.finditer(text))
    if not matches:
        return text, None
    m = matches[-1]
    request = m.group(1).strip()[:50]
    clean = (text[: m.start()] + text[m.end() :]).strip()
    # 清掉标记行残留的空行
    clean = re.sub(r"\n{2,}$", "\n", clean).strip()
    return clean, request


async def meme_tool_loop(
    group_id: str,
    user_id: str,
    reply_text: str,
    request: str,
    msg_id: str,
    is_group: bool = True,
):
    """求图协议：检索 → 二次调用点选 → 入队发送。文字气泡已先行。"""
    try:
        vector = await embed_text(request[:100])
        # 显式求图，门槛放低（她都开口要了，候选差点也给她挑）
        cands = [
            c
            for c in await search_memes(group_id, vector, top_k=3)
            if c["score"] >= 0.45
        ]
        if not cands:
            await _log(
                group_id, reply_text, None, [], None, "no_candidate", request[:30]
            )
            return

        lines = []
        for i, c in enumerate(cands, 1):
            lock = " ✓已订正" if c.get("manual") else ""
            lines.append(f"{i}. {c['description']}{lock}")
        user_msg = (
            f"她刚说完这段话：\n{reply_text[:200]}\n\n"
            f"她想要一张表情包：{request}\n\n"
            f"库里的候选：\n" + "\n".join(lines) + "\n\n"
            '选一张最合适的，或放弃。只输出JSON：{"pick": 编号} 或 {"pick": 0}'
        )
        raw = await get_ai_reply(
            user_message=user_msg,
            system_override=CHOOSER_SYSTEM,
            max_tokens=60,
            temperature=0.0,
            timeout=30,
            enable_thinking=False,
        )
        pick = 0
        m = re.search(r"\{[^{}]*\}", raw or "")
        if m:
            try:
                pick = int(json.loads(m.group(0)).get("pick", 0))
            except (json.JSONDecodeError, ValueError, TypeError):
                pick = 0
        if pick <= 0:
            await _log(
                group_id,
                reply_text,
                cands[0]["score"],
                _brief(cands),
                (raw or "")[:200],
                "declined",
                request[:30],
            )
            return
        if pick > len(cands):
            await _log(
                group_id,
                reply_text,
                cands[0]["score"],
                _brief(cands),
                (raw or "")[:200],
                "bad_index",
                request[:30],
            )
            return
        await _deliver_pick(
            group_id,
            user_id,
            cands[pick - 1],
            msg_id,
            is_group,
            reply_text,
            _brief(cands),
            "tool_pick",
            (raw or "")[:200],
            request[:30],
        )
    except Exception as e:
        print(f"[表情包] {type(e).__name__}: {e}")


# ========== echo 指代回发 ==========


def _last_context_image(group_id: str, user_id: str) -> str | None:
    ctx = get_context(group_id, user_id)
    for m in reversed(ctx[-10:]):
        found = re.findall(r"【图片:([^】]+)】", m.get("content", ""))
        if found:
            return found[-1]
    return None


async def maybe_attach_meme(
    group_id: str,
    user_id: str,
    reply_text: str,
    msg_id: str,
    is_group: bool = True,
    user_text: str = "",
):
    """仅 echo：群友要求"再发一遍这张/同样的"→ 原图回发。"""
    try:
        text = reply_text.strip()
        if len(text) < MEME_MIN_REPLY_LEN:
            return
        combined = f"{user_text} {text}"
        if not _ECHO_RE.search(combined):
            return
        target = None
        m = re.findall(r"【图片:([^】]+)】", user_text or "")
        if m:
            target = m[-1]
        if target is None:
            target = _last_context_image(group_id, user_id)
        if not target:
            return
        p = resolve_file(target)
        if not p:
            await _log(group_id, text, None, [], None, "echo_missing_file", target[:16])
            return
        info = await image_cache.get_image(target)
        desc = (info and info["description"]) or "群友的那张图"
        await _deliver_path(
            group_id,
            user_id,
            p,
            desc,
            msg_id,
            is_group,
            text,
            [],
            "echo",
            None,
            "",
            enforce_cooldown=False,
        )
    except Exception as e:
        print(f"[表情包] {type(e).__name__}: {e}")


# ========== 发送 ==========


async def _deliver_path(
    group_id,
    user_id,
    path,
    desc,
    msg_id,
    is_group,
    reply_text,
    cands,
    action,
    judge_raw,
    reason,
    enforce_cooldown=True,
) -> bool:
    now = time.time()
    if enforce_cooldown and now - _last_sent.get(group_id, 0) < MEME_COOLDOWN:
        await _log(group_id, reply_text, None, cands, judge_raw, "cooldown", reason)
        return False
    _last_sent[group_id] = now
    from services.sender import enqueue_image

    await enqueue_image(group_id, user_id, path, desc, msg_id, is_group=is_group)
    await _log(group_id, reply_text, None, cands, judge_raw, action, reason)
    print(f"[表情包] 已排队({action}): {desc[:30]}")
    return True


async def _deliver_pick(
    group_id,
    user_id,
    pick,
    msg_id,
    is_group,
    text,
    cands,
    action,
    judge_raw=None,
    reason="",
):
    p = resolve_file(pick["filename"])
    if not p:
        await _log(
            group_id, text, pick["score"], cands, judge_raw, "file_missing", reason
        )
        return
    await _deliver_path(
        group_id,
        user_id,
        p,
        pick["description"],
        msg_id,
        is_group,
        text,
        cands,
        action,
        judge_raw,
        reason,
    )


# ========== 存储与索引 ==========


async def save_meme_file(filename: str, img_bytes: bytes):
    os.makedirs(MEME_DIR, exist_ok=True)
    with open(_path(filename), "wb") as f:
        f.write(img_bytes)


async def save_recent_file(filename: str, img_bytes: bytes):
    os.makedirs(RECENT_DIR, exist_ok=True)
    path = os.path.join(RECENT_DIR, filename)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(img_bytes)
    _prune_recent()


def _prune_recent():
    now = time.time()
    try:
        for fn in os.listdir(RECENT_DIR):
            p = os.path.join(RECENT_DIR, fn)
            try:
                if now - os.path.getmtime(p) > RECENT_KEEP_DAYS * 86400:
                    if not os.path.exists(_path(fn)):
                        os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


async def sync_index(filename: str):
    """按 image_cache 状态同步索引；订正为meme时若文件只在滚动缓存则转存永久库"""
    info = await image_cache.get_image(filename)
    if not info:
        return
    pid = point_id_of(filename)
    if info["type"] == "meme" and info["description"]:
        if not os.path.exists(_path(filename)):
            recent = os.path.join(RECENT_DIR, filename)
            if os.path.exists(recent):
                os.makedirs(MEME_DIR, exist_ok=True)
                with open(recent, "rb") as src, open(_path(filename), "wb") as dst:
                    dst.write(src.read())
            else:
                return
        try:
            vector = await embed_text(info["description"][:200])
            await upsert_meme(
                pid,
                filename,
                info["description"],
                info.get("group_id", ""),
                bool(info["manual"]),
                vector,
            )
            print(f"[表情包索引] 入库 {filename[:16]}…（{info['description'][:20]}）")
        except Exception as e:
            print(f"[表情包索引失败] {type(e).__name__}: {e}")
    else:
        try:
            await delete_meme(pid)
        except Exception as e:
            print(f"[表情包删索引] {type(e).__name__}: {e}")


# ========== 日志 ==========


async def init_meme_log_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meme_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                reply TEXT,
                score REAL,
                candidates TEXT,
                judge TEXT,
                action TEXT,
                reason TEXT,
                created_at TIMESTAMP
            )
        """)
        await db.commit()


async def _log(group_id, reply, score, candidates, judge_raw, action, reason):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO meme_log
                   (group_id, reply, score, candidates, judge, action, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    group_id,
                    reply[:200],
                    score,
                    json.dumps(candidates, ensure_ascii=False)[:500]
                    if candidates
                    else "",
                    (judge_raw or "")[:200],
                    action,
                    (reason or "")[:50],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
    except Exception as e:
        print(f"[表情包日志失败] {type(e).__name__}: {e}")


def _brief(cands: list) -> list:
    return [
        {
            "d": c["description"][:30],
            "s": round(c["score"], 3),
            "fn": c["filename"][:12],
        }
        for c in cands
    ]
