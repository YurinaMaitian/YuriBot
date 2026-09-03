import asyncio
import json
import aiosqlite
from datetime import datetime
from core.ai import get_ai_reply

DB_PATH = "/home/minds/qqbot/data/bot.db"

_current_scenes = {}


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


def _format_messages_for_summary(msgs):
    lines = []
    for m in msgs:
        speaker = "Bot" if m["speaker"] == "bot" else "用户"
        lines.append(f"{speaker}: {m['content'][:80]}")
    return "\n".join(lines)


async def _generate_summary(msgs):
    if not msgs:
        return "无内容"

    prompt = f"""以下是一段群聊对话，请用一句话总结这段对话的核心内容（50字以内）。
要包含：谁在问、聊了什么话题、结果如何。

对话：
{_format_messages_for_summary(msgs)}

总结："""

    summary = await get_ai_reply(prompt, temperature=0.3)
    if len(summary) > 60:
        summary = summary[:60]
    return summary


async def close_scene(group_id: str):
    scene = _current_scenes.pop(group_id, None)
    if not scene or not scene["messages"]:
        return

    msgs = scene["messages"]
    summary = await _generate_summary(msgs)
    participants = list(set(m["user_id"] for m in msgs if m["speaker"] == "user"))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO scenes (group_id, summary, participants, start_time, end_time, message_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                group_id,
                summary,
                json.dumps(participants),
                scene["start_time"],
                scene["last_time"],
                len(msgs),
            ),
        )
        await db.commit()

    print(f"[情景关闭] 群:{group_id[:8]}, 条数:{len(msgs)}, 摘要:{summary}")


async def check_and_update_scene(
    group_id: str, user_id: str, speaker: str, content: str, msg_time: datetime = None
):
    if msg_time is None:
        msg_time = datetime.now()

    closed = False
    existing = _current_scenes.get(group_id)

    if existing:
        time_gap = (msg_time - existing["last_time"]).total_seconds()
        msg_count = len(existing["messages"])

        if time_gap > 900 or msg_count >= 12:
            asyncio.create_task(close_scene(group_id))
            closed = True
            existing = None

    if existing is None:
        _current_scenes[group_id] = {
            "messages": [],
            "last_time": msg_time,
            "start_time": msg_time,
            "user_id": user_id,
        }

    _current_scenes[group_id]["messages"].append(
        {"speaker": speaker, "user_id": user_id, "content": content, "time": msg_time}
    )
    _current_scenes[group_id]["last_time"] = msg_time

    return closed
