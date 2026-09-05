import asyncio
import json
import re
import aiosqlite
from datetime import datetime
from core.ai import get_ai_reply
from services.db import DB_PATH
from services import scene_queue
from config import (
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    LIGHT_MODEL_KEY,
    SCENE_MIN_JUDGE,
    SCENE_JUDGE_INTERVAL,
    SCENE_IDLE_FORCE,
    SCENE_MAX_QUEUE,
    SCENE_MIN_CUT,
    SCENE_SCAN_INTERVAL,
)

# group_id -> {"last_judge_len": int, "last_msg_time": datetime|None}
_group_state: dict[str, dict] = {}


async def init_scenes_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                summary TEXT,
                participants TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                message_count INTEGER
            )
        """)
        await db.commit()
    await scene_queue.init_scene_queue_table()


async def check_and_update_scene(
    group_id: str, user_id: str, speaker: str, content: str
):
    """
    消息入队入口（main.py 调用点不变）。
    元交互（指令/动作）不入队；攒够阈值则触发 LLM 分割判断。
    """
    if content.startswith("[动作]") or content.startswith("[指令]"):
        return

    now = datetime.now()
    await scene_queue.enqueue(group_id, speaker, user_id, content, now)
    st = _group_state.setdefault(group_id, {"last_judge_len": 0, "last_msg_time": now})
    st["last_msg_time"] = now

    n = (await scene_queue.get_queue(group_id)).__len__()
    due = (
        st["last_judge_len"] == 0 or (n - st["last_judge_len"]) >= SCENE_JUDGE_INTERVAL
    )
    if n >= SCENE_MIN_JUDGE and due:
        st["last_judge_len"] = n
        asyncio.create_task(_judge_loop(group_id))


JUDGE_SYSTEM = """你是群聊话题分割员。判断给定聊天记录的队头是否构成一个完整且已结束的话题。

规则：
- 只从队头切：cut = 从第 1 条开始的连续前 cut 条构成一个已结束的话题
- 若队头话题明显还在延续，返回 cut=0
- 时间间隔超过 20 分钟的消息通常属于不同话题
- 若队头话题不足 4 条就还没成段，返回 cut=0
- summary 用昵称指代人（如"麦田询问择校，YuriBot建议..."），40字内客观纪要

