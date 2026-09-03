import base64
import aiohttp

async def upload_image(token: str, target_id: str, image_path: str, is_group: bool = True) -> str:
    """
    上传图片到 QQ，返回 file_info
    """
    # 读取图片并转 base64
    with open(image_path, "rb") as f:
        img_base64 = base64.b64encode(f.read()).decode()
    
    # 选择上传接口
    if is_group:
        upload_url = f"https://api.sgroup.qq.com/v2/groups/{target_id}/files"
    else:
        upload_url = f"https://api.sgroup.qq.com/v2/users/{target_id}/files"
    
    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "file_type": 1,        # 1=图片
        "file_data": img_base64,
        "srv_send_msg": False  # 只上传，不直接发
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(upload_url, headers=headers, json=payload) as r:
            data = await r.json()
            if "file_info" in data:
                return data["file_info"]
            else:
                print(f"[上传图片失败] {data}")
                return None

async def send_image(token: str, target_id: str, file_info: str, msg_id: str, is_group: bool = True):
    """
    发送图片消息
    """
    if is_group:
        url = f"https://api.sgroup.qq.com/v2/groups/{target_id}/messages"
    else:
        url = f"https://api.sgroup.qq.com/v2/users/{target_id}/messages"
    
    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "msg_type": 7,  # 富媒体消息（图片）
        "msg_id": msg_id,
        "msg_seq": 1,
        "media": {
            "file_info": file_info
        }
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as r:
            result = await r.json()
            print(f"[发送图片] 状态:{r.status}, 返回:{result}")
            return result
