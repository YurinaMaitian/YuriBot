from utils.state import load_state, save_state, is_group_enabled, set_group_state, set_global_state, get_group_by_index

state = load_state()

async def handle_on(user_id: str, target=None):
    if target is None:
        set_global_state(state, True)
        return "✅ 全局已开启"
    else:
        set_group_state(state, target, True)
        return f"✅ 群已开启"

async def handle_off(user_id: str, target=None):
    if target is None:
        set_global_state(state, False)
        return "⏸️ 全局已关闭"
    else:
        set_group_state(state, target, False)
        return f"⏸️ 群已关闭"

async def handle_status(user_id: str):
    lines = [f"🌐 全局：{'开启' if state.get('global_enabled', True) else '关闭'}"]
    if not state["groups"]:
        lines.append("📭 还没有群记录，先在群里 @Bot 一次")
    else:
        lines.append("📋 群列表：")
        for i, (gid, enabled) in enumerate(state["groups"].items(), 1):
            flag = "✅" if enabled is True else ("⏸️" if enabled is False else "🌐")
            lines.append(f"  {i}. {gid[:14]}... {flag}")
        lines.append("用法：/off 1 或 /on 2")
    return "\n".join(lines)
