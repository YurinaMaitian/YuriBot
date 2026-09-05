import json
import re
from core.ai import get_ai_reply
from config import (
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    LIGHT_MODEL_KEY,
)

ROUTER_SYSTEM = """你是信息调度员。根据用户消息，判断回复前需要检索哪些信息。

可选模块：
- time：时间情境（几点、在干嘛）
- preference：Bot 的喜好/雷点（番剧、角色、食物等）
- scene：情景记忆（之前聊过什么相关话题）

输出严格 JSON，不要解释，不要思考过程，不要 markdown 代码块：
{"time": true/false, "preference": true/false, "scene": true/false}

判断规则：
- 问"在干嘛""现在""在吗" → time
- 问"你喜欢什么""推荐番""xx怎么样" → preference
- 提到具体作品/角色/食物名 → preference
- 追问"刚才""之前""你不是说" → scene
- 普通闲聊 → 可能只需要 time"""


def _route_rule(user_msg: str) -> dict:
    """规则兜底：LLM 不可用或输出无法解析时使用"""
    m = user_msg.lower()

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
            "你觉得",
            "如何",
            "好吗",
        ]
    ):
        return {"time": True, "preference": True, "scene": False}

    return {"time": True, "preference": False, "scene": False}


def _parse_router_json(raw: str) -> dict | None:
    """从模型输出里抠出 JSON 计划并校验"""
    if not raw:
        return None
    m = re.search(r"\{[^{}]*\}", raw)
    if not m:
        return None
    try:
        plan = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(plan, dict):
        return None
    # 校验字段，缺省补 False，过滤未知键
    keys = ("time", "preference", "scene")
    if not any(k in plan for k in keys):
        return None
    return {k: bool(plan.get(k, False)) for k in keys}


async def route(user_msg: str) -> dict:
    """
    LLM 路由（Qwen3-8B，免费）+ 规则兜底。
    LLM 调用失败 / 输出非 JSON / 字段异常时，退回纯规则。
    """
    try:
        raw = await get_ai_reply(
            user_message=user_msg,
            system_override=ROUTER_SYSTEM,
            max_tokens=100,
            temperature=0.0,
            model=LIGHT_MODEL_NAME,
            api_url=LIGHT_MODEL_URL,
            api_key=LIGHT_MODEL_KEY,
        )
        plan = _parse_router_json(raw)
        if plan:
            print(f"[Router] LLM命中: {plan}, 消息:{user_msg[:30]}")
            return plan
        print(f"[Router] LLM输出无法解析: {raw[:80]}")
    except Exception as e:
        print(f"[Router] LLM异常: {type(e).__name__}: {e}")

    plan = _route_rule(user_msg)
    print(f"[Router] 规则兜底: {plan}, 消息:{user_msg[:30]}")
    return plan
