"""
无@回复系统（v4）。群消息（非@、非触发词）由轻量裁判模型决定是否回复，
作为 @ 的轻量替代。

机制：
- 静默门：同一用户连发碎片时，等静默期（基准±抖动）到期才把"最新一条"入队——
  人类用"停顿"判断对方说完没有，judge 届时看到的是完整的一段话（碎片已合并+图片描述已替换）
- 预处理：低价值消息（纯表情/单字/语气词）直接 false，不进静默门
- FIFO 不覆盖 + 两级并发 + 批量折叠（同 v3）
- 插话不@人；消息以普通 bot 消息进记忆
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime
from tokenize import group

import aiosqlite

from config import (
    INTERJECT_API_CONCURRENCY,
    INTERJECT_BATCH_THRESHOLD,
    INTERJECT_HISTORY_LINES,
    INTERJECT_MAX_BATCH,
    INTERJECT_SILENCE,
    INTERJECT_TEMP,
    INTERJECT_THINKING,
    INTERJECT_WORKER2_BACKLOG,
    LIGHT_MODEL_KEY,
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    MOOD_AIR_ENABLED,
    SERIAL_QUEUE_MAX,
    SEND_JOB_MAX_WAIT,
)
from core.ai import get_ai_reply
from core.memory import (
    build_merged_lines,
    get_context,
    substitute_image_placeholders,
)
from core.scene import get_current_scene
from services.db import DB_PATH
from services.user_manager import get_nickname
from services.actions import send_text_chat
from services import mood_air

JUDGE_SYSTEM = """你是群聊插话裁判。判断 YuriBot 看到这条消息后会不会想接话。

【重要】这个队列里的消息都没有@YuriBot；消息中出现的 @某人 或 @某位群友，指的都是其他群友，不是她。

【她是谁】YuriBot（群里也被叫 yuri / bot）：广州市天河区的高二宅女，回家部。兴趣：二次元（番剧/谷子/同人）、游戏、日常闲聊。无感：体育运动、现充社交。说话短、有梗但克制。

【她的可得性】（结合【她现在】判断）：
- 睡觉（23:00-6:00）→ 基本不回，除非被直接叫醒
- 在学校 → 只有课间/午休可能回，上课不回
- 补番/打游戏/刷手机 → 正常可回（手机就在旁边，边看边回是她的日常）

【判断标准】
- 这句话掉在地上可不可惜？有话接、有槽吐、有共情点 → reply=true
- 群友分享日常（吃饭/上课/吐槽）她常会捧场，但话少，也算 reply=true
- 纯表情包/纯图 → 倾向 false，除非特别想吐槽
- 她明显没空（在睡觉/在上课）→ 倾向 false
- 拿不准时倾向 false：接错话比错过可惜
- 【她的动向】显示她正在回复或刚回复过某条 → 同话题的新消息倾向 false（她的回复马上到或已经到了，别重复接）；只有新消息开了明显新话题且她的话掉在地上可惜时才 true
- 群友在讨论她的回复机制、judge、系统内部的事 → 她听不太懂这个，倾向 false；只有被直接点名问她才回

判断步骤：
第一步，判断新消息对谁说（addressee）：
- "yuri"：主语是她、点她名、请求她做事，或者——她刚发过言，这条消息在回应/评价/吐槽她刚说的话或做的事（夸她、骂她、纠正她、问她"你怎么这样"），哪怕一个名字都没提
- "someone:名字"：明确点名其他某个成员，问的是那个人的事
- "everyone"：对全群的开放喊话/提问
- "none"：自言自语/纯通知，没有明确对象

第二步，决定 reply：
- addressee 是 someone:别人 → 默认 false（别抢话）；只有那句话明显也抛给了她时才 true
- addressee 是 everyone → 她可能举手，有话接就 true
- addressee 是 yuri → 通常该回，但有两条例外：
  ① 她在睡觉/上课，且她没在这个话题里 → 装没听见；
     但她正在参与这个话题（她刚问过、猜过相关内容）→ 该回
  ② 她刚连续发过言（≥2条）且这条没点名她 → 倾向 false（让人类把话说完，别句句都接）
- addressee 是 none → 按上面的判断标准

输出严格 JSON，不要解释：
{"addressee": "yuri|everyone|none|someone:名字", "reply": true/false, "reason": "≤15字"}

