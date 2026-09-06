"""
发送队列（她的"嘴"）：每群串行调度拟人化回复 + 表情包图片。

- 文本 job：分泡 + 打字节奏（同前）
- 图片 job：排在文字气泡之后，作为回合收尾发送
"""

import asyncio
import random
import re
import time

from config import (
    SEND_BUBBLE_MAX,
    SEND_JOB_MAX_WAIT,
    SEND_PACE_BASE,
    SEND_PACE_JITTER,
    SEND_PACE_PER_CHAR,
    SEND_TURN_GAP,
)
from core.messenger import send_split_message
from core.memory import record_message


class _SendJob:
    __slots__ = (
        "group_id",
        "user_id",
        "chunks",
        "msg_id",
        "at_user",
        "is_group",
        "memory_tag",
        "image_path",
        "image_desc",
        "enqueued_at",
    )

    def __init__(
        self,
        group_id,
        user_id,
        chunks,
        msg_id,
        at_user,
        is_group,
        memory_tag,
        image_path="",
        image_desc="",
    ):
        self.group_id = group_id
        self.user_id = user_id
        self.chunks = chunks
        self.msg_id = msg_id
        self.at_user = at_user
        self.is_group = is_group
        self.memory_tag = memory_tag
        self.image_path = image_path
        self.image_desc = image_desc
        self.enqueued_at = time.time()


class _GroupQ:
    """带优先级的小队列：高优先级插队首"""

    def __init__(self):
        self.items: list[tuple[int, _SendJob]] = []
        self.event = asyncio.Event()

    def put(self, job: _SendJob, priority: bool):
        if priority:
            self.items.insert(0, (1, job))
        else:
            self.items.append((0, job))
        self.event.set()

    async def get(self) -> _SendJob:
        while True:
            if self.items:
                return self.items.pop(0)[1]
            self.event.clear()
            await self.event.wait()


_queues: dict[str, _GroupQ] = {}
_running: set[str] = set()
_seq_cursor: dict[str, int] = {}
_SPLIT_RE = re.compile(r"(?<=[。！？!?…~])")


def _next_seq(msg_id: str) -> int:
    n = _seq_cursor.get(msg_id, 0) + 1
    _seq_cursor[msg_id] = n
    if len(_seq_cursor) > 2000:  # 防无限增长（被动回复msg_id本就会过期，直接清空重来）
        _seq_cursor.clear()
        _seq_cursor[msg_id] = n
    return n


def pack_bubbles(content: str) -> list[str]:
    """按句末标点切句打包成拟人气泡；超 SEND_BUBBLE_MAX 合并末泡"""
    text = content.strip()
    if not text:
        return []
    sentences: list[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        sentences.extend(s.strip() for s in _SPLIT_RE.split(para) if s.strip())
    if not sentences:
        return [text]

    bubbles: list[str] = []
    cur = ""
    for s in sentences:
        if not cur:
            cur = s
        elif len(cur) + len(s) <= 30:
            cur += s
        else:
            bubbles.append(cur)
            cur = s
    if cur:
        bubbles.append(cur)

    if len(bubbles) > SEND_BUBBLE_MAX:
        head = bubbles[: SEND_BUBBLE_MAX - 1]
        tail = "".join(bubbles[SEND_BUBBLE_MAX - 1 :])
        bubbles = head + [tail]
    return bubbles


def _ensure_worker(group_id: str, q: _GroupQ):
    if group_id not in _running:
        _running.add(group_id)
        asyncio.create_task(_worker(group_id, q))


async def enqueue_chat(
    group_id: str,
    user_id: str,
    content: str,
    msg_id: str,
    is_group: bool = True,
    at_user: str = "",
    priority: bool = False,
    memory_tag: str = "",
):
    """人设对话文本：分泡入队"""
    chunks = pack_bubbles(content)
    if not chunks:
        return
    q = _queues.setdefault(group_id, _GroupQ())
    q.put(
        _SendJob(group_id, user_id, chunks, msg_id, at_user, is_group, memory_tag),
        priority=priority,
    )
    _ensure_worker(group_id, q)


async def enqueue_image(
    group_id: str,
    user_id: str,
    image_path: str,
    description: str,
    msg_id: str,
    is_group: bool = True,
):
    """表情包图片：作为独立 job 排在队列里（自然落在文字气泡之后）"""
    q = _queues.setdefault(group_id, _GroupQ())
    q.put(
        _SendJob(
            group_id,
            user_id,
            [],
            msg_id,
            "",
            is_group,
            "",
            image_path=image_path,
            image_desc=description,
        ),
        priority=False,
    )
    _ensure_worker(group_id, q)


async def _worker(group_id: str, q: _GroupQ):
    try:
        while True:
            job = await q.get()
            await _deliver_turn(job)
    finally:
        _running.discard(group_id)


async def _deliver_turn(job: _SendJob):
    waited = time.time() - job.enqueued_at
    if waited > SEND_JOB_MAX_WAIT:
        print(f"[发送队列] 丢弃过期任务(等待{waited:.0f}s)")
        return

    if job.image_path:
        await _deliver_image(job)
        return

    url = (
        f"https://api.sgroup.qq.com/v2/groups/{job.group_id}/messages"
        if job.is_group
        else f"https://api.sgroup.qq.com/v2/users/{job.user_id}/messages"
    )

    for i, chunk in enumerate(job.chunks):
        if i > 0:
            delay = (
                SEND_PACE_BASE
                + len(chunk) * SEND_PACE_PER_CHAR
                + random.uniform(-SEND_PACE_JITTER, SEND_PACE_JITTER)
            )
            await asyncio.sleep(max(0.8, delay))
        # @ 只挂首泡；同 msg_id 内 msg_seq 按泡递增（1,2,3…），否则平台去重报错
        at = job.at_user if (i == 0 and job.is_group) else ""
        await send_split_message(
            url, chunk, job.msg_id, at_user_id=at, msg_seq_start=_next_seq(job.msg_id)
        )
        await record_message(
            job.group_id, job.user_id, "bot", f"{job.memory_tag}{chunk}"
        )
    await asyncio.sleep(SEND_TURN_GAP)


async def _deliver_image(job: _SendJob):
    """图片 job：上传 + 发送 + 记忆"""
    from core.ai import get_token
    from services.media import send_image as raw_send_image
    from services.media import upload_image

    token = await get_token()
    target_id = job.group_id if job.is_group else job.user_id
    try:
        file_info = await upload_image(
            token, target_id, job.image_path, is_group=job.is_group
        )
        if file_info:
            await raw_send_image(
                token,
                target_id,
                file_info,
                job.msg_id,
                is_group=job.is_group,
                msg_seq=_next_seq(job.msg_id),
            )
            await record_message(
                job.group_id,
                job.user_id,
                "bot",
                f"[发送了图片：{job.image_desc[:40]}]",
            )
    except Exception as e:
        print(f"[发送队列] 图片发送失败: {type(e).__name__}: {e}")
    await asyncio.sleep(SEND_TURN_GAP)
