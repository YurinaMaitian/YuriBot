import asyncio
import aiohttp
from core.ai import get_token, refresh_token


async def send_split_message(url: str, content: str, msg_id: str, at_user_id: str = ""):
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
        if i == 1 and at_user_id:
            # 回@：文本消息不解析 at 标签，必须走 markdown 通道
            payload = {
                "msg_type": 2,
                "markdown": {"content": f'<qqbot-at-user id="{at_user_id}" />{chunk}'},
                "msg_id": msg_id,
                "msg_seq": i,
            }
        else:
            payload = {"content": chunk, "msg_type": 0, "msg_id": msg_id, "msg_seq": i}

        # ……以下原有的发送/重试逻辑不动        # ……以下原有发送/重试逻辑不动
        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as r:
                    resp = await r.json()

                    if r.status == 401 and "AccessToken无效" in str(resp):
                        print("[Token] 过期，自动刷新...")
                        headers["Authorization"] = f"QQBot {await refresh_token()}"
                        continue  # 用新 token 重试

                    if r.status in (500, 502, 503):
                        print(f"[发送{i}] 服务端{r.status}，重试...")
                        await asyncio.sleep(0.5)
                        continue

                    if r.status != 200:
                        print(
                            f"[发送{i}/{len(final_chunks)}] 失败:{r.status} {str(resp)[:150]}"
                        )

                    break  # 成功或 4xx 都不重试（4xx 重试无意义）
