import asyncio
import aiohttp
from core.ai import get_token, refresh_token
from services.http import get_session


async def send_split_message(
    url: str,
    content: str,
    msg_id: str,
    at_user_id: str = "",
    msg_seq_start: int = 1,
):
    """
    发送回复，自动分条，Token过期自动刷新；markdown@失败自动降级纯文本。
    msg_seq_start：同一 msg_id 的多泡回复从第几号开始（seq 必须递增，否则平台去重报错）。
    """
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
        while len(p) > 900:
            final_chunks.append(p[:800])
            p = p[800:].strip()
        if p:
            final_chunks.append(p)

    session = get_session()

    for i, chunk in enumerate(final_chunks, start=1):
        seq = msg_seq_start + i - 1
        # 第一条且需要@：文本消息不解析 at 标签，必须走 markdown 通道
        use_markdown_at = bool(i == 1 and at_user_id)
        if use_markdown_at:
            payload = {
                "msg_type": 2,
                "markdown": {"content": f'<qqbot-at-user id="{at_user_id}" />{chunk}'},
                "msg_id": msg_id,
                "msg_seq": seq,
            }
        else:
            payload = {
                "content": chunk,
                "msg_type": 0,
                "msg_id": msg_id,
                "msg_seq": seq,
            }

        for attempt in range(2):
            async with session.post(
                url, headers=headers, json=payload, timeout=15
            ) as r:
                resp = await r.json()

                if r.status == 401 and "AccessToken无效" in str(resp):
                    print("[Token] 过期，自动刷新...")
                    headers["Authorization"] = f"QQBot {await refresh_token()}"
                    continue

                if r.status in (500, 502, 503):
                    print(f"[发送seq{seq}] 服务端{r.status}，重试...")
                    await asyncio.sleep(0.5)
                    continue

                if r.status != 200:
                    print(f"[发送seq{seq}] 失败:{r.status} {str(resp)[:150]}")
                    # 平台已有同(msg_id,msg_seq)消息：视为送达，不重试不降级
                    if resp.get("err_code") == 40054005:
                        break
                    # markdown 通道不可用（无权限/模板被拒）→ 降级纯文本发内容，丢弃@
                    if use_markdown_at and r.status in (400, 403, 404):
                        print("[发送] markdown通道不可用，降级为纯文本")
                        payload = {
                            "content": chunk,
                            "msg_type": 0,
                            "msg_id": msg_id,
                            "msg_seq": seq,
                        }
                        use_markdown_at = False
                        await asyncio.sleep(0.3)
                        continue

                break  # 成功或 4xx 都不重试（4xx 重试无意义）
