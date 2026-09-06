import asyncio
import json
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.user_manager import get_nickname, get_group_name, normalize_mentions

# 副作用导入：触发 @cmd 装饰器注册
import handlers.admin  # noqa: F401
import handlers.chat  # noqa: F401
import handlers.owner  # noqa: F401
import tools.latex  # noqa: F401

from config import APP_ID, APP_SECRET, BOT_OPENID, ENABLE_INTERJECT
from services.interject import maybe_interject, init_interject_table

from config import APP_ID, APP_SECRET, BOT_OPENID
from handlers.chat import handle_chat
from core.dispatcher import handle_command
from core.registry import auto_discover
from core.memory import record_message
from services.db import init_db
from services.state import load_state, is_group_enabled, set_group_state
from services.user_manager import get_nickname, get_group_name
from services import image_cache
from services.image_cache import init_image_cache_table
from utils.scene_manager import (
    check_and_update_scene,
    init_scenes_table,
    scene_scan_loop,
)
from services.vector_store import init_collection
from core.ai import refresh_token
from services.actions import send_text, send_text_chat
from collections import deque

import hashlib
import re

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_URL_RE = re.compile(r"https?://\S+")
_TRIGGER_BOT_RE = re.compile(r"(?<![a-z0-9])bot(?![a-z])", re.IGNORECASE)


# ========== 消息去重（FIFO 淘汰，满了不清空） ==========
_processed_ids = set()
_processed_order = deque(maxlen=1000)
_dedup_lock = asyncio.Lock()


async def is_duplicate(msg_id: str) -> bool:
    async with _dedup_lock:
        if msg_id in _processed_ids:
            return True
        if len(_processed_order) == 1000:
            _processed_ids.discard(_processed_order[0])
        _processed_order.append(msg_id)
        _processed_ids.add(msg_id)
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
    """去掉消息里所有 @ 段（群聊@事件里消息以 <@Bot> 开头，可能还@了别人）"""
    return re.sub(r"<@[^>]*>", "", content).strip()


def _detect_at_bot(content: str):
    """
    检测消息中是否 @Bot（任意位置，支持多人同时@）。
    返回 (是否@Bot, 清理掉所有@段后的内容)
    """
    if "<@" not in content:
        return False, content

    found = False
    for m in re.finditer(r"<@[^>]*>", content):
        oid = m.group(0)[2:-1].lstrip("!")
        if oid == BOT_OPENID:
            found = True
    cleaned = re.sub(r"<@[^>]*>", "", content).strip()
    return found, cleaned


def _extract_quote(d: dict) -> tuple[str, list[dict]]:
    elements = d.get("msg_elements") or []
    quote_text = ""
    quote_images: list[dict] = []
    for el in elements:
        if el.get("message_type") != 103:
            continue
        if el.get("content"):
            quote_text = el["content"]
        for a in el.get("attachments") or []:
            if a.get("content_type", "").startswith("image/"):
                quote_images.append(a)
    return quote_text, quote_images


def _strip_face_tags(content: str) -> str:
    """把 <faceType=...> 标签全部替换为 [表情]（可能有多个）"""
    while "<faceType=" in content:
        start = content.find("<faceType=")
        end = content.find(">", start)
        if end == -1:
            break
        content = content[:start] + "[表情]" + content[end + 1 :]
    return content


def _extract_content_with_attachments(d: dict) -> tuple[str, list[dict]]:
    """
    提取消息内容 + 图片附件。
    支持：纯文字、纯图片、文字+图片混合（attachments 或 markdown 内嵌）。
    返回：(处理后的文本, 图片列表)
    """
    content = _strip_face_tags(d.get("content", "").strip())
    attachments = d.get("attachments", [])

    quote_text, quote_images = _extract_quote(d)
    if quote_text:
        content = f"【引用的消息】{quote_text}\n{content}".strip()
    attachments = list(attachments) + quote_images

    images = [a for a in attachments if a.get("content_type", "").startswith("image/")]

    # QQ 混合消息：图片以 markdown 形式内嵌在 content 里
    md_images: list[dict] = []

    def _md_to_placeholder(m: re.Match) -> str:
        url = m.group(1)
        md_images.append(
            {
                "url": url,
                "filename": hashlib.md5(url.encode()).hexdigest() + ".png",
                "content_type": "image/png",
            }
        )
        return "[图片]"

    content = _MD_IMAGE_RE.sub(_md_to_placeholder, content)
    images.extend(md_images)

    # 处理表情包标签
    if "<faceType=" in content:
        start = content.find("<faceType=")
        end = content.find(">", start)
        if end != -1:
            content = content[:start] + "[表情]" + content[end + 1 :]

    if images and not content:
        content = "[图片]"

    return content, images


def _has_trigger_word(raw_content: str) -> bool:
    """触发词判断：先剥 URL 防误触，词边界匹配 bot"""
    text = _URL_RE.sub("", raw_content).lower()
    return "yuri" in text or bool(_TRIGGER_BOT_RE.search(text))


