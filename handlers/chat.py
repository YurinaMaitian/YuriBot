from core.ai import get_ai_reply


async def handle_chat(content: str, user_id: str = "", group_id: str = "") -> str:
    return await get_ai_reply(content, user_id=user_id, group_id=group_id)
