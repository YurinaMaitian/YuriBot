from utils.ai import get_ai_reply


async def handle_chat(content: str):
    return await get_ai_reply(content)
