import json
import os

STATE_FILE = "/home/minds/qqbot/data/bot_state.json"

def load_state():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"global_enabled": True, "groups": {}}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_group_enabled(state, group_id: str) -> bool:
    """检查某个群是否启用。群单独设置优先，未设置则跟随全局。"""
    group_setting = state["groups"].get(group_id)
    if group_setting is not None:
        return group_setting
    return state.get("global_enabled", True)

def set_group_state(state, group_id: str, enabled: bool):
    state["groups"][group_id] = enabled
    save_state(state)

def set_global_state(state, enabled: bool):
    state["global_enabled"] = enabled
    save_state(state)

def get_group_by_index(state, idx: int):
    """按序号获取群 openid（从1开始）"""
    groups = list(state["groups"].keys())
    if 1 <= idx <= len(groups):
        return groups[idx - 1]
    return None
