import asyncio
import re
from config import BOT_OWNER
from core.registry import cmd
from services.user_manager import set_nickname, set_group_name, get_nickname


def _is_owner(user_id: str) -> bool:
    return user_id == BOT_OWNER


def _parse_target(text: str) -> tuple[str, str]:
    text = text.strip()
    if text.startswith("<@"):
        end = text.find(">")
        if end != -1:
            oid = text[2:end]
            rest = text[end + 1 :].strip()
            return oid, rest
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return text, ""


def _sanitize_log(text: str) -> str:
    """脱敏：隐藏 Token、API Key、Secret 等"""
    # 替换 access_token
    text = re.sub(
        r'access_token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]{10,}',
        "access_token: ***",
        text,
    )
    # 替换 QQBot Token
    text = re.sub(r"QQBot\s+[a-zA-Z0-9_-]{20,}", "QQBot ***", text)
    # 替换 DeepSeek API Key
    text = re.sub(r"sk-[a-zA-Z0-9]{20,}", "sk-***", text)
    # 替换 APP_SECRET
    text = re.sub(
        r'clientSecret["\']?\s*[:=]\s*["\']?[a-zA-Z0-9]{10,}', "clientSecret: ***", text
    )
    # 替换 plain_token / signature
    text = re.sub(r'"plain_token":\s*"[^"]+"', '"plain_token": "***"', text)
    text = re.sub(r'"signature":\s*"[^"]+"', '"signature": "***"', text)
    return text


@cmd("myid", desc="查看自己的 openid")
async def myid_cmd(ctx):
    return f"你的 openid：\n`{ctx.user_id}`"


@cmd("setnick", desc="[主人] 设置群友昵称")
async def setnick_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    target_id, nickname = _parse_target(ctx.raw)
    if not target_id or not nickname:
        return "用法：/setnick <openid> <昵称>\n示例：/setnick E98EFE5B1DE766EBC8307244C2332E9F 小红"

    await set_nickname(target_id, nickname)
    return f"✅ 已设置 {target_id[:8]}... 的昵称为：{nickname}"


@cmd("lookup", desc="[主人] 查询某人当前昵称")
async def lookup_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    oid = ctx.raw.strip()
    if not oid:
        return "用法：/lookup <openid>"

    nick = await get_nickname(oid)
    return f"🔍 {oid[:8]}... 当前昵称：{nick}"


@cmd("setgroupname", desc="[主人] 设置群名称")
async def setgroupname_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    if not ctx.is_group:
        return "这个指令只能在群聊使用～"

    name = ctx.raw.strip()
    if not name:
        return "用法：/setgroupname 群名称"

    await set_group_name(ctx.group_id, name)
    return f"✅ 已设置本群名称为：{name}"


@cmd("logs", desc="[主人] 查看最近日志，用法: /logs 30")
async def logs_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    # 解析条数，默认 30
    try:
        n = int(ctx.raw.strip()) if ctx.raw.strip() else 30
    except ValueError:
        n = 30
    n = min(n, 100)  # 最多 100 条

    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            "qqbot",
            "-n",
            str(n),
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        if proc.returncode != 0:
            return f"❌ 读取日志失败：{stderr.decode()[:200]}"

        raw = stdout.decode("utf-8", errors="replace")
        if not raw.strip():
            return "📭 暂无日志"

        # 脱敏
        clean = _sanitize_log(raw)

        # 截断到合理长度（QQ 分条发送）
        if len(clean) > 3000:
            clean = clean[-3000:]
            clean = "...（前面截断）\n" + clean

        return f"📋 最近 {n} 条日志：\n```\n{clean}\n```"

    except asyncio.TimeoutError:
        return "⏱️ 读取日志超时"
    except Exception as e:
        return f"❌ 异常：{e}"
