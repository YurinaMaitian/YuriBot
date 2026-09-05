import json
import re
from core.ai import get_ai_reply
from config import (
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    LIGHT_MODEL_KEY,
    LIGHT_MODEL_MAX_TOKENS,
    LIGHT_MODEL_TEMP,
)

ROUTER_SYSTEM = """你是信息调度员。根据用户消息，判断回复前需要检索哪些信息。

可选模块：
- time：时间情境（几点、在干嘛）
- preference：Bot 的喜好/雷点（番剧、角色、食物等）
- scene：情景记忆（之前聊过什么相关话题）

输出严格 JSON，不要解释，不要思考过程：
{"time": true/false, "preference": true/false, "scene": true/false}

判断规则：
- 问"在干嘛""现在""在吗" → time
- 问"你喜欢什么""推荐番""xx怎么样" → preference
- 提到具体作品/角色/食物名 → preference
- 追问"刚才""之前""你不是说" → scene
- 普通闲聊 → 可能只需要 time"""


from core.registry import cmd


def _route_rule(user_msg: str) -> dict:
    m = user_msg.lower()

    # scene：追问历史
    if any(
        k in m
        for k in [
            "刚才",
            "之前",
            "你不是说",
            "刚刚聊",
            "上午",
            "昨天",
            "上次",
            "之前聊",
            "之前说",
            "之前谁",
        ]
    ):
        return {"time": True, "preference": False, "scene": True}

    # preference：喜好/推荐/评价
    if any(
        k in m
        for k in [
            "喜欢",
            "推荐",
            "怎么样",
            "推什么",
            "本命",
            "最爱",
            "讨厌",
            "雷",
            "你觉得",
            "如何",
            "好吗",
            "喜欢什么",
        ]
    ):
        return {"time": True, "preference": True, "scene": False}

    # time：默认兜底
    return {"time": True, "preference": False, "scene": False}


async def route(user_msg: str) -> dict:
    """判断需要检索哪些模块。纯规则引擎，零成本，100% 稳定。"""
    result = _route_rule(user_msg)
    print(f"[Router] 规则命中: {result}, 消息:{user_msg[:30]}")
    return result
