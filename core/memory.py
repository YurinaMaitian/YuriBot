import re
import aiosqlite
from collections import deque
from datetime import datetime
from typing import List
from services.db import save_message, DB_PATH
from services import image_cache

_hot_cache = {}

IMAGE_PLACEHOLDER_RE = re.compile(r"【图片:([^】]+)】")


def _get_key(group_id: str, user_id: str) -> str:
    if not group_id:
        return f"c2c:{user_id}"
    return group_id


def _format_rel_time(msg_time: datetime) -> str:
    if not msg_time:
        return ""
    delta = (datetime.now() - msg_time).total_seconds()
    if delta < 60:
        return "[刚刚] "
    elif delta < 300:
        return "[几分钟前] "
    elif delta < 900:
        return "[刚才] "
    elif delta < 3600:
        return "[半小时前] "
    elif delta < 7200:
        return "[一小时前] "
    return ""


async def _lazy_load(key: str, group_id: str, user_id: str):
    from services.user_manager import get_nickname

    async with aiosqlite.connect(DB_PATH) as db:
        if group_id:
            async with db.execute(
                "SELECT speaker, speaker_id, content, created_at FROM messages WHERE group_id = ? ORDER BY created_at DESC LIMIT 20",
                (group_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT speaker, speaker_id, content, created_at FROM messages WHERE group_id = '' AND (speaker_id = ? OR speaker_id = 'yuribot') ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

    if rows:
        _hot_cache[key] = deque(maxlen=50)
        for speaker, speaker_id, content, created_at in reversed(rows):
            identity = "YuriBot" if speaker == "bot" else await get_nickname(speaker_id)
            msg_time = None
            if created_at:
                try:
                    msg_time = datetime.fromisoformat(created_at)
                except (ValueError, TypeError):
                    msg_time = datetime.now()
            _hot_cache[key].append(
                {
                    "speaker": speaker,
                    "identity": identity,
                    "content": content[:200],
                    "time": msg_time,
                }
            )
        print(f"[缓存恢复] key={key[:20]}, 恢复{len(rows)}条")


async def record_message(group_id: str, user_id: str, speaker: str, content: str):
    key = _get_key(group_id, user_id)
    if key not in _hot_cache or len(_hot_cache[key]) == 0:
        await _lazy_load(key, group_id, user_id)
    if key not in _hot_cache:
        _hot_cache[key] = deque(maxlen=50)

    from services.user_manager import get_nickname

    identity = "YuriBot" if speaker == "bot" else await get_nickname(user_id)
    _hot_cache[key].append(
        {
            "speaker": speaker,
            "identity": identity,
            "content": content[:200],
            "time": datetime.now(),
        }
    )

    db_speaker_id = "yuribot" if speaker == "bot" else user_id
    await save_message(group_id, speaker, db_speaker_id, content)


def get_context(group_id: str, user_id: str) -> List[dict]:
    return list(_hot_cache.get(_get_key(group_id, user_id), []))


async def get_history_text(group_id: str, user_id: str) -> str:
    """组装带相对时间戳的历史文本。Router 和主 prompt 共用，保证两者看到同一份上下文"""
    ctx = get_context(group_id, user_id)
    if not ctx:
        return ""
    history_lines = []
    for m in ctx:
        rel = _format_rel_time(m.get("time"))
        history_lines.append(f"{rel}{m['identity']}：{m['content']}")
    all_text = "\n".join(history_lines)
    if len(all_text) < 600:
        return all_text
    recent = history_lines[-15:] if len(history_lines) >= 15 else history_lines
    return "\n".join(recent)


async def substitute_image_placeholders(text: str) -> str:
    """把 【图片:filename】 占位替换为 cache 中已解析的描述（只替换 success）"""
    if not text or "【图片:" not in text:
        return text
    filenames = set(IMAGE_PLACEHOLDER_RE.findall(text))
    descs = {}
    for fn in filenames:
        info = await image_cache.get_image(fn)
        if info and info["status"] == "success" and info["description"]:
            descs[fn] = info["description"]
    if not descs:
        return text
    return IMAGE_PLACEHOLDER_RE.sub(
        lambda m: (
            f"【图片:{m.group(1)}】{descs[m.group(1)]}"
            if m.group(1) in descs
            else m.group(0)
        ),
        text,
    )


async def build_prompt(
    group_id: str,
    user_id: str,
    current_msg: str,
    plan: dict = None,
    history_text: str = None,
) -> str:
    """
    组装主 prompt。plan/history_text 可由调用方传入（chat.py 等待队列复用，
    避免 route 和 history 组装跑两次）。
    """
    from core.router import route
    from core.scene import get_current_scene
    from core.preference import get_relevant_preferences
    from services.user_manager import get_nickname

    if history_text is None:
        history_text = await get_history_text(group_id, user_id)
    if plan is None:
        plan = await route(current_msg, history_text)

    lines = []

    if plan.get("time"):
        lines.append(f"【现在】{get_current_scene()}")

    if plan.get("preference"):
        prefs = await get_relevant_preferences(current_msg)
        if prefs:
            lines.append("【你的喜好】")
            for p in prefs:
                lines.append(f"  - {p}")

    if plan.get("scene") and group_id:
        from services.embedding import embed_text
        from services.vector_store import search_scenes

        try:
            query_vector = await embed_text(current_msg)
            scenes = await search_scenes(group_id, query_vector, top_k=3)
            if scenes:
                lines.append("【之前聊过】")
                for s in scenes:
                    summary = await substitute_image_placeholders(s["summary"])
                    lines.append(f"  - {summary}")
        except Exception as e:
            print(f"[情景检索失败] {e}")
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT summary FROM scenes WHERE group_id = ? ORDER BY end_time DESC LIMIT 3",
                    (group_id,),
                ) as cur:
                    rows = await cur.fetchall()
            if rows:
                lines.append("【之前聊过】")
                for r in rows:
                    lines.append(f"  - {r[0]}")

    if history_text:
        history_text = await substitute_image_placeholders(history_text)
        lines.append(f"【刚才】\n{history_text}")

    current_msg = await substitute_image_placeholders(current_msg)
    nick = await get_nickname(user_id)
    lines.append(f'{nick}说："{current_msg}"')
    lines.append("直接回复，不要解释你在干嘛。")

    return "\n".join(lines)
