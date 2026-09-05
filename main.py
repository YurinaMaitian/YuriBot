import asyncio
import json
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# 副作用导入：触发 @cmd 装饰器注册
import handlers.admin  # noqa: F401
import handlers.chat  # noqa: F401
import handlers.owner  # noqa: F401
import tools.latex  # noqa: F401

from config import APP_ID, APP_SECRET, BOT_OPENID
from handlers.chat import handle_chat
from core.registry import get_handler, get_cmd_list, auto_discover
from core.context import CmdContext
from core.memory import record_message
from services.db import init_db
from services.state import load_state, is_group_enabled, set_group_state
from services.user_manager import get_nickname, get_group_name
from services.image_cache import init_image_cache_table
from utils.scene_manager import check_and_update_scene, init_scenes_table
from services.vector_store import init_collection
from core.ai import refresh_token
from services.actions import send_text

# ========== 消息去重（加锁） ==========
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


# ========== 签名验证 ==========
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


# ========== 内容提取工具 ==========
def _extract_at_content(content: str) -> str:
    """去掉 @Bot 前缀"""
    if content.startswith("<@"):
        end_idx = content.find(">")
        if end_idx != -1:
            mentioned_id = content[2:end_idx]
            if mentioned_id == BOT_OPENID:
                return content[end_idx + 1 :].strip()
    return content


def _detect_at_bot(content: str):
    """检测是否 @Bot，返回 (是否@, 清理后的内容)"""
    if content.startswith("<@"):
        end_idx = content.find(">")
        if end_idx != -1:
            mentioned_id = content[2:end_idx]
            if mentioned_id == BOT_OPENID:
                return True, content[end_idx + 1 :].strip()
    return False, content


def _extract_content_with_attachments(d: dict) -> tuple[str, list[dict]]:
    """
    提取消息内容 + 图片附件。
    支持：纯文字、纯图片、文字+图片混合。
    返回：(处理后的文本, 图片列表)
    """
    content = d.get("content", "").strip()
    attachments = d.get("attachments", [])

    # 过滤图片附件
    images = [a for a in attachments if a.get("content_type", "").startswith("image/")]

    # 处理表情包标签（替换成 [表情]，方便模型理解）
    if "<faceType=" in content:
        start = content.find("<faceType=")
        end = content.find(">", start)
        if end != -1:
            content = content[:start] + "[表情]" + content[end + 1 :]

    # 如果有图片但没有文字，给个占位符
    if images and not content:
        content = "[图片]"

    return content, images


async def _process_images(content: str, images: list[dict]) -> str:
    """
    识别图片内容，把 [图片] 占位符替换成 【图片】描述。
    支持同一条消息里有多个图片。
    """
    if not images:
        return content

    from services.vision import describe_image

    # 逐个识别，替换占位符
    # 策略：每识别一张，替换掉第一个 [图片]
    for img in images:
        url = img.get("url", "")
        filename = img.get("filename", "unknown")
        mime = img.get("content_type", "image/jpeg")

        if not url:
            continue

        desc = await describe_image(url, filename, mime)

        # 替换第一个 [图片] 为 【图片】描述
        if "[图片]" in content:
            content = content.replace("[图片]", f"【图片：{desc}】", 1)
        else:
            # 如果没有占位符（比如混合消息文字在前），追加到末尾
            content += f" 【图片：{desc}】"

    return content


# ========== 事件处理 ==========
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

    # ---------- 私聊 ----------
    if event == "C2C_MESSAGE_CREATE":
        user_id = d["author"]["id"]
        content, images = _extract_content_with_attachments(d)
        msg_id = d["id"]

        # 识别图片
        content = await _process_images(content, images)

        nick = await get_nickname(user_id)
        print(f"[私聊] {nick}: {content[:120]}")

        # 记录到记忆（包含图片描述）
        await record_message("", user_id, "user", content)

        # 命令处理
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

        # AI 聊天
        reply = await handle_chat(content, user_id=user_id, group_id="")
        await send_text("", user_id, reply, msg_id, is_group=False)

    # ---------- 群聊 @ ----------
    elif event == "GROUP_AT_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        raw_content = d.get("content", "").strip()

        # 先去掉 @Bot
        clean_content = _extract_at_content(raw_content)

        # 提取 attachments（从原始消息里取）
        clean_content, images = _extract_content_with_attachments(
            {"content": clean_content, "attachments": d.get("attachments", [])}
        )

        # 识别图片
        clean_content = await _process_images(clean_content, images)

        nick = await get_nickname(user_id)
        gname = await get_group_name(group_id)
        print(f"[群聊@] {gname} | {nick}: {clean_content[:120]}")

        # 群状态管理
        state = load_state()
        if group_id not in state["groups"]:
            set_group_state(state, group_id, None)

        if not is_group_enabled(state, group_id):
            await send_text(
                group_id, user_id, "⏸️ 当前群 Bot 未开启", msg_id, is_group=True
            )
            return

        # 记录记忆 + 更新场景
        await record_message(group_id, user_id, "user", clean_content)
        await check_and_update_scene(group_id, user_id, "user", clean_content)

        # 命令处理
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

        # AI 聊天
        reply = await handle_chat(clean_content, user_id=user_id, group_id=group_id)
        await send_text(group_id, user_id, reply, msg_id, is_group=True)
        await check_and_update_scene(group_id, user_id, "bot", reply)

    # ---------- 群聊免@ ----------
    elif event == "GROUP_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        raw_content = d.get("content", "").strip()

        is_at_bot, clean_content = _detect_at_bot(raw_content)

        # 提取 attachments
        clean_content, images = _extract_content_with_attachments(
            {"content": clean_content, "attachments": d.get("attachments", [])}
        )

        # 识别图片
        clean_content = await _process_images(clean_content, images)

        # 群状态管理
        state = load_state()
        if group_id not in state["groups"]:
            set_group_state(state, group_id, None)

        if not is_group_enabled(state, group_id):
            return

        # 决定记录什么内容
        msg_to_record = clean_content if is_at_bot else raw_content
        # 如果免@ 但带了图片，也要识别后记录
        if not is_at_bot and images:
            raw_with_images = await _process_images(raw_content, images)
            msg_to_record = raw_with_images

        await record_message(group_id, user_id, "user", msg_to_record)
        await check_and_update_scene(group_id, user_id, "user", msg_to_record)

        # 免@ 触发词过滤
        if not is_at_bot:
            if "yuri" not in raw_content.lower() and "bot" not in raw_content.lower():
                return

        nick = await get_nickname(user_id)
        gname = await get_group_name(group_id)
        print(
            f"[群聊{'@' if is_at_bot else '免@'}] {gname} | {nick}: {clean_content[:120]}"
        )

        # 命令处理
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

        # AI 聊天
        reply = await handle_chat(clean_content, user_id=user_id, group_id=group_id)
        await send_text(group_id, user_id, reply, msg_id, is_group=True)
        await check_and_update_scene(group_id, user_id, "bot", reply)


# ========== FastAPI 生命周期 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_scenes_table()
    await init_image_cache_table()
    await init_collection()
    auto_discover("tools")
    auto_discover("handlers")
    asyncio.create_task(token_refresh_loop())
    yield


app = FastAPI(lifespan=lifespan)


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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
