from handlers.admin import handle_on, handle_off, handle_status
from handlers.chat import handle_chat
from tools.latex import render_latex
from services.actions import send_text, send_image


async def handle_latex(
    group_id: str, user_id: str, clean_content: str, msg_id: str, is_group: bool = True
):
    """处理 /latex 指令"""
    formula = clean_content[7:].strip()

    if not formula:
        await send_text(
            group_id,
            user_id,
            "用法：/latex \\int_0^1 x^2 dx",
            msg_id,
            is_group=is_group,
        )
        return True

    img_path = render_latex(formula)
    if not img_path:
        await send_text(
            group_id,
            user_id,
            "公式渲染失败了，检查一下语法？",
            msg_id,
            is_group=is_group,
        )
        return True

    success = await send_image(
        group_id,
        user_id,
        img_path,
        description=f"LaTeX公式：{formula[:50]}",
        msg_id=msg_id,
        is_group=is_group,
    )

    if not success:
        await send_text(
            group_id, user_id, "图片上传失败了...", msg_id, is_group=is_group
        )

    return True


async def dispatch_command(cmd: str, user_id: str, target: str = None) -> str:
    if cmd == "on":
        return await handle_on(user_id, target)
    elif cmd == "off":
        return await handle_off(user_id, target)
    elif cmd == "status":
        return await handle_status(user_id)
    else:
        return f"❓ 未知指令: /{cmd}\n可用: /on /off /status /latex"
