import asyncio
import json
import aiosqlite
from datetime import datetime
from core.ai import get_ai_reply
from services.db import DB_PATH

# 当前活跃情景：group_id -> {messages, last_time, user_id, start_time}
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

    prompt = f"""用第三人称客观记录这段群聊（40字内，不带情绪，像会议纪要）。
直接写结论，不要分析过程。

对话：
{_format_messages_for_summary(msgs)}

记录："""

    from core.ai import get_ai_reply
    from config import LIGHT_MODEL_NAME, LIGHT_MODEL_URL, LIGHT_MODEL_KEY

    summary = await get_ai_reply(
        user_message=prompt,
        system_override="你是一位客观的会议记录员。只写结论，不要分析过程。",
        max_tokens=200,
        temperature=0.1,
        model=LIGHT_MODEL_NAME,
        api_url=LIGHT_MODEL_URL,
        api_key=LIGHT_MODEL_KEY,
    )

    # 失败兜底（AI服务异常时 get_ai_reply 会返回人设化占位句）
    if not summary or "没听见" in summary or "开小差" in summary:
        summary = "群友聊天"

    summary = summary.replace("\n", " ").strip()
    if len(summary) > 50:
        summary = summary[:50]
    return summary


async def _do_close_scene(group_id: str, scene: dict):
    """真正关闭场景并入库（SQLite + Qdrant 双写）"""
    if not scene or not scene.get("messages"):
        return

    msgs = scene["messages"]
    if len(msgs) < 2:
        print(f"[情景丢弃] 群:{group_id[:8]}, 条数:{len(msgs)}")
        return

    summary = await _generate_summary(msgs)
    summary = summary.replace("\n", " ").replace("  ", " ").strip()
    if len(summary) > 50:
        summary = summary[:50]

    participants = list(set(m["user_id"] for m in msgs if m["speaker"] == "user"))
    now = datetime.now().isoformat()

    # 1. 写入 SQLite，拿到自增 id
    import aiosqlite
    from services.db import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
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
        scene_id = cursor.lastrowid  # 拿到自增 id

    print(f"[情景关闭] 群:{group_id[:8]}, id:{scene_id}, 摘要:{summary[:40]}")

    # 2. 异步写入 Qdrant（fire-and-forget，失败不影响主流程）
    async def _async_index():
        try:
            from services.embedding import embed_text
            from services.vector_store import upsert_scene

            vector = await embed_text(summary)
            await upsert_scene(
                scene_id=scene_id,
                group_id=group_id,
                summary=summary,
                participants=participants,
                timestamp=now,
                vector=vector,
            )
        except Exception as e:
            print(f"[Qdrant索引失败] scene_id={scene_id}: {e}")

    asyncio.create_task(_async_index())


async def close_scene(group_id: str):
    """兼容旧调用的入口（从全局字典取）"""
    scene = _current_scenes.pop(group_id, None)
    if scene:
        await _do_close_scene(group_id, scene)


async def check_and_update_scene(
    group_id: str, user_id: str, speaker: str, content: str, msg_time: datetime = None
):
    """
    检查是否需要关闭旧情景，然后把消息加入当前情景
    返回：是否刚关闭了一个情景
    """
    if msg_time is None:
        msg_time = datetime.now()

    closed = False
    existing = _current_scenes.get(group_id)

    # 检查是否需要关闭旧情景
    if existing:
        time_gap = (msg_time - existing["last_time"]).total_seconds()
        msg_count = len(existing["messages"])

        # 触发条件：静默>15分钟 或 累计>12条
        if time_gap > 900 or msg_count >= 12:
            old_scene = _current_scenes.pop(group_id, None)
            if old_scene:
                asyncio.create_task(_do_close_scene(group_id, old_scene))
            closed = True
            existing = None

    # 创建或加入当前情景
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
