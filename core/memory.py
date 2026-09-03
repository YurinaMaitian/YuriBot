import aiosqlite
from collections import deque
from typing import List
from services.db import save_message, DB_PATH

_hot_cache = {}


def _get_key(group_id: str, user_id: str) -> str:
    if not group_id:
        return f"c2c:{user_id}"
    return group_id


async def _lazy_load(key: str, group_id: str, user_id: str):
    from services.user_manager import get_nickname

    async with aiosqlite.connect(DB_PATH) as db:
        if group_id:
            async with db.execute(
                """
                SELECT speaker, speaker_id, content FROM messages
                WHERE group_id = ? ORDER BY created_at DESC LIMIT 20
            """,
                (group_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT speaker, speaker_id, content FROM messages
                WHERE group_id = '' AND (speaker_id = ? OR speaker_id = 'yuribot')
                ORDER BY created_at DESC LIMIT 20
            """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

    if rows:
        _hot_cache[key] = deque(maxlen=50)
        for speaker, speaker_id, content in reversed(rows):
            if speaker == "bot":
                identity = "YuriBot"
            else:
                identity = await get_nickname(speaker_id)
            _hot_cache[key].append(
                {"speaker": speaker, "identity": identity, "content": content[:200]}
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
        {"speaker": speaker, "identity": identity, "content": content[:200]}
    )

    db_speaker_id = "yuribot" if speaker == "bot" else user_id
    await save_message(group_id, speaker, db_speaker_id, content)


def get_context(group_id: str, user_id: str) -> List[dict]:
    key = _get_key(group_id, user_id)
    return list(_hot_cache.get(key, []))


async def build_prompt(group_id: str, user_id: str, current_msg: str) -> str:
    from core.router import route
    from core.scene import get_current_scene
    from core.preference import get_relevant_preferences
    from services.user_manager import get_nickname

    plan = await route(current_msg)
    print(f"[Router] 计划: {plan}")

    lines = []

    if plan.get("time"):
        scene = get_current_scene()
        lines.append(f"【现在】{scene}")

    if plan.get("preference"):
        prefs = await get_relevant_preferences(current_msg)
        if prefs:
            lines.append("【你的喜好】")
            for p in prefs:
                lines.append(f"  - {p}")

    ctx = get_context(group_id, user_id)
    if ctx:
        history_lines = [f"{m['identity']}：{m['content']}" for m in ctx]
        all_text = "\n".join(history_lines)
        if len(all_text) < 600:
            history = all_text
        else:
            recent = history_lines[-15:] if len(history_lines) >= 15 else history_lines
            history = "\n".join(recent)
        lines.append(f"【刚才】\n{history}")

    nick = await get_nickname(user_id)
    lines.append(f'{nick}说："{current_msg}"')
    lines.append("直接回复，不要解释你在干嘛。")

    return "\n".join(lines)
