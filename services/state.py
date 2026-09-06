import json
import os

STATE_FILE = "/home/minds/qqbot/data/bot_state.json"
_state_cache = None


def load_state():
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            _state_cache = json.load(f)
    else:
        _state_cache = {"global_enabled": True, "groups": {}}
    return _state_cache


def is_reply_at_enabled(state) -> bool:
    """回@功能开关（被@后回复时@回去）。默认开启"""
    return bool(state.get("reply_at_enabled", True))


def save_state(state):
    global _state_cache
    _state_cache = state
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_group_enabled(state, group_id: str) -> bool:
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
    groups = list(state["groups"].keys())
    if 1 <= idx <= len(groups):
        return groups[idx - 1]
    return None
