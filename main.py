import asyncio
import time
import json
import aiohttp
import uvicorn
from collections import deque
from fastapi import FastAPI, Request, Header
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from config import APP_ID, APP_SECRET, TOKEN_URL, BOT_OPENID
from handlers.admin import handle_on, handle_off, handle_status, state
from handlers.chat import handle_chat
from utils.state import get_group_by_index, is_group_enabled, save_state
from utils.memory import record_message
from utils.db import init_db

app = FastAPI()

current_token = None

# ========== 短期上下文缓存 ==========
# key: user_id(私聊) 或 group_id(群聊) -> deque(maxlen=15)
group_contexts = {}


def record_context(key: str, user_id: str, content: str):
    """记录消息到短期上下文"""
    if key not in group_contexts:
        group_contexts[key] = deque(maxlen=15)
    group_contexts[key].append({"user": user_id[:8], "content": content[:100]})


def build_prompt(key: str, current_msg: str) -> str:
    """组装带上下文的 prompt"""
    ctx = group_contexts.get(key)
    if not ctx:
        return current_msg

    # 取最近 8 条，避免太长
    history = "\n".join([f"{m['user']}: {m['content']}" for m in list(ctx)[-8:]])

    return f"以下是对话上下文：\n{history}\n---\n现在问你：{current_msg}"


# ========== Token 管理 ==========
async def refresh_token():
    global current_token
    async with aiohttp.ClientSession() as session:
        r = await session.post(
            TOKEN_URL, json={"appId": APP_ID, "clientSecret": APP_SECRET}
        )
        data = await r.json()
        if "access_token" not in data:
            raise Exception(f"Token失败: {data}")
        current_token = data["access_token"]
        print(f"[Token] 已刷新: {current_token[:10]}...")
        return current_token


async def get_token():
    if current_token is None:
        return await refresh_token()
    return current_token


async def send_reply(url: str, content: str, msg_id: str):
    """发送回复，自动分条，Token过期自动刷新"""
    if not content or not content.strip():
        print(f"[警告] 尝试发送空消息")
        return

    token = await get_token()
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}

    # 按段落拆分
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

    # 逐条发送
    for i, chunk in enumerate(final_chunks, start=1):
        payload = {"content": chunk, "msg_type": 0, "msg_id": msg_id, "msg_seq": i}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as r:
                resp = await r.json()

                # ========== 关键修复：Token过期自动刷新重试 ==========
                if r.status == 401 and "AccessToken无效" in str(resp):
                    print("[Token] 过期，自动刷新...")
                    global current_token
                    current_token = await refresh_token()

                    # 重试一次
                    headers["Authorization"] = f"QQBot {current_token}"
                    async with session.post(url, headers=headers, json=payload) as r2:
                        print(f"[发送{i}/{len(final_chunks)}] 重试状态:{r2.status}")
                else:
                    print(
                        f"[发送{i}/{len(final_chunks)}] {chunk[:40]}... 状态:{r.status}"
                    )

        if i < len(final_chunks):
            await asyncio.sleep(0.5)


async def dispatch_command(cmd: str, user_id: str, target=None) -> str:
    if cmd == "on":
        return await handle_on(user_id, target)
    elif cmd == "off":
        return await handle_off(user_id, target)
    elif cmd == "status":
        return await handle_status(user_id)
    else:
        return f"❓ 未知指令: /{cmd}\n可用: /on /off /status"


