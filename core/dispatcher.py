"""
统一指令分发：私聊 / 群@ / 群免@ 三种事件入口共用。
指令交互以 [指令] 标签进聊天记录，不进情景队列（过滤在 scene_manager 入队处）。
"""

from core.registry import get_handler, get_cmd_list
from core.context import CmdContext
from core.memory import record_message
from services.state import load_state
from services.actions import send_text


async def handle_command(
    content: str,
    user_id: str,
    group_id: str,
    msg_id: str,
    is_group: bool,
) -> None:
    """
    处理一条 / 指令（调用方保证 content 以 "/" 开头）。
    流程：记录[指令] → 查注册表 → 执行 → 回复。
    handler 返回 str 时由本函数统一发送并记记忆；
    返回 None 视为 handler 已自行发送（如 /latex 发图）。
    """
    await record_message(group_id, user_id, "user", f"[指令] {content}")

    parts = content.split(maxsplit=1)
    cmd_name = parts[0][1:]
    raw = parts[1] if len(parts) > 1 else ""
    args = raw.split() if raw else []

    handler = get_handler(cmd_name)
    if not handler:
        reply = f"❓ 未知指令: /{cmd_name}，可用: {get_cmd_list()}"
        await send_text(
            group_id,
            user_id,
            reply,
            msg_id,
            is_group=is_group,
            memory_tag="[指令] ",
        )
        return

    ctx = CmdContext(
        group_id=group_id,
        user_id=user_id,
        msg_id=msg_id,
        is_group=is_group,
        cmd=cmd_name,
        args=args,
        raw=raw,
        state=load_state(),
    )
    result = await handler(ctx)
    if isinstance(result, str):
        await send_text(
            group_id,
            user_id,
            result,
            msg_id,
            is_group=is_group,
            memory_tag="[指令] ",
        )
