import asyncio
import json
import uvicorn
import handlers.admin
import handlers.chat
import handlers.owner
from services.vector_store import init_collection
import tools.latex
from fastapi import FastAPI, Request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import APP_ID, APP_SECRET, BOT_OPENID
from handlers.chat import handle_chat
from core.registry import get_handler, get_help, auto_discover, get_cmd_list
from core.context import CmdContext
from core.memory import record_message
from services.db import init_db
from services.state import load_state, is_group_enabled, set_group_state
from services.user_manager import get_nickname, get_group_name
from utils.scene_manager import check_and_update_scene, init_scenes_table
from core.ai import refresh_token
from services.actions import send_text

app = FastAPI()

_processed_ids = set()
_dedup_lock = asyncio.Lock()


async def is_duplicate(msg_id: str) -> bool:
    async with _dedup_lock:
        if msg_id in _processed_ids:
            return True
        _processed_ids.add(msg_id)
        if len(_processed_ids) > 1000:
            _processed_ids.clear()
        return False


def generate_signature(event_ts: str, plain_token: str) -> str:
    seed = APP_SECRET.encode("utf-8")
    if not seed:
        raise ValueError("APP_SECRET is empty")
    if len(seed) < 32:
        seed = (seed * (32 // len(seed) + 1))[:32]
    else:
        seed = seed[:32]
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    message = (event_ts + plain_token).encode("utf-8")
    signature = private_key.sign(message)
    return signature.hex()


async def process_event(data: dict):
    event = data.get("t")
    d = data.get("d", {})
    msg_id = d.get("id")

    if msg_id and await is_duplicate(msg_id):
        print(f"[去重] 跳过重复消息 {msg_id[:8]}")
        return

    print(
        f"[process_event] event={event}, group_id={d.get('group_openid')}, "
        f"user_id={d.get('author', {}).get('member_openid') or d.get('author', {}).get('id')}"
    )

    if event == "C2C_MESSAGE_CREATE":
        user_id = d["author"]["id"]
        content = d.get("content", "").strip()
        msg_id = d["id"]
        nick = await get_nickname(user_id)
        print(f"[私聊] {nick}: {content}")

        await record_message("", user_id, "user", content)

        if content.startswith("/"):
            parts = content.split(maxsplit=1)
            cmd_name = parts[0][1:]
            raw = parts[1] if len(parts) > 1 else ""
            args = raw.split() if raw else []

            handler = get_handler(cmd_name)
            if not handler:
                reply = f"❓ 未知指令: /{cmd_name}，可用: {get_cmd_list()}"
                await send_text("", user_id, reply, msg_id, is_group=False)
                return

            ctx = CmdContext(
                group_id="",
                user_id=user_id,
                msg_id=msg_id,
                is_group=False,
                cmd=cmd_name,
                args=args,
                raw=raw,
                state=load_state(),
            )
            result = await handler(ctx)
            if isinstance(result, str):
                await send_text("", user_id, result, msg_id, is_group=False)
            return

        reply = await handle_chat(content, user_id=user_id, group_id="")
        await send_text("", user_id, reply, msg_id, is_group=False)

    elif event == "GROUP_AT_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        content = d.get("content", "").strip()
        msg_id = d["id"]

        clean_content = _extract_at_content(content)
        nick = await get_nickname(user_id)
        gname = await get_group_name(group_id)
        print(f"[群聊@] {gname} | {nick}: {clean_content}")

        state = load_state()
        if group_id not in state["groups"]:
            set_group_state(state, group_id, None)

        if not is_group_enabled(state, group_id):
            await send_text(
                group_id, user_id, "⏸️ 当前群 Bot 未开启", msg_id, is_group=True
            )
            return

        await record_message(group_id, user_id, "user", clean_content)
        await check_and_update_scene(group_id, user_id, "user", clean_content)

        if clean_content.startswith("/"):
            parts = clean_content.split(maxsplit=1)
            cmd_name = parts[0][1:]
            raw = parts[1] if len(parts) > 1 else ""
            args = raw.split() if raw else []

            handler = get_handler(cmd_name)
            if not handler:
                reply = f"❓ 未知指令: /{cmd_name}，可用: {get_cmd_list()}"
                await send_text(group_id, user_id, reply, msg_id, is_group=True)
                return

            ctx = CmdContext(
                group_id=group_id,
                user_id=user_id,
                msg_id=msg_id,
                is_group=True,
                cmd=cmd_name,
                args=args,
                raw=raw,
                state=load_state(),
            )
            result = await handler(ctx)
            if isinstance(result, str):
                await send_text(group_id, user_id, result, msg_id, is_group=True)
            return
        else:
            reply = await handle_chat(clean_content, user_id=user_id, group_id=group_id)
            await send_text(group_id, user_id, reply, msg_id, is_group=True)
            await check_and_update_scene(group_id, user_id, "bot", reply)

    elif event == "GROUP_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        content = d.get("content", "").strip()
        msg_id = d["id"]

        is_at_bot, clean_content = _detect_at_bot(content)

        state = load_state()
        if group_id not in state["groups"]:
            set_group_state(state, group_id, None)

        if not is_group_enabled(state, group_id):
            return

        msg_to_record = clean_content if is_at_bot else content
        await record_message(group_id, user_id, "user", msg_to_record)
        await check_and_update_scene(group_id, user_id, "user", msg_to_record)

        if not is_at_bot:
            if "yuri" not in content.lower() and "bot" not in content.lower():
                return

        nick = await get_nickname(user_id)
        gname = await get_group_name(group_id)
        print(f"[群聊{'@' if is_at_bot else '免@'}] {gname} | {nick}: {clean_content}")

        if clean_content.startswith("/"):
            parts = clean_content.split(maxsplit=1)
            cmd_name = parts[0][1:]
            raw = parts[1] if len(parts) > 1 else ""
            args = raw.split() if raw else []

            handler = get_handler(cmd_name)
            if not handler:
                reply = f"❓ 未知指令: /{cmd_name}，可用: {get_cmd_list()}"
                await send_text(group_id, user_id, reply, msg_id, is_group=True)
                return

            ctx = CmdContext(
                group_id=group_id,
                user_id=user_id,
                msg_id=msg_id,
                is_group=True,
                cmd=cmd_name,
                args=args,
                raw=raw,
                state=load_state(),
            )
            result = await handler(ctx)
            if isinstance(result, str):
                await send_text(group_id, user_id, result, msg_id, is_group=True)
            return
        else:
            reply = await handle_chat(clean_content, user_id=user_id, group_id=group_id)
            await send_text(group_id, user_id, reply, msg_id, is_group=True)
            await check_and_update_scene(group_id, user_id, "bot", reply)


def _extract_at_content(content: str) -> str:
    if content.startswith("<@"):
        end_idx = content.find(">")
        if end_idx != -1:
            mentioned_id = content[2:end_idx]
            if mentioned_id == BOT_OPENID:
                return content[end_idx + 1 :].strip()
    return content


def _detect_at_bot(content: str):
    if content.startswith("<@"):
        end_idx = content.find(">")
        if end_idx != -1:
            mentioned_id = content[2:end_idx]
            if mentioned_id == BOT_OPENID:
                return True, content[end_idx + 1 :].strip()
    return False, content


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
    await init_db()
    await init_scenes_table()

    try:
        await init_collection()
    except Exception as e:
        print(f"[启动] Qdrant 初始化失败（将降级到 SQLite）: {e}")

    auto_discover("tools")
    auto_discover("handlers")
    asyncio.create_task(token_refresh_loop())


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