# ========== 事件处理 ==========
async def process_event(data: dict):
    event = data.get("t")
    d = data.get("d", {})
    print(
        f"[process_event] event={event}, group_id={d.get('group_openid')}, user_id={d.get('author', {}).get('member_openid') or d.get('author', {}).get('id')}"
    )

    # ---------- 私聊 ----------
    if event == "C2C_MESSAGE_CREATE":
        user_id = d["author"]["id"]
        content = d.get("content", "").strip()
        msg_id = d["id"]
        print(f"[私聊] {user_id}: {content}")

        # 记录用户消息
        record_message("", user_id, "user", content)

        if content.startswith("/"):
            parts = content.split()
            cmd = parts[0][1:]
            target = None
            if len(parts) > 1:
                arg = parts[1]
                if arg.isdigit():
                    target = get_group_by_index(state, int(arg))
                    if target is None:
                        reply = "❌ 序号不存在，用 /status 查看列表"
                        url = f"https://api.sgroup.qq.com/v2/users/{user_id}/messages"
                        await send_reply(url, reply, msg_id)
                        return
                else:
                    target = arg
            reply = await dispatch_command(cmd, user_id, target)
        else:
            reply = await handle_chat(content, user_id=user_id, group_id="")

        url = f"https://api.sgroup.qq.com/v2/users/{user_id}/messages"
        await send_reply(url, reply, msg_id)

        # 记录 Bot 自己的回复
        record_message("", user_id, "bot", reply)

    # ---------- 群聊 @ ----------
    elif event == "GROUP_AT_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        content = d.get("content", "").strip()
        msg_id = d["id"]

        # 去掉 @<id> 前缀
        if content.startswith("<@"):
            end_idx = content.find(">")
            if end_idx != -1:
                mentioned_id = content[2:end_idx]
                if mentioned_id == BOT_OPENID:
                    clean_content = content[end_idx + 1 :].strip()
                else:
                    clean_content = content
            else:
                clean_content = content
        else:
            clean_content = content

        print(f"[群聊@] {group_id}: {clean_content}")

        # 记录群
        if group_id not in state["groups"]:
            state["groups"][group_id] = None
            save_state(state)

        # 检查开关
        if not is_group_enabled(state, group_id):
            url = f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
            await send_reply(url, "⏸️ 当前群 Bot 未开启", msg_id)
            return

        # 记录用户消息
        record_message(group_id, user_id, "user", clean_content)

        if clean_content.startswith("/"):
            parts = clean_content.split()
            cmd = parts[0][1:]
            reply = await dispatch_command(cmd, user_id)
        else:
            reply = await handle_chat(clean_content, user_id=user_id, group_id=group_id)

        url = f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
        await send_reply(url, reply, msg_id)

        # 记录 Bot 自己的回复
        record_message(group_id, user_id, "bot", reply)

    # ---------- 群聊免@ ----------
    elif event == "GROUP_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        content = d.get("content", "").strip()
        msg_id = d["id"]

        # 1. 记录群
        if group_id not in state["groups"]:
            state["groups"][group_id] = None
            save_state(state)

        # 2. 检查开关
        if not is_group_enabled(state, group_id):
            return

        # 3. 判断是否是 @Bot（精确匹配）
        is_at_bot = False
        clean_content = content

        if content.startswith("<@"):
            end_idx = content.find(">")
            if end_idx != -1:
                mentioned_id = content[2:end_idx]
                if mentioned_id == BOT_OPENID:
                    is_at_bot = True
                    clean_content = content[end_idx + 1 :].strip()

        # 4. 所有消息都 push 进记忆（不管@不@）
        # 用 clean_content 记（去掉@前缀后的实际内容）
        record_context(group_id, user_id, clean_content if is_at_bot else content)

        # 5. 决定是否回答
        if not is_at_bot:
            # 免@模式：只有含关键词才回答
            if "yuri" not in content.lower() and "bot" not in content.lower():
                return  # 不回答，但记忆已记录

        print(f"[群聊{'@' if is_at_bot else '免@'}] {group_id}: {clean_content}")

        # 6. 回答
        if clean_content.startswith("/"):
            parts = clean_content.split()
            cmd = parts[0][1:]
            reply = await dispatch_command(cmd, user_id)
        else:
            prompt = build_prompt(group_id, clean_content)
            reply = await handle_chat(prompt, user_id=user_id, group_id=group_id)

        url = f"https://api.sgroup.qq.com/v2/groups/{group_id}/messages"
        await send_reply(url, reply, msg_id)

        # 记录 Bot 自己的回复
        record_message(group_id, user_id, "bot", reply)


# ========== Webhook 签名验证 ==========
def generate_signature(event_ts: str, plain_token: str) -> str:
    seed = APP_SECRET.encode("utf-8")
    while len(seed) < 32:
        seed += seed
    seed = seed[:32]
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    message = (event_ts + plain_token).encode("utf-8")
    signature = private_key.sign(message)
    return signature.hex()


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    data = json.loads(body)
    op = data.get("op")
    d = data.get("d", {})

    print(f"[Webhook] 收到 op={op}, event={data.get('t')}")

    if op == 13:
        plain_token = d.get("plain_token", "")
        event_ts = d.get("event_ts", "")
        signature = generate_signature(event_ts, plain_token)
        return {"plain_token": plain_token, "signature": signature}

    if op == 0:
        # 用 create_task 不阻塞响应，但加异常捕获
        async def safe_process(data):
            try:
                await process_event(data)
            except Exception as e:
                print(f"[处理异常] {type(e).__name__}: {e}")
                import traceback

                traceback.print_exc()

        asyncio.create_task(safe_process(data))

    return ""


@app.get("/health")
async def health():
    return {"status": "ok"}


async def token_refresh_loop():
    while True:
        try:
            await refresh_token()
        except Exception as e:
            print(f"[Token刷新失败] {e}")
        await asyncio.sleep(60 * 60)


@app.on_event("startup")
async def startup():
    init_db()  # 初始化数据库
    asyncio.create_task(token_refresh_loop())


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
