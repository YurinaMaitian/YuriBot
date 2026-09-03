"""
统一动作层：发送消息/图片的同时，自动记入 Bot 自己的记忆
"""
from utils.memory import record_message
from utils.ai import get_token
from utils.media import upload_image, send_image as _send_image_raw

async def send_text(group_id: str, user_id: str, content: str, msg_id: str, send_reply_func, is_group: bool = True):
    """
    发送文字，并记入记忆
    """
    url = (
        f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
        if is_group else
        f"https://api.sgroup.qq.com/v2/users/{user_id}/messages"
    )
    await send_reply_func(url, content, msg_id)
    
    # 记入记忆：Bot 自己说了这句话
    target_id = group_id if is_group else user_id
    record_message(target_id, user_id, "bot", content)

async def send_image(group_id: str, user_id: str, image_path: str, description: str, msg_id: str, is_group: bool = True):
    """
    发送图片，并记入记忆（描述自己做了什么）
    """
    token = await get_token()
    target_id = group_id if is_group else user_id
    
    file_info = await upload_image(token, target_id, image_path, is_group=is_group)
    if file_info:
        await _send_image_raw(token, target_id, file_info, msg_id, is_group=is_group)
        # 记入记忆：Bot 生成了图片
        record_message(target_id, user_id, "bot", f"[发送了图片：{description}]")
        return True
    return False