输出严格 JSON，不要解释：
{"summary": "...", "cut": 数字}"""


async def _format_queue(queue: list[dict]) -> str:
    from services.user_manager import get_nickname

    lines = []
    for m in queue:
        name = "YuriBot" if m["speaker"] == "bot" else await get_nickname(m["user_id"])
        ts = m["time"].strftime("%m-%d %H:%M")
        lines.append(f"【{ts}】{name}: {m['content'][:80]}")
    return "\n".join(lines)


def _parse_judge(raw: str) -> tuple[str | None, int]:
    m = re.search(r"\{[^{}]*\}", raw or "")
    if not m:
        return None, 0
    try:
        data = json.loads(m.group(0))
        summary = str(data.get("summary", "")).strip()
        cut = int(data.get("cut", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, 0
    if not summary:
        return None, 0
    return summary, cut


async def _judge_once(group_id: str) -> bool:
    """判断一轮。返回 True 表示出了队（调用方可继续连判）"""
    queue = await scene_queue.get_queue(group_id)
    n = len(queue)
    if n < SCENE_MIN_CUT:
        return False

    forced = n >= SCENE_MAX_QUEUE  # 硬顶：必须切

    try:
        raw = await get_ai_reply(
            user_message=f"共{n}条消息：\n{await _format_queue(queue)}",
            system_override=JUDGE_SYSTEM,
            max_tokens=300,
            temperature=0.0,
            model=LIGHT_MODEL_NAME,
            api_url=LIGHT_MODEL_URL,
            api_key=LIGHT_MODEL_KEY,
            enable_thinking=False,
            timeout=30,
        )
        summary, cut = _parse_judge(raw)
    except Exception as e:
        print(f"[情景判断异常] {type(e).__name__}: {e}")
        return False

    if summary is None:
        print(f"[情景判断解析失败] {(raw or '')[:100]!r}")
        return False

    # 校验与夹逼
    if cut < SCENE_MIN_CUT:
        cut = 0
    cut = min(cut, n)
    if forced and cut == 0:
        cut = n // 2  # 硬顶强制切半
    if cut == 0:
        return False

    segment = queue[:cut]
    await _close_segment(group_id, segment, summary)
    await scene_queue.dequeue(group_id, cut)

    st = _group_state.setdefault(group_id, {"last_judge_len": 0, "last_msg_time": None})
    st["last_judge_len"] = max(0, st["last_judge_len"] - cut)
    print(f"[情景出队] 群:{group_id[:8]}, cut:{cut}, 剩余:{n - cut}")
    return True


async def _judge_loop(group_id: str):
    """连判最多 3 轮：一次触发把队头的多个话题都结算掉"""
    for _ in range(3):
        if not await _judge_once(group_id):
            break
        if len(await scene_queue.get_queue(group_id)) < SCENE_MIN_CUT:
            break


async def _close_segment(group_id: str, segment: list[dict], summary: str):
    """摘要入库 + 嵌入 + Qdrant（沿用原有双写管线）"""
    summary = summary.replace("\n", " ").strip()
    if len(summary) > 80:
        summary = summary[:80]
    participants = list({m["user_id"] for m in segment if m["speaker"] == "user"})
    now = datetime.now().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO scenes (group_id, summary, participants, start_time, end_time, message_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                group_id,
                summary,
                json.dumps(participants),
                segment[0]["time"].isoformat(),
                segment[-1]["time"].isoformat(),
                len(segment),
            ),
        )
        await db.commit()
        scene_id = cursor.lastrowid

    print(
        f"[情景关闭] 群:{group_id[:8]}, id:{scene_id}, 段长:{len(segment)}, 摘要:{summary[:40]}"
    )

    async def _async_index():
        try:
            from services.embedding import embed_text
            from services.vector_store import upsert_scene

            vector = await embed_text(summary)
            await upsert_scene(scene_id, group_id, summary, participants, now, vector)
        except Exception as e:
            print(f"[Qdrant索引失败] scene_id={scene_id}: {e}")

    asyncio.create_task(_async_index())


async def _summarize(msgs: list[dict]) -> str:
    """静默兜底：整队强制摘要（不问 LLM 话题是否结束）"""
    prompt = f"""用第三人称客观记录这段群聊（40字内，不带情绪，像会议纪要）。
直接写结论，不要分析过程。

对话：
{await _format_queue(msgs)}

记录："""
    summary = await get_ai_reply(
        user_message=prompt,
        system_override="你是一位客观的会议记录员。只写结论，不要分析过程。",
        max_tokens=200,
        temperature=0.1,
        model=LIGHT_MODEL_NAME,
        api_url=LIGHT_MODEL_URL,
        api_key=LIGHT_MODEL_KEY,
        enable_thinking=False,
    )
    if not summary or "没听见" in summary or "开小差" in summary:
        summary = "群友聊天"
    for prefix in ("会议记录：", "记录：", "摘要：", "总结："):
        if summary.startswith(prefix):
            summary = summary[len(prefix) :]
            break
    return summary


async def _flush_group(group_id: str, queue: list[dict]):
    """静默结算：整队入库清空"""
    if not queue:
        return
    summary = await _summarize(queue)
    await _close_segment(group_id, queue, summary)
    await scene_queue.clear_group(group_id)
    st = _group_state.get(group_id)
    if st:
        st["last_judge_len"] = 0
    print(f"[情景静默结算] 群:{group_id[:8]}, 整队{len(queue)}条已入库")


async def scene_scan_loop():
    """后台扫描：静默超时强制结算。冷场也能结算，不再依赖有人说话"""
    while True:
        try:
            await asyncio.sleep(SCENE_SCAN_INTERVAL)
            for group_id in await scene_queue.all_group_ids():
                queue = await scene_queue.get_queue(group_id)
                if not queue:
                    continue
                st = _group_state.get(group_id)
                last_msg = (
                    st["last_msg_time"]
                    if st and st["last_msg_time"]
                    else queue[-1]["time"]
                )
                idle = (datetime.now() - last_msg).total_seconds()
                if idle >= SCENE_IDLE_FORCE:
                    await _flush_group(group_id, queue)
        except Exception as e:
            print(f"[情景扫描异常] {type(e).__name__}: {e}")
