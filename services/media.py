import base64
import aiohttp
from services.http import get_session


async def upload_image(
    token: str, target_id: str, image_path: str, is_group: bool = True
) -> str:
    with open(image_path, "rb") as f:
        img_base64 = base64.b64encode(f.read()).decode()

    upload_url = (
        f"https://api.sgroup.qq.com/v2/groups/{target_id}/files"
        if is_group
        else f"https://api.sgroup.qq.com/v2/users/{target_id}/files"
    )

    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    payload = {"file_type": 1, "file_data": img_base64, "srv_send_msg": False}

    session = get_session()
    async with session.post(upload_url, headers=headers, json=payload, timeout=30) as r:
        data = await r.json()
        if "file_info" in data:
            return data["file_info"]
        else:
            print(f"[上传图片失败] {data}")
            return None


async def send_image(
    token: str, target_id: str, file_info: str, msg_id: str, is_group: bool = True
):
    url = (
        f"https://api.sgroup.qq.com/v2/groups/{target_id}/messages"
        if is_group
        else f"https://api.sgroup.qq.com/v2/users/{target_id}/messages"
    )

    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    payload = {
        "msg_type": 7,
        "msg_id": msg_id,
        "msg_seq": 1,
        "media": {"file_info": file_info},
    }

    session = get_session()
    async with session.post(url, headers=headers, json=payload, timeout=30) as r:
        result = await r.json()
        print(f"[发送图片] 状态:{r.status}, 返回:{result}")
        return result
