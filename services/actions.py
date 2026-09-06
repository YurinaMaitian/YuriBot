from core.memory import record_message
from core.ai import get_token
from core.messenger import send_split_message
from services.media import upload_image, send_image as _send_image_raw


from services.state import load_state, is_reply_at_enabled


async def send_text(
    group_id: str,
    user_id: str,
    content: str,
    msg_id: str,
    is_group: bool = True,
    memory_tag: str = "",
    at_user: str = "",
):
    """即发通道：指令/系统回复用（整段连续发，不排队不做气泡）。"""
    url = (
        f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
        if is_group
        else f"https://api.sgroup.qq.com/v2/users/{user_id}/messages"
    )

    at_id = (
        at_user if (at_user and is_group and is_reply_at_enabled(load_state())) else ""
    )
    await send_split_message(url, content, msg_id, at_user_id=at_id)

    target_id = group_id if is_group else user_id
    await record_message(target_id, user_id, "bot", f"{memory_tag}{content}")


async def send_text_chat(
    group_id: str,
    user_id: str,
    content: str,
    msg_id: str,
    is_group: bool = True,
    memory_tag: str = "",
    at_user: str = "",
    priority: bool = False,
):
    """
    人设对话发送：进发送队列（串行 + 打字节奏 + 气泡化）。
    priority=True 插到队首（@回复有人等）；False 排队（插话回复）。
    """
    from services.sender import enqueue_chat

    at_id = (
        at_user if (at_user and is_group and is_reply_at_enabled(load_state())) else ""
    )
    await enqueue_chat(
        group_id,
        user_id,
        content,
        msg_id,
        is_group=is_group,
        at_user=at_id,
        priority=priority,
        memory_tag=memory_tag,
    )


async def send_image(
    group_id: str,
    user_id: str,
    image_path: str,
    description: str,
    msg_id: str,
    is_group: bool = True,
):
    token = await get_token()
    target_id = group_id if is_group else user_id

    file_info = await upload_image(token, target_id, image_path, is_group=is_group)
    if file_info:
        await _send_image_raw(token, target_id, file_info, msg_id, is_group=is_group)
        await record_message(target_id, user_id, "bot", f"[发送了图片：{description}]")
        return True
    return False
