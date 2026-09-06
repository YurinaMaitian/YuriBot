"""
发送队列（她的"嘴"）：每群串行调度拟人化回复。

设计：
- 生成端并行不变，发送端强制串行——一次只说一个回合
- 优先级：@回复（有人等）> 插话回复；高优先级插到队首
- 分泡：按句末标点切句，打包 10~30 字气泡；每回合 ≤SEND_BUBBLE_MAX，
  超出合并进末泡（防热情刷屏）
- 节奏：首泡立即，后续泡按"打字时间"（基础+字数×系数±抖动）
- 回合间换气；任务排队超 SEND_JOB_MAX_WAIT 丢弃（msg_id 时效保护）
- 只服务人设对话；指令/系统回复走 actions.send_text 即发通道，不进队
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
        "enqueued_at",
    )

    def __init__(
        self, group_id, user_id, chunks, msg_id, at_user, is_group, memory_tag
    ):
        self.group_id = group_id
        self.user_id = user_id
        self.chunks = chunks
        self.msg_id = msg_id
        self.at_user = at_user
        self.is_group = is_group
        self.memory_tag = memory_tag
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

_SPLIT_RE = re.compile(r"(?<=[。！？!?…~])")


def pack_bubbles(content: str) -> list[str]:
    """
    把回复打包成拟人气泡：
    - 按句末标点切句，短句合并成 10~30 字的气泡
    - 总泡数超上限 → 多余部分合并进末泡
    - 单句回复 → 原样单泡
    """
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
    """人设对话发送入口：分泡入队，由 worker 按节奏逐泡发送并逐泡进记忆"""
    import traceback

    dup = [
        j
        for q in _queues.values()
        for _, j in q.items
        if j.msg_id == msg_id and j.chunks and j.chunks[0][:15] == content.strip()[:15]
    ]
    if dup:
        print(f"[发送队列] 疑似重复入队: msg_id={msg_id}, content={content[:20]!r}")
        traceback.print_stack()

    chunks = pack_bubbles(content)
    if not chunks:
        return
    q = _queues.setdefault(group_id, _GroupQ())
    q.put(
        _SendJob(group_id, user_id, chunks, msg_id, at_user, is_group, memory_tag),
        priority=priority,
    )
    if group_id not in _running:
        _running.add(group_id)
        asyncio.create_task(_worker(group_id, q))


async def _worker(group_id: str, q: _GroupQ):
    try:
        while True:
            job = await q.get()
            await _deliver_turn(job)
    finally:
        _running.discard(group_id)


async def _deliver_turn(job: _SendJob):
    print(
        f"[发送队列] 回合开始: {len(job.chunks)}泡, 等了{time.time() - job.enqueued_at:.1f}s, 首泡={job.chunks[0][:20]!r}"
    )
    waited = time.time() - job.enqueued_at
    if waited > SEND_JOB_MAX_WAIT:
        print(f"[发送队列] 丢弃过期任务(等待{waited:.0f}s): {job.chunks[0][:20]!r}")
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
        # @ 只挂在首泡；单泡发送（msg_seq=1），气泡各自独立
        # @ 只挂在首泡；按泡续 msg_seq（同 msg_id 内 1,2,3 递增，否则平台去重报错）
        at = job.at_user if (i == 0 and job.is_group) else ""
        await send_split_message(
            url, chunk, job.msg_id, at_user_id=at, msg_seq_start=i + 1
        )
        await record_message(
            job.group_id, job.user_id, "bot", f"{job.memory_tag}{chunk}"
        )

    await asyncio.sleep(SEND_TURN_GAP)
