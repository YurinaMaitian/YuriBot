"""
气氛站状态：router register 模块的群级缓存。
shadow 期（MOOD_AIR_ENABLED=false）只记录不消费；日志验证判定质量后再开闸。
"""

import time

_state: dict[str, tuple[str, float]] = {}  # group_id → (register, 时间戳)
TTL = 600  # 气氛持续10分钟；超时回 normal（防陈旧沉重误伤新话题）


def set_register(group_id: str, register: str):
    _state[group_id] = (register, time.time())


def get_register(group_id: str) -> str:
    hit = _state.get(group_id)
    if not hit:
        return "normal"
    reg, ts = hit
    if time.time() - ts > TTL:
        return "normal"
    return reg
