from services.state import (
    load_state,
    set_group_state,
    set_global_state,
    get_group_by_index,
)
from services.user_manager import get_group_name
from core.registry import cmd


@cmd("on", desc="开启Bot，用法: /on [群序号]")
async def handle_on(ctx):
    target = None
    if ctx.args:
        arg = ctx.args[0]
        if arg.isdigit():
            target = get_group_by_index(ctx.state, int(arg))
            if target is None:
                return "❌ 序号不存在，用 /status 查看列表"
        else:
            target = arg

    if target is None:
        set_global_state(ctx.state, True)
        return "✅ 全局已开启"
    else:
        set_global_state(ctx.state, True)
        return f"✅ 群已开启"


@cmd("off", desc="关闭Bot，用法: /off [群序号]")
async def handle_off(ctx):
    target = None
    if ctx.args:
        arg = ctx.args[0]
        if arg.isdigit():
            target = get_group_by_index(ctx.state, int(arg))
            if target is None:
                return "❌ 序号不存在，用 /status 查看列表"
        else:
            target = arg

    if target is None:
        set_global_state(ctx.state, True)
        return "⏸️ 全局已关闭"
    else:
        set_global_state(ctx.state, True)

        return f"⏸️ 群已关闭"


@cmd("status", desc="查看Bot状态")
async def handle_status(ctx):
    lines = [f"🌐 全局：{'开启' if ctx.state.get('global_enabled', True) else '关闭'}"]
    if not ctx.state["groups"]:
        lines.append("📭 还没有群记录，先在群里 @Bot 一次")
    else:
        lines.append("📋 群列表：")
        for i, (gid, enabled) in enumerate(ctx.state["groups"].items(), 1):
            flag = "✅" if enabled is True else ("⏸️" if enabled is False else "🌐")
            gname = await get_group_name(gid)
            lines.append(f"  {i}. {gname} {flag}")
        lines.append("用法：/off 1 或 /on 2")
    return "\n".join(lines)
