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
from datetime import datetime

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

JUDGE_SYSTEM = """你是群聊插话裁判。判断 YuriBot 看到这条消息后会不会想接话。

【重要】这个队列里的消息都没有@YuriBot；消息中出现的 @某人 或 @昵称，指的都是其他群友，不是她。
addressee 判断时：消息以 @xxx 开头 → 一律 someone:xxx，绝不可能是 yuri。

【她是谁】YuriBot：广州市天河区的高二宅女，回家部。兴趣：二次元（番剧/谷子/同人）、游戏、日常闲聊。无感：体育运动、现充社交。说话短、有梗但克制。

【她的可得性】（结合【她现在】判断）：
- 睡觉（23:00-6:00）→ 基本不回，除非被直接叫醒
- 在学校 → 只有课间/午休可能回，上课不回
- 补番/打游戏/刷手机 → 正常可回（手机就在旁边，边看边回是她的日常）

【判断标准】
- 这句话掉在地上可不可惜？有话接、有槽吐、有共情点 → reply=true
- 群友分享日常（吃饭/上课/吐槽）她常会捧场，但话少，也算 reply=true
- 纯表情包/纯图（无文字或只有配字）→ 倾向 false；除非特别想吐槽，否则一句短回
- 她明显没空（在睡觉/在上课）→ 倾向 false
- 拿不准时倾向 false：接错话比错过可惜

判断步骤：
第一步，判断新消息对谁说（addressee）：
- "yuri"：主语是她、点她名、请求她做事（"你帮我X""yuri你看这个"）
- "someone:名字"：明确点名其他某个成员（包括消息开头带 @昵称、@某位群友）
- "everyone"：对全群的开放喊话/提问（"群里有人吗""谁看过XX"）
- "none"：自言自语/纯通知，没有明确对象

第二步，结合 addressee 决定 reply：
- someone:别人 → 默认 false（别抢话）；只有那句话明显也抛给了她时才 true
- everyone → 她可能举手，有话接就 true
- yuri → 必须 true（被点到了不能沉默）；请求超出能力也 true——回应"办不到"或变相帮忙
- none → 按上面的判断标准

输出严格 JSON，不要解释：
{"addressee": "yuri|everyone|none|someone:名字", "reply": true/false, "reason": "≤15字"}

示例1：
群聊：麦田: mjl，你在吗 → {"addressee":"someone:mjl","reply":false,"reason":"点名mjl，不抢话"}
示例2：
群聊：麦田: 群里没人吗 → {"addressee":"everyone","reply":true,"reason":"全群喊话，举手"}
示例3：
群聊：麦田: 你帮我@一下mjl → {"addressee":"yuri","reply":true,"reason":"点她做事，@不了就文字代喊"}
示例4：
群聊：麦田: 今天食堂的菜好咸 → {"addressee":"none","reply":true,"reason":"日常吐槽，捧场"}"""

CONTINUATION_NOTE = (
    "\n\n（注意：这是她刚发过言后的延续对话，或群友引用了她的话，"
    "同等条件下倾向 reply=true。但若消息点名的是其他群友，仍以'别抢话'为准。）"
)

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
        )


async def _log(
    group_id,
    user_id,
    msg_excerpt,
    reply,
    reason,
    addressee,
    continuation,
):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO interject_log
                   (group_id, user_id, msg, reply, reason, addressee, continuation, scene, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    group_id,
                    user_id,
                    msg_excerpt,
                    int(reply),
                    reason,
                    addressee,
                    int(continuation),
                    get_current_scene(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
    except Exception as e:
        print(f"[插话日志失败] {type(e).__name__}: {e}")


def _schedule_reply(item: _JudgeItem, delay: float = 1.5):
    """回复走 debounce，多条选中时按序号错开 2 秒发送（不@人）"""
    from core.debounce import schedule
    from handlers.chat import handle_chat
    from services.actions import send_text
    from utils.scene_manager import check_and_update_scene

    async def _do():
        reply_text = await handle_chat(
            item.content,
            user_id=item.user_id,
            group_id=item.group_id,
            msg_id=item.msg_id,
            is_group=True,
        )
        if not reply_text:
            return
        await send_text(
            item.group_id,
            item.user_id,
            reply_text,
            item.msg_id,
            is_group=True,
            at_user="",
        )
        await check_and_update_scene(item.group_id, item.user_id, "bot", reply_text)

    schedule(f"interject:{item.group_id}", _do, delay=delay)


async def _judge_one(item: _JudgeItem):
    ctx = get_context(item.group_id, item.user_id)
    if not ctx:
        return
    lines = await _judge_lines(ctx)
    history_text = "\n".join(lines[-(INTERJECT_HISTORY_LINES + 1) : -1])
    current_line = lines[-1]
    continuation = item.has_quote or any(m["speaker"] == "bot" for m in ctx[-2:])

    system = JUDGE_SYSTEM + (CONTINUATION_NOTE if continuation else "")
    judge_input = (
        f"【本群成员】YuriBot（她，也被叫 yuri / bot）、{_roster_text(ctx)}\n"
        f"【她现在】{get_current_scene()}\n"
        f"【最近群聊】\n{history_text}\n\n"
        f"【新消息】{current_line}\n\n"
        "按步骤判断她会不会想接话。"
    )
    raw = await _call_judge(system, judge_input)
    reply, reason, addressee = _parse_judge(raw)
    print(
        f"[插话] judge: reply={reply}, addressee={addressee}, "
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
        _schedule_reply(it, delay=1.5 + seq * 2.0)
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
        print(f"[插话静默门] {type(e).__name__}: {e}")


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
        print(f"[插话] {type(e).__name__}: {e}")
