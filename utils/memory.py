from collections import deque
from typing import List
from utils.db import save_message

_hot_cache = {}


def _get_key(group_id: str, user_id: str) -> str:
    if not group_id:
        return f"c2c:{user_id}"
    return group_id


def record_message(group_id: str, user_id: str, speaker: str, content: str):
    print(
        f"[record] key={_get_key(group_id, user_id)[:20]}, speaker={speaker}, content={content[:30]}"
    )
    key = _get_key(group_id, user_id)
    if key not in _hot_cache:
        _hot_cache[key] = deque(maxlen=50)

    display = "你" if speaker == "bot" else "对方"
    _hot_cache[key].append({"speaker": display, "content": content[:200]})

    db_speaker_id = "yuribot" if speaker == "bot" else user_id
    save_message(group_id, speaker, db_speaker_id, content)


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
