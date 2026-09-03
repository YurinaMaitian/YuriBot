import asyncio
import aiohttp
from core.ai import get_token, refresh_token


async def send_split_message(url: str, content: str, msg_id: str):
    """发送回复，自动分条，Token过期自动刷新"""
    if not content or not content.strip():
        print("[警告] 尝试发送空消息")
        return

    token = await get_token()
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) == 1:
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]

    final_chunks = []
    for p in paragraphs:
        if len(p) > 900:
            while p:
                final_chunks.append(p[:800])
                p = p[800:].strip()
        else:
            final_chunks.append(p)

    for i, chunk in enumerate(final_chunks, start=1):
        payload = {"content": chunk, "msg_type": 0, "msg_id": msg_id, "msg_seq": i}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as r:
                resp = await r.json()

                if r.status == 401 and "AccessToken无效" in str(resp):
                    print("[Token] 过期，自动刷新...")
                    new_token = await refresh_token()
                    headers["Authorization"] = f"QQBot {new_token}"
                    async with session.post(url, headers=headers, json=payload) as r2:
                        print(f"[发送{i}/{len(final_chunks)}] 重试状态:{r2.status}")
                else:
                    print(
                        f"[发送{i}/{len(final_chunks)}] {chunk[:40]}... 状态:{r.status}"
                    )

        if i < len(final_chunks):
            await asyncio.sleep(0.5)
