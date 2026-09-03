import json
from utils.ai import get_ai_reply

ROUTER_SYSTEM = """你是信息调度员。根据用户消息，判断回复前需要检索哪些信息。

可选模块：
- time：时间情境（几点、在干嘛）
- preference：Bot 的喜好/雷点（番剧、角色、食物等）
- scene：情景记忆（之前聊过什么相关话题）

输出严格 JSON，不要解释：
{"time": true/false, "preference": true/false, "scene": true/false}

判断规则：
- 问"在干嘛""现在""在吗" → time
- 问"你喜欢什么""推荐番""xx怎么样" → preference
- 提到具体作品/角色/食物名 → preference
- 追问"刚才""之前""你不是说" → scene
- 普通闲聊 → 可能只需要 time"""

async def route(user_msg: str) -> dict:
    """判断需要检索哪些模块"""
    prompt = f"用户消息：{user_msg}\n\n输出JSON："
    
    raw = await get_ai_reply(
        user_message=prompt,
        system_override=ROUTER_SYSTEM,
        max_tokens=100
    )
    
    try:
        clean = raw.strip().strip("```json").strip("```").strip()
        result = json.loads(clean)
        # 确保字段存在
        return {
            "time": result.get("time", True),
            "preference": result.get("preference", False),
            "scene": result.get("scene", False)
        }
    except Exception as e:
        print(f"[Router解析失败] {e}, 原始返回:{raw}")
        # 兜底：全量加载 time，其他不加载
        return {"time": True, "preference": False, "scene": False}
