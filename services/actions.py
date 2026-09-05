from core.memory import record_message
from core.ai import get_token
from core.messenger import send_split_message
from services.media import upload_image, send_image as _send_image_raw


async def send_text(
    group_id: str,
    user_id: str,
    content: str,
    msg_id: str,
    is_group: bool = True,
    memory_tag: str = "",
):
    url = (
        f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
        if is_group
        else f"https://api.sgroup.qq.com/v2/users/{user_id}/messages"
    )
    await send_split_message(url, content, msg_id)

    target_id = group_id if is_group else user_id
    await record_message(target_id, user_id, "bot", f"{memory_tag}{content}")


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