示例1：
群聊：麦田: mjl，你在吗 → {"addressee":"someone:mjl","reply":false,"reason":"点名mjl，不抢话"}
示例2：
群聊：麦田: 群里没人吗 → {"addressee":"everyone","reply":true,"reason":"全群喊话，举手"}
示例3：
群聊：麦田: 你帮我@一下mjl → {"addressee":"yuri","reply":true,"reason":"点她做事，@不了就文字代喊"}
示例4：
群聊：YuriBot: 这是bfnz，就是逼飞奶炸 / 麦田: 你怎么能这么粗俗，直接说出那个词 → {"addressee":"yuri","reply":true,"reason":"在评价她刚说的话，必须接"}
示例5：
群聊：YuriBot: （刚才的发言） / 麦田: oi → {"addressee":"yuri","reply":true,"reason":"喊她回应，在叫她"}
示例6：
群聊：YuriBot: 这摩托太子款？排量多大的 / 军师: 太子个锤子，这是张雪机车 → {"addressee":"yuri","reply":true,"reason":"纠正她的错，她在聊这个话题必须接"}
示例7：
群聊：YuriBot: （ unrelated 的上一条） / 群友: （课堂话题外的新问题） → {"addressee":"yuri","reply":false,"reason":"她在上课且没在聊这个，装没听见"}
"""


BATCH_SUFFIX = """

