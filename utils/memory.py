import sqlite3
from collections import deque
from typing import List
from utils.db import save_message, DB_PATH

_hot_cache = {}

def _get_key(group_id: str, user_id: str) -> str:
    if not group_id:
        return f"c2c:{user_id}"
    return group_id

def _lazy_load(key: str, group_id: str, user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT speaker, content FROM messages
        WHERE group_id = ? AND (speaker_id = ? OR speaker_id = 'yuribot')
        ORDER BY created_at DESC LIMIT 20
    ''', (group_id or "", user_id))
    rows = cursor.fetchall()
    conn.close()
    if rows:
        _hot_cache[key] = deque(maxlen=50)
        for speaker, content in reversed(rows):
            display = "你" if speaker == "bot" else "对方"
            _hot_cache[key].append({
                "speaker": display,
                "content": content[:200]
            })
        print(f"[缓存恢复] key={key[:20]}, 恢复{len(rows)}条")

def record_message(group_id: str, user_id: str, speaker: str, content: str):
    key = _get_key(group_id, user_id)
    if key not in _hot_cache or len(_hot_cache[key]) == 0:
        _lazy_load(key, group_id, user_id)
    if key not in _hot_cache:
        _hot_cache[key] = deque(maxlen=50)
    display = "你" if speaker == "bot" else "对方"
    _hot_cache[key].append({
        "speaker": display,
        "content": content[:200]
    })
    db_speaker_id = "yuribot" if speaker == "bot" else user_id
    save_message(group_id, speaker, db_speaker_id, content)

def get_context(group_id: str, user_id: str) -> List[dict]:
    key = _get_key(group_id, user_id)
    return list(_hot_cache.get(key, []))

async def build_prompt(group_id: str, user_id: str, current_msg: str) -> str:
    """按需组装 prompt（async，因为 Router 要调 API）"""
    from utils.router import route
    from utils.scene import get_current_scene
    from utils.preference import get_relevant_preferences
    
    # Step 1: Router 判断
    plan = await route(current_msg)
    print(f"[Router] 计划: {plan}")
    
    lines = []
    
    # Step 2: 按需加载
    if plan.get("time"):
        scene = get_current_scene()
        lines.append(f"【现在】{scene}")
    
    if plan.get("preference"):
        prefs = get_relevant_preferences(current_msg)
        if prefs:
            lines.append("【你的喜好】")
            for p in prefs:
                lines.append(f"  - {p}")
    
    # Step 3: 短期上下文（始终加载，但控制长度）
    ctx = get_context(group_id, user_id)
    if ctx:
        history_lines = [f"{m['speaker']}：{m['content']}" for m in ctx]
        all_text = "\n".join(history_lines)
        if len(all_text) < 600:
            history = all_text
        else:
            recent = history_lines[-15:] if len(history_lines) >= 15 else history_lines
            history = "\n".join(recent)
        lines.append(f"【刚才】\n{history}")
    
    lines.append(f"对方说：\"{current_msg}\"")
    lines.append("直接回复，不要解释你在干嘛。")
    
    return "\n".join(lines)