async def _process_images(content: str, images: list[dict]) -> str:
    """
    把图片附件替换为 【图片:filename】 占位符，后台异步解析。
    主流程不阻塞，描述在 prompt 组装时从 image_cache 查最新状态替换。
    """
    if not images:
        return content

    from services.vision import describe_image

    user_text = content  # OCR 判断用原文

    for img in images:
        url = img.get("url", "")
        filename = img.get("filename", "unknown")
        mime = img.get("content_type", "image/jpeg")
        if not url:
            continue

        placeholder = f"【图片:{filename}】"
        if "[图片]" in content:
            content = content.replace("[图片]", placeholder, 1)
        else:
            content += f" {placeholder}"

        # 收到即登记：关闭"发图后毫秒级追问查无此图"的竞态
        # 已 success 的（重复表情包）不动，保住去重
        info = await image_cache.get_image(filename)
        if not (info and info["status"] == "success"):
            await image_cache.mark_pending(filename)

        asyncio.create_task(describe_image(url, filename, mime, user_text=user_text))

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
        content = await _process_images(content, images)

        nick = await get_nickname(user_id)
        print(f"[私聊] {nick}: {content[:120]}")

        # 命令：打标签进记忆，不进情景（私聊本来就没有情景）
        if content.startswith("/"):
            await handle_command(content, user_id, "", msg_id, is_group=False)
            return

        # 非命令：进记忆，走 AI
        await record_message("", user_id, "user", content)

        reply = await handle_chat(
            content, user_id=user_id, group_id="", msg_id=msg_id, is_group=False
        )
        if reply is None:
            print("[静默丢弃] 私聊图片等待超时/超龄")
            return
        await send_text("", user_id, reply, msg_id, is_group=False)

    # ---------- 群聊 @ ----------
    elif event == "GROUP_AT_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        raw_content = d.get("content", "").strip()

        # 先去掉 @Bot
        clean_content = _extract_at_content(raw_content)

        clean_content = await normalize_mentions(clean_content)

        # 提取 attachments + 引用 + 图片占位（后台解析，不阻塞）
        clean_content, images = _extract_content_with_attachments(
            {
                "content": clean_content,
                "attachments": d.get("attachments", []),
                "msg_elements": d.get("msg_elements", []),
            }
        )
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

        # 命令：打标签进记忆，不进情景
        if clean_content.startswith("/"):
            await handle_command(
                clean_content, user_id, group_id, msg_id, is_group=True
            )
            return

        # 非命令：进记忆 + 情景
        await record_message(group_id, user_id, "user", clean_content)
        await check_and_update_scene(group_id, user_id, "user", clean_content)

        # 防抖合并：1.5s 内同群新消息会取消本次回复，以最后一条的 msg_id 统一回复
        from core.debounce import schedule

        async def _do_reply():
            await send_text_chat(
                group_id,
                user_id,
                reply,
                msg_id,
                is_group=True,
                at_user=user_id,
                priority=True,
            )
            if reply is None:
                return
            await send_text(
                group_id, user_id, reply, msg_id, is_group=True, at_user=user_id
            )
            await check_and_update_scene(group_id, user_id, "bot", reply)

        schedule(f"{group_id}:{user_id}", _do_reply)

    elif event == "GROUP_MESSAGE_CREATE":
        group_id = d["group_openid"]
        user_id = d["author"]["member_openid"]
        raw_content = d.get("content", "").strip()

        is_at_bot, clean_content = _detect_at_bot(raw_content)

        # 提取 attachments
        clean_content, images = _extract_content_with_attachments(
            {
                "content": clean_content,
                "attachments": d.get("attachments", []),
                "msg_elements": d.get("msg_elements", []),
            }
        )

        clean_content = await normalize_mentions(clean_content)

        # 图片占位（后台解析）：@Bot 记清理后内容，免@ 记原始内容
        base_content = clean_content if is_at_bot else raw_content
        base_content = await normalize_mentions(base_content)
        base_content = _strip_face_tags(base_content)
        msg_to_record = await _process_images(base_content, images)

        # 群状态管理
        state = load_state()
        if group_id not in state["groups"]:
            set_group_state(state, group_id, None)

        if not is_group_enabled(state, group_id):
            return

        if not is_at_bot:
            triggered = _has_trigger_word(raw_content)
            if not triggered:
                await record_message(group_id, user_id, "user", msg_to_record)
                await check_and_update_scene(group_id, user_id, "user", msg_to_record)
                # 主动插话判断：异步，不阻塞；延续对话/引用场景 judge 先验拉高
                if ENABLE_INTERJECT:
                    has_quote = any(
                        el.get("message_type") == 103
                        for el in d.get("msg_elements") or []
                    )
                    asyncio.create_task(
                        maybe_interject(
                            group_id, user_id, msg_to_record, msg_id, has_quote
                        )
                    )
                return

        nick = await get_nickname(user_id)
        gname = await get_group_name(group_id)
        print(
            f"[群聊{'@' if is_at_bot else '免@'}] {gname} | {nick}: {msg_to_record[:120]}"
        )

        # 命令：打标签进记忆，不进情景
        if clean_content.startswith("/"):
            await handle_command(
                clean_content, user_id, group_id, msg_id, is_group=True
            )
            return

        # 非命令：进记忆 + 情景，走 AI（用带图片占位的 msg_to_record）
        await record_message(group_id, user_id, "user", msg_to_record)
        await check_and_update_scene(group_id, user_id, "user", msg_to_record)

        from core.debounce import schedule

        async def _do_reply():
            await send_text_chat(
                group_id,
                user_id,
                reply,
                msg_id,
                is_group=True,
                at_user=user_id,
                priority=True,
            )
            if reply is None:
                return
            await send_text(
                group_id, user_id, reply, msg_id, is_group=True, at_user=user_id
            )
            await check_and_update_scene(group_id, user_id, "bot", reply)

        schedule(f"{group_id}:{user_id}", _do_reply)


# ========== FastAPI 生命周期 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_scenes_table()
    await init_image_cache_table()
    await init_interject_table()
    await init_collection()
    auto_discover("tools")
    auto_discover("handlers")

    # 注册完成后跑自检（必须在 auto_discover 之后，否则注册表还没填）
    from services.selfcheck import run_self_check

    run_self_check()

    asyncio.create_task(token_refresh_loop())
    asyncio.create_task(scene_scan_loop())
    from services.daily_schedule import schedule_ensure_loop

    asyncio.create_task(schedule_ensure_loop())

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