【批量模式】上面的【待判消息】是 N 条积压消息（编号1-N）。逐条独立判断哪些值得她回，
输出：{"replies": [{"index": 编号, "reason": "≤10字"}, ...]}
只列值得回的；都不值得就输出 {"replies": []}。最多回 3 条。"""

# 预处理：不值得思考的消息（直接 false，不进静默门）
_JUNK_RE = re.compile(
    r"^(哈哈+|哦+|嗯+|行+|好$|可以|666+|233+|nb|k|OK|ok|"
    r"[\s\W_]+|\[表情\]+)+$"
)


class _JudgeItem:
    __slots__ = ("group_id", "user_id", "content", "msg_id", "has_quote")

    def __init__(self, group_id, user_id, content, msg_id, has_quote):
        self.group_id = group_id
        self.user_id = user_id
        self.content = content
        self.msg_id = msg_id
        self.has_quote = has_quote


class _GroupState:
    __slots__ = ("queue", "workers", "lock")

    def __init__(self):
        self.queue: asyncio.Queue[_JudgeItem] = asyncio.Queue()
        self.workers = 0
        self.lock = asyncio.Lock()


_groups: dict[str, _GroupState] = {}
_pending_silence: dict[tuple, tuple] = {}  # (group,user) → (_JudgeItem, task)
_api_sem = asyncio.Semaphore(INTERJECT_API_CONCURRENCY)

# ========== 每群串行接话队列（串行化：复查→生成→等发完→下一条） ==========
RECHECK_MIN_AGE = 6.0  # 入队不足6秒的item跳过复查（上下文几乎没变，省一次调用）
ITEM_TIMEOUT = 20.0  # 单条处理整体超时（防队头堵死）
_serial_queues: dict[str, asyncio.Queue] = {}
_serial_workers: dict[str, asyncio.Task] = {}
# ========== 她的动向（在途回复可见性，TTL 20s，纯内存不进记忆） ==========
ACTIVITY_TTL = 20.0
_reply_activity: dict[str, list] = {}  # group_id → [(monotonic_ts, kind, excerpt)]


def _note_activity(group_id: str, kind: str, excerpt: str):
    """kind: pending=已决定回还没发 / sent=刚发出去。excerpt 截短防 prompt 膨胀"""
    now = time.monotonic()
    lst = [
        (t, k, e)
        for t, k, e in _reply_activity.get(group_id, [])
        if now - t < ACTIVITY_TTL
    ]
    lst.append((now, kind, excerpt[:20]))
    _reply_activity[group_id] = lst[-3:]  # 最多留3条，防刷屏时块膨胀


async def init_interject_table():
    """建表 + 补列迁移（旧表无 addressee 列时自动 ALTER，保留旧日志）"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS interject_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                user_id TEXT,
                msg TEXT,
                reply INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                addressee TEXT DEFAULT '',
                continuation INTEGER DEFAULT 0,
                scene TEXT DEFAULT '',
                created_at TIMESTAMP
            )
        """)
        async with db.execute("PRAGMA table_info(interject_log)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        if "addressee" not in cols:
            await db.execute(
                "ALTER TABLE interject_log ADD COLUMN addressee TEXT DEFAULT ''"
            )
        if "judge_latency_ms" not in cols:
            await db.execute(
                "ALTER TABLE interject_log ADD COLUMN judge_latency_ms INTEGER DEFAULT 0"
            )
        await db.execute("""CREATE TABLE IF NOT EXISTS arbiter_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT, n_items INTEGER, decision TEXT,
            created_at TIMESTAMP)""")

        await db.commit()


def _is_junk(content: str) -> bool:
    text = content.strip()
    if len(text) <= 1:
        return True
    if _JUNK_RE.match(text):
        return True
    return False


def _roster_text(ctx: list) -> str:
    roster = []
    for m in ctx:
        if m["identity"] not in roster:
            roster.append(m["identity"])
    return "、".join(roster) if roster else "（暂无）"


async def _judge_lines(ctx: list) -> list:
    """judge 历史：碎片合并 + 图片描述替换（与主 prompt 同一份语义）"""
    lines = build_merged_lines(ctx)
    return [await substitute_image_placeholders(l) for l in lines]


def _parse_judge(raw: str) -> tuple[bool, str, str]:
    """返回 (reply, reason, addressee)"""
    if not raw:
        return False, "空响应", ""

    def _loads(s: str):
        m = re.search(r"\{[^{}]*\}", s)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    data = _loads(raw)
    if data is None:
        fixed = (
            raw.replace(""", '"').replace(""", '"')
            .replace("'", "'")
            .replace("'", "'")
            .replace("：", ":")
        )
        data = _loads(fixed)
    if data is not None:
        return (
            bool(data.get("reply", False)),
            str(data.get("reason", ""))[:30],
            str(data.get("addressee", ""))[:20],
        )
    lowered = raw.lower()
    if "false" in lowered or "不回" in raw:
        return False, "解析失败兜底false", ""
    if "true" in lowered:
        return True, "解析失败兜底true", ""
    return False, "解析失败", ""


def _parse_batch(raw: str) -> list[int]:
    """批量判断输出 → 值得回的编号（0 基）列表，最多 3 条"""
    if not raw:
        return []
    m = re.search(r"\{[^{}]*\}", raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for r in data.get("replies") or []:
        if not isinstance(r, dict):
            continue
        try:
            i = int(r.get("index")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < INTERJECT_MAX_BATCH * 2:
            out.append(i)
    return sorted(set(out))[:3]


async def _call_judge(system: str, user_msg: str, batch: bool = False) -> str:
    max_tokens = 1000 if INTERJECT_THINKING else (400 if batch else 150)
    async with _api_sem:
        return await get_ai_reply(
            user_message=user_msg,
            system_override=system,
            max_tokens=max_tokens,
            temperature=INTERJECT_TEMP,
            model=LIGHT_MODEL_NAME,
            api_url=LIGHT_MODEL_URL,
            api_key=LIGHT_MODEL_KEY,
            timeout=60 if INTERJECT_THINKING else 30,
            enable_thinking=INTERJECT_THINKING,
            tag="judge",
        )


async def _log(
    group_id,
    user_id,
    msg_excerpt,
    reply,
    reason,
    addressee,
    continuation,
    latency_ms: int = 0,  # 新增：judge 耗时（毫秒），默认 0
):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO interject_log
                   (group_id, user_id, msg, reply, reason, addressee,
                    continuation, scene, judge_latency_ms, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    group_id,
                    user_id,
                    msg_excerpt,
                    int(reply),
                    reason,
                    addressee,
                    int(continuation),
                    get_current_scene(),
                    latency_ms,  # 新增：对应新列
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
    except Exception as e:
        print(f"[接话日志失败] {type(e).__name__}: {e}")


def _schedule_reply(item: _JudgeItem, delay: float = 1.5):
    """judge=true 的item进每群串行队列，由队列worker串行处理（不再各自异步直发）"""
    _note_activity(item.group_id, "pending", item.content)
    q = _serial_queues.get(item.group_id)
    if q is None:
        q = asyncio.Queue(maxsize=SERIAL_QUEUE_MAX)
        _serial_queues[item.group_id] = q
    try:
        q.put_nowait((item, time.monotonic()))
    except asyncio.QueueFull:
        print(
            f"[接话队列] 群{item.group_id[:8]}满员({SERIAL_QUEUE_MAX})，丢弃: {item.content[:30]!r}"
        )
        asyncio.create_task(
            _log(
                item.group_id,
                item.user_id,
                item.content[:80],
                False,
                "队列满丢弃",
                "",
                False,
                0,
            )
        )
        return
    task = _serial_workers.get(item.group_id)
    if task is None or task.done():
        _serial_workers[item.group_id] = asyncio.create_task(
            _serial_worker(item.group_id)
        )


async def _serial_worker(group_id: str):
    """每群一条流水线：出队 → 复查 → 生成 → 等气泡发完 → 下一条。
    串行保证：第N条生成时第N-1条已在历史里（连续性根治）；
    热群队列越长 → 复查过期率越高 → 自动安静（噪声自我调节）。"""
    from handlers.chat import handle_chat
    from services.actions import send_text_chat
    from utils.scene_manager import check_and_update_scene

    q = _serial_queues[group_id]
    while True:
        try:
            item, enqueued_at = q.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            # ===== 复查：入队已久才复查（新鲜item跳过） =====
            if time.monotonic() - enqueued_at >= RECHECK_MIN_AGE:
                if not await _recheck(item):
                    continue

            # ===== 生成 + 发送，等气泡真正发完（record_message已落历史） =====
            async def _process(it=item):
                reply_text = await handle_chat(
                    it.content,
                    user_id=it.user_id,
                    group_id=it.group_id,
                    msg_id=it.msg_id,
                    is_group=True,
                )
                if not reply_text:
                    return
                done = asyncio.Event()
                await send_text_chat(
                    it.group_id,
                    it.user_id,
                    reply_text,
                    it.msg_id,
                    is_group=True,
                    priority=False,
                    trigger_content=it.content,
                    done_event=done,
                )
                try:
                    await asyncio.wait_for(done.wait(), timeout=SEND_JOB_MAX_WAIT + 10)
                except asyncio.TimeoutError:
                    print(f"[接话队列] 等发送完成超时: {it.group_id[:8]}")
                await check_and_update_scene(it.group_id, it.user_id, "bot", reply_text)
                _note_activity(it.group_id, "sent", reply_text)

            await asyncio.wait_for(_process(), timeout=ITEM_TIMEOUT)
        except asyncio.TimeoutError:
            print(
                f"[接话队列] 单条处理超时丢弃(>{ITEM_TIMEOUT:.0f}s): {item.content[:30]!r}"
            )
            asyncio.create_task(
                _log(
                    group_id,
                    item.user_id,
                    item.content[:80],
                    False,
                    "处理超时丢弃",
                    "",
                    False,
                    0,
                )
            )
        except Exception as e:
            print(f"[接话队列] {type(e).__name__}: {e}")


async def _recheck(item: _JudgeItem) -> bool:
    """出队复查：话题已被回过/已过期/在途回复已覆盖 → false丢弃。
    注意：历史里可能已有更新消息，待判消息必须显式指定（不能用lines[-1]）。"""
    ctx = get_context(item.group_id, item.user_id)
    if not ctx:
        return False
    lines = await _judge_lines(ctx)
    history_text = "\n".join(lines[-(INTERJECT_HISTORY_LINES + 1) :])
    nick = await get_nickname(item.user_id)
    judge_input = (
        f"【本群成员】YuriBot（她，也被叫 yuri / bot）、{_roster_from_lines(lines)}\n"
        f"【她现在】{get_current_scene()}\n"
        f"【最近群聊】\n{history_text}\n\n"
        f"【待复查的消息】{nick}：{item.content}\n\n"
        "【复查】这条消息此前被判值得回。现在距那时已过了一些时间，"
        "上面的群聊里有这期间的新对话。重新判断：这个话题（或等价的追问）"
        "已经被她回过了吗？她现在再回会显得重复或过期吗？是则 reply=false。"
        "严格 JSON 输出。"
    )
    judge_input += _activity_block(item.group_id)
    jt0 = time.monotonic()
    raw = await _call_judge(JUDGE_SYSTEM, judge_input)
    latency_ms = int((time.monotonic() - jt0) * 1000)
    reply, reason, addressee = _parse_judge(raw)
    print(f"[接话] 复查: reply={reply}, reason={reason}, msg={item.content[:30]!r}")
    if not reply:
        await _log(
            item.group_id,
            item.user_id,
            item.content[:80],
            False,
            f"复查:{reason}",
            addressee,
            False,
            latency_ms,
        )
    return reply


async def _judge_one(item: _JudgeItem):
    system = JUDGE_SYSTEM
    ctx = get_context(item.group_id, item.user_id)
    if not ctx:
        return
    lines = await _judge_lines(ctx)
    continuation = item.has_quote or any(m["speaker"] == "bot" for m in ctx[-2:])

    judge_input = _compose_judge_input(lines, continuation, group_id=item.group_id)

    # 气氛站 solemn 提示（依赖 item.group_id，留在调用处，不放进纯函数）
    if MOOD_AIR_ENABLED and mood_air.get_register(item.group_id) == "solemn":
        judge_input += "\n\n（当前气氛沉重（有群友难过），没被直接点名就保持沉默）"

    jt0 = time.monotonic()
    raw = await _call_judge(system, judge_input)
    latency_ms = int((time.monotonic() - jt0) * 1000)
    reply, reason, addressee = _parse_judge(raw)
    print(
        f"[接话] judge: reply={reply}, addressee={addressee}, "
        f"reason={reason}, msg={item.content[:30]!r}"
    )
    await _log(
        item.group_id,
        item.user_id,
        item.content[:80],
        reply,
        reason,
        addressee,
        continuation,
        latency_ms,
    )
    if reply:
        _schedule_reply(item)


async def _judge_batch(group_id: str, items: list[_JudgeItem]):
    ctx = get_context(group_id, items[-1].user_id)
    if not ctx:
        return
    lines = await _judge_lines(ctx)
    history_text = "\n".join(lines[-(INTERJECT_HISTORY_LINES + 1) :])

    numbered = []
    for i, it in enumerate(items, 1):
        nick = await get_nickname(it.user_id)
        numbered.append(f"{i}. {nick}: {it.content}")

    system = JUDGE_SYSTEM + BATCH_SUFFIX
    judge_input = (
        f"【本群成员】YuriBot（她，也被叫 yuri / bot）、{_roster_text(ctx)}\n"
        f"【她现在】{get_current_scene()}\n"
        f"【最近群聊】\n{history_text}\n\n"
        f"【待判消息】\n" + "\n".join(numbered) + "\n\n"
        "逐条判断哪些值得她回。"
    )
    judge_input += _activity_block(group_id)
    raw = await _call_judge(system, judge_input, batch=True)
    picked = _parse_batch(raw)
    print(f"[插话] 批量judge: 选中{len(picked)}/{len(items)}条, raw={raw[:80]!r}")

    for seq, idx in enumerate(picked):
        it = items[idx]
        await _log(
            group_id,
            it.user_id,
            it.content[:80],
            True,
            f"批量选中#{idx + 1}",
            "",
            False,
        )
    _schedule_reply(it)
    for idx, it in enumerate(items):
        if idx not in picked:
            await _log(
                group_id, it.user_id, it.content[:80], False, "批量未选中", "", False
            )


async def _worker(st: _GroupState, group_id: str):
    try:
        while True:
            try:
                item = st.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if st.queue.qsize() >= INTERJECT_BATCH_THRESHOLD - 1:
                batch = [item]
                while len(batch) < INTERJECT_MAX_BATCH:
                    try:
                        batch.append(st.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await _judge_batch(group_id, batch)
            else:
                await _judge_one(item)
    finally:
        async with st.lock:
            st.workers -= 1


async def _ensure_worker(st: _GroupState, group_id: str):
    async with st.lock:
        want = st.workers
        if st.workers == 0:
            want = 1
        elif st.workers < 2 and st.queue.qsize() >= INTERJECT_WORKER2_BACKLOG:
            want = 2
        while st.workers < want:
            st.workers += 1
            asyncio.create_task(_worker(st, group_id))


async def _silence_gate(key: tuple, item: _JudgeItem, delay: float):
    """静默门：等该用户停顿期满才把消息入队（连发碎片只入队最新一条）"""
    try:
        await asyncio.sleep(delay)
        _pending_silence.pop(key, None)
        st = _groups.setdefault(item.group_id, _GroupState())
        await st.queue.put(item)
        await _ensure_worker(st, item.group_id)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"[接话静默门] {type(e).__name__}: {e}")


async def maybe_interject(
    group_id: str,
    user_id: str,
    content: str,
    msg_id: str,
    has_quote: bool = False,
):
    """
    群消息（非@、非触发词）的回复判断入口。由 main.py fire-and-forget 调用。
    先进静默门：同用户连发时只判最后的完整一段话。
    """
    try:
        if _is_junk(content):
            await _log(group_id, user_id, content[:80], False, "预处理跳过", "", False)
            return
        key = (group_id, user_id)
        old = _pending_silence.pop(key, None)
        if old:
            old[1].cancel()
        item = _JudgeItem(group_id, user_id, content, msg_id, has_quote)
        delay = INTERJECT_SILENCE + random.uniform(-1.5, 1.5)
        task = asyncio.create_task(_silence_gate(key, item, delay))
        _pending_silence[key] = (item, task)
    except Exception as e:
        print(f"[接话] {type(e).__name__}: {e}")


def _roster_from_lines(lines: list) -> str:
    roster = []
    for l in lines:
        identity = l.split("：", 1)[0]
        # 去掉行首相对时间前缀
        for prefix in (
            "[刚刚] ",
            "[几分钟前] ",
            "[刚才] ",
            "[半小时前] ",
            "[一小时前] ",
        ):
            if identity.startswith(prefix):
                identity = identity[len(prefix) :]
        if identity not in roster:
            roster.append(identity)
    return "、".join(roster) if roster else "（暂无）"


def _compose_judge_input(
    lines: list, continuation: bool, scene_text: str = None, group_id: str = ""
) -> str:
    """
    纯函数：由合并后的历史行组装 judge 输入。
    scene_text 可注入（测试用），默认取当前情境。
    """
    if scene_text is None:
        scene_text = get_current_scene()
    history_text = "\n".join(lines[-(INTERJECT_HISTORY_LINES + 1) : -1])
    current_line = lines[-1]

    extra = []
    if continuation:
        extra.append("上一条消息就是她刚发的，这条很可能是对她说的")
    streak = 0
    for l in reversed(lines[:-1]):
        if (
            l.lstrip().startswith(
                (
                    "YuriBot：",
                    "[",
                )
            )
            and "YuriBot：" in l[:30]
        ):
            streak += 1
        else:
            break
    if streak >= 2:
        extra.append(f"她已经连续发了{streak}条，这条没点名她名字的话她倾向先潜水")
    if any(k in scene_text for k in ("睡觉", "上课")):
        extra.append(f"她现在在{scene_text}，没被直接喊名字就装没听见")

    judge_input = (
        f"【本群成员】YuriBot（她，也被叫 yuri / bot）、{_roster_from_lines(lines)}\n"
        f"【她现在】{scene_text}\n"
        f"【最近群聊】\n{history_text}\n\n"
        f"【新消息】{current_line}\n\n"
        "按步骤判断她会不会想接话。"
    )
    if extra:
        judge_input += "\n\n（" + "；".join(extra) + "）"

    if group_id:
        judge_input += _activity_block(group_id)
    return judge_input


def _activity_block(group_id: str) -> str:
    """【她的动向】块：在途回复可见性。无活动返回空串"""
    now = time.monotonic()
    entries = [
        e for e in _reply_activity.get(group_id, []) if now - e[0] < ACTIVITY_TTL
    ]
    if not entries:
        return ""
    lines = []
    for ts, kind, excerpt in entries:
        ago = int(now - ts)
        if kind == "pending":
            lines.append(f"- 她正在回复「{excerpt}」那条（{ago}秒前决定的，还没发）")
        else:
            lines.append(f"- 她刚回了「{excerpt}」（{ago}秒前）")
    return "\n\n【她的动向】（她现在正在做的事，判断接不接话时必须考虑）\n" + "\n".join(
        lines
    )
