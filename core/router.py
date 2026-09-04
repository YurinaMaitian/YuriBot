import json
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

输出严格 JSON，不要解释：
{"time": true/false, "preference": true/false, "scene": true/false}

判断规则：
- 问"在干嘛""现在""在吗等" → time
- 问"你喜欢什么""推荐番""xx怎么样"等 → preference
- 提到具体作品/角色/食物名等 → preference
- 追问"刚才""之前""你不是说"等 → scene
- 普通闲聊 → 可能只需要 time"""


async def route(user_msg: str) -> dict:
    prompt = f"用户消息：{user_msg}\n\n输出JSON："

    raw = await get_ai_reply(
        user_message=prompt,
        system_override=ROUTER_SYSTEM,
        max_tokens=LIGHT_MODEL_MAX_TOKENS,
        temperature=LIGHT_MODEL_TEMP,
        model=LIGHT_MODEL_NAME,
        api_url=LIGHT_MODEL_URL,
        api_key=LIGHT_MODEL_KEY,
    )

    try:
        clean = raw.strip().strip("```json").strip("```").strip()
        result = json.loads(clean)
        return {
            "time": result.get("time", True),
            "preference": result.get("preference", False),
            "scene": result.get("scene", False),
        }
    except Exception as e:
        print(f"[Router解析失败] {e}, 原始返回:{raw}")
        return {"time": True, "preference": False, "scene": False}
