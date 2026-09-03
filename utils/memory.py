from collections import deque
from typing import List
from utils.db import save_message, DB_PATH
import sqlite3

_hot_cache = {}


def _get_key(group_id: str, user_id: str) -> str:
    if not group_id:
        return f"c2c:{user_id}"
    return group_id


def record_message(group_id: str, user_id: str, speaker: str, content: str):
    key = _get_key(group_id, user_id)

    # 懒加载：如果缓存为空（刚重启），从数据库恢复最近20条
    if key not in _hot_cache or len(_hot_cache[key]) == 0:
        _lazy_load(key, group_id, user_id)

    if key not in _hot_cache:
        _hot_cache[key] = deque(maxlen=50)

    display = "你" if speaker == "bot" else "对方"
    _hot_cache[key].append({"speaker": display, "content": content[:200]})

    db_speaker_id = "yuribot" if speaker == "bot" else user_id
    save_message(group_id, speaker, db_speaker_id, content)


def _lazy_load(key: str, group_id: str, user_id: str):
    """从 SQLite 恢复最近 20 条消息到热缓存"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT speaker, content FROM messages
        WHERE group_id = ? AND (speaker_id = ? OR speaker_id = 'yuribot')
        ORDER BY created_at DESC LIMIT 20
    """,
        (group_id or "", user_id),
    )

    rows = cursor.fetchall()
    conn.close()

    if rows:
        _hot_cache[key] = deque(maxlen=50)
        for speaker, content in reversed(rows):
            display = "你" if speaker == "bot" else "对方"
            _hot_cache[key].append({"speaker": display, "content": content[:200]})
        print(f"[缓存恢复] key={key[:20]}, 恢复{len(rows)}条")


def get_context(group_id: str, user_id: str) -> List[dict]:
    key = _get_key(group_id, user_id)
    return list(_hot_cache.get(key, []))


def build_prompt(group_id: str, user_id: str, current_msg: str) -> str:
    ctx = get_context(group_id, user_id)
    print(f"[prompt] key={_get_key(group_id, user_id)[:20]}, 缓存条数={len(ctx)}")
    if not ctx:
        return current_msg

    lines = [f"{m['speaker']}：{m['content']}" for m in ctx]
    all_text = "\n".join(lines)

    if len(all_text) < 800:
        history = all_text
    else:
        recent = lines[-20:] if len(lines) >= 20 else lines
        history = "\n".join(recent)

    return (
        f"以下是对话记录：\n{history}\n"
        f"---\n现在对方说：{current_msg}\n"
        f"请回复。注意上面'你：'开头的都是你自己之前说过的话。"
    )


def load_recent_from_db(group_id: str, user_id: str, limit: int = 20):
    """启动时从 SQLite 恢复最近 N 条到热缓存"""
    key = _get_key(group_id, user_id)
    if key in _hot_cache:
        return  # 已有缓存，不覆盖

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT speaker, content FROM messages
        WHERE group_id = ? AND speaker_id = ?
        ORDER BY created_at DESC LIMIT ?
    """,
        (group_id or "", user_id, limit),
    )

    rows = cursor.fetchall()
    conn.close()

    if rows:
        _hot_cache[key] = deque(maxlen=50)
        # 按时间正序插入（数据库是倒序查的）
        for speaker, content in reversed(rows):
            display = "你" if speaker == "bot" else "对方"
            _hot_cache[key].append({"speaker": display, "content": content[:200]})
        print(f"[缓存恢复] key={key[:20]}, 恢复{len(rows)}条")
