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


@cmd("setnick", desc="[主人] 设置群友昵称", hidden=True)
async def setnick_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    target_id, nickname = _parse_target(ctx.raw)
    if not target_id or not nickname:
        return "用法：/setnick <openid> <昵称>\n示例：/setnick E98EFE5B1DE766EBC8307244C2332E9F 小红"

    await set_nickname(target_id, nickname)
    return f"✅ 已设置 {target_id[:8]}... 的昵称为：{nickname}"


@cmd("lookup", desc="[主人] 查询某人当前昵称", hidden=True)
async def lookup_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    oid = ctx.raw.strip()
    if not oid:
        return "用法：/lookup <openid>"

    nick = await get_nickname(oid)
    return f"🔍 {oid[:8]}... 当前昵称：{nick}"


@cmd("setgroupname", desc="[主人] 设置群名称", hidden=True)
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


@cmd("logs", desc="[主人] 查看最近日志，用法: /logs 30", hidden=True)
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


from services.state import load_state, save_state


@cmd("aton", desc="[主人] 开启回@（被@时回复@回去）", hidden=True)
async def aton_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    st = load_state()
    st["reply_at_enabled"] = True
    save_state(st)
    return "✅ 回@已开启，群里被@时会@回去"


@cmd("atoff", desc="[主人] 关闭回@", hidden=True)
async def atoff_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    st = load_state()
    st["reply_at_enabled"] = False
    save_state(st)
    return "⏸️ 回@已关闭"


@cmd("syncpanel", desc="[主人] 手动同步指令面板到QQ", hidden=True)
async def syncpanel_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    from services.panel import sync_all_panels

    await sync_all_panels()
    return "✅ 面板同步已触发，结果看日志"


from services import daily_schedule as daily_sched


@cmd("today", desc="[主人] 查看Bot今日日程", hidden=True)
async def today_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    data = daily_sched.get_today_schedule()
    if not data:
        return "今天还没有生成日程（正在回退静态表）。可以用 /reschedule 手动生成。"
    lines = [f"📅 {data['date']}  心情：{data.get('mood', '?')}"]
    for ev in data.get("events") or []:
        lines.append(
            f"  ⚡{ev['start']:02d}:00-{ev['end']:02d}:00 {ev['desc']}（{ev.get('mood', '')}）"
        )
    for b in data.get("blocks", []):
        note = f"（{b['note']}）" if b.get("note") else ""
        lines.append(f"  {b['start']:02d}:00-{b['end']:02d}:00 {b['activity']}{note}")
    return "\n".join(lines)


@cmd("reschedule", desc="[主人] 丢弃并重新生成今日日程", hidden=True)
async def reschedule_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    await daily_sched.reschedule_today()
    data = daily_sched.get_today_schedule()
    if not data:
        return "⚠️ 生成失败，今天回退静态表。稍后再试或看日志。"
    return f"✅ 已重新生成：心情{data.get('mood', '?')}，{len(data.get('blocks', []))}个时段"


from services import image_cache as img_cache


async def _resolve_image(prefix: str) -> tuple[str | None, str | None]:
    """前缀唯一匹配 → (filename, None)；失败 → (None, 错误信息)"""
    matches = await img_cache.find_by_prefix(prefix)
    if not matches:
        return None, f"❌ 没有找到以 {prefix!r} 开头的图片记录"
    if len(matches) > 1:
        names = "、".join(m["filename"][:12] for m in matches)
        return None, f"❌ 前缀匹配到 {len(matches)} 张（{names} 等），请加长前缀"
    return matches[0]["filename"], None


@cmd(
    "imginfo", desc="[主人] 查看图片识别信息，用法: /imginfo <文件名前缀>", hidden=True
)
async def imginfo_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    prefix = ctx.raw.strip()
    if not prefix:
        return (
            "用法：/imginfo <文件名前缀>\n（日志里【图片:xxxx.png】的 xxxx 前几位即可）"
        )
    matches = await img_cache.find_by_prefix(prefix)
    if not matches:
        return f"❌ 没有找到以 {prefix!r} 开头的图片记录"
    lines = []
    for m in matches[:5]:
        label = img_cache.label_of(m["type"])
        lock = " 🔒" if m["manual"] else ""
        ts = str(m["created_at"])[:16] if m["created_at"] else "?"
        lines.append(
            f"#{m['id']} {m['filename'][:20]}… [{m['status']}] 【{label}】{m['description'][:45]}{lock}（{ts}）"
        )
    return "\n".join(lines)


@cmd(
    "imgset",
    desc="[主人] 订正图片类型，用法: /imgset <前缀> <表情包|照片|截图|手绘|其他>",
    hidden=True,
)
async def imgset_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    parts = ctx.raw.split(maxsplit=1)
    if len(parts) != 2:
        return "用法：/imgset <文件名前缀> <类型>\n类型：表情包/照片/截图/手绘/其他\n示例：/imgset B07F 表情包"
    prefix, label = parts[0].strip(), parts[1].strip()
    fn, err = await _resolve_image(prefix)
    if err:
        return err
    t = img_cache.parse_type(label)
    if not t:
        return "❌ 类型必须是：表情包 / 照片 / 截图 / 手绘 / 其他"
    await img_cache.set_manual(fn, img_type=t)
    return f"✅ 已订正 {fn[:12]}… 为【{label}】并锁定（自动重解析不会覆盖）"


@cmd("imgdesc", desc="[主人] 订正图片描述，用法: /imgdesc <前缀> <新描述>", hidden=True)
async def imgdesc_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    parts = ctx.raw.split(maxsplit=1)
    if len(parts) != 2:
        return (
            "用法：/imgdesc <文件名前缀> <新描述>\n示例：/imgdesc B07F 熊猫头憋笑梗图"
        )
    prefix, desc = parts[0].strip(), parts[1].strip()
    if not desc:
        return "❌ 新描述不能为空"
    fn, err = await _resolve_image(prefix)
    if err:
        return err
    await img_cache.set_manual(fn, description=desc)
    return f"✅ 已订正 {fn[:12]}… 的描述并锁定"
