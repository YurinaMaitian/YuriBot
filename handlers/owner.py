import asyncio
import aiosqlite
from datetime import datetime
import re
from config import BOT_OWNER
from core.registry import cmd
from services.user_manager import set_nickname, set_group_name, get_nickname
from services.db import DB_PATH


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
    from services import meme_store

    asyncio.create_task(meme_store.sync_index(fn))

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
    from services import meme_store

    asyncio.create_task(meme_store.sync_index(fn))

    return f"✅ 已订正 {fn[:12]}… 的描述并锁定"


from services import slang_store


@cmd("梗", desc="[主人] 教/改群梗，用法: /梗 词 = 解释", hidden=True)
async def slang_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    if "=" not in ctx.raw:
        return "用法：/梗 词 = 解释\n示例：/梗 圣遗物歪 = 强化时副词条没随机到想要的属性，全加防御生命就叫歪了\n（对已存在的词重复教学即为修改）"
    term, explanation = ctx.raw.split("=", 1)
    term, explanation = term.strip(), explanation.strip()
    if not term or not explanation:
        return "❌ 词和解释都不能为空"
    ok = await slang_store.add_slang(term, explanation)
    if not ok:
        return "❌ 入库失败，看日志"
    return f"✅ 已收录【{term}】"


@cmd("梗del", desc="[主人] 删除群梗，用法: /梗del 词", hidden=True)
async def slangdel_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    term = ctx.raw.strip()
    if not term:
        return "用法：/梗del 词"
    if await slang_store.delete_slang(term):
        return f"✅ 已删除【{term}】"
    return f"❌ 库里没有【{term}】"


@cmd("梗list", desc="[主人] 查看已收录的群梗", hidden=True)
async def slanglist_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    rows = await slang_store.list_slang()
    if not rows:
        return "梗百科是空的，用 /梗 词 = 解释 来教她"
    lines = [f"📚 已收录 {len(rows)} 条："]
    for r in rows:
        lines.append(f"- {r['term']}：{r['explanation'][:40]}")
    return "\n".join(lines)


@cmd("good", desc="[主人] 给bot上一条回复点好评", hidden=True)
async def good_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    return await _rate_last(ctx, 1)


@cmd("bad", desc="[主人] 给bot上一条回复点差评", hidden=True)
async def bad_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    return await _rate_last(ctx, -1)


async def _rate_last(ctx, rating: int) -> str:
    from core.memory import get_context

    ctx_msgs = get_context(ctx.group_id, ctx.user_id)
    last_bot = next((m for m in reversed(ctx_msgs) if m["speaker"] == "bot"), None)
    if not last_bot:
        return "没有找到可评价的回复"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT, user_id TEXT, bot_msg TEXT,
            rating INTEGER, created_at TIMESTAMP)""")
        await db.execute(
            "INSERT INTO feedback_log (group_id, user_id, bot_msg, rating, created_at)"
            " VALUES (?,?,?,?,?)",
            (
                ctx.group_id,
                ctx.user_id,
                last_bot["content"][:200],
                rating,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        await db.commit()
    return "✅ 已记录" if rating > 0 else "📝 已记录，建议把这条 case 加进测试集"


import json as _json


@cmd("stats", desc="[主人] 今日运行指标汇总", hidden=True)
async def stats_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"

    import os
    from datetime import datetime
    from config import DATA_DIR

    today = ctx.raw.strip() or datetime.now().strftime("%Y-%m-%d")
    lines = []

    # ===== 1. AI 调用指标（jsonl 聚合） =====
    path = os.path.join(DATA_DIR, "metrics", "ai_calls.jsonl")
    by_tag: dict = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if not str(rec.get("ts", "")).startswith(today):
                    continue
                s = by_tag.setdefault(
                    rec["tag"], {"n": 0, "ok": 0, "lat": 0, "pt": 0, "ct": 0}
                )
                s["n"] += 1
                s["ok"] += 1 if rec.get("ok") else 0
                s["lat"] += rec.get("latency_ms") or 0
                s["pt"] += rec.get("prompt_tokens") or 0
                s["ct"] += rec.get("completion_tokens") or 0

    if by_tag:
        lines.append(f"📊 {today} AI调用：")
        for tag, s in sorted(by_tag.items()):
            avg = s["lat"] // max(1, s["n"])
            lines.append(
                f"  {tag}: {s['n']}次 成功{s['ok']} 均延迟{avg}ms"
                f" tok {s['pt']}+{s['ct']}"
            )
    else:
        lines.append("📊 今日暂无AI调用")

    # ===== 2. judge / 表情包 / 反馈（SQLite 聚合） =====
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            async with db.execute(
                "SELECT source, COUNT(*) FROM web_search_log"
                " WHERE created_at >= ? GROUP BY source",
                (today,),
            ) as cur:
                rows = dict(await cur.fetchall())
            api_n, cache_n = rows.get("api", 0), rows.get("cache", 0)
            if api_n or cache_n:
                total = api_n + cache_n
                lines.append(
                    f"🔍 搜索: 真搜{api_n}次 缓存{cache_n}次"
                    f"(命中率{cache_n * 100 // max(1, total)}%)"
                )
        except Exception:
            pass

        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(reply),0), COALESCE(AVG(judge_latency_ms),0)"
            " FROM interject_log WHERE created_at >= ?",
            (today,),
        ) as cur:
            n, replied, avg_lat = await cur.fetchone()
        if n:
            lines.append(
                f"🧑‍⚖️ 接话: {n}次 回复{replied}次({replied * 100 // n}%)"
                f" 均延迟{int(avg_lat)}ms"
            )
        else:
            lines.append("🧑‍⚖️ 接话: 今日暂无")

        try:
            async with db.execute(
                "SELECT action, COUNT(*) FROM meme_log"
                " WHERE created_at >= ? GROUP BY action",
                (today,),
            ) as cur:
                meme_rows = await cur.fetchall()
            if meme_rows:
                dist = " ".join(f"{a}×{c}" for a, c in meme_rows)
                lines.append(f"🖼 表情包: {dist}")
        except Exception:
            pass

        try:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS feedback_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT, user_id TEXT, bot_msg TEXT,
                    rating INTEGER, created_at TIMESTAMP)"""
            )
            async with db.execute(
                "SELECT rating, COUNT(*) FROM feedback_log"
                " WHERE created_at >= ? GROUP BY rating",
                (today,),
            ) as cur:
                fb = dict(await cur.fetchall())
            if fb:
                lines.append(f"👍{fb.get(1, 0)} 👎{fb.get(-1, 0)}")
        except Exception:
            pass

        try:
            async with db.execute(
                "SELECT COUNT(*) FROM interject_log"
                " WHERE created_at >= ? AND reason LIKE '复查%'",
                (today,),
            ) as cur:
                n_drop = (await cur.fetchone())[0]
            if n_drop:
                lines.append(f"🔁 复查丢弃: {n_drop}条")
        except Exception:
            pass

    return "\n".join(lines)


from services import pdf_tool


@cmd(
    "pdf",
    desc="[主人] 解析群里的PDF：/pdf [文件名前缀]，或查看已解析的总结",
    hidden=True,
)
async def pdf_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    if not ctx.is_group:
        return "这个指令只能在群聊用～"
    hit = await pdf_tool._find_doc(ctx.group_id, ctx.raw.strip())
    if not hit:
        return "这个群还没有登记过 PDF，先发一份上来"
    doc_id, filename, status = hit
    if status == pdf_tool.STATUS_DONE:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT summary FROM pdf_docs WHERE doc_id=?", (doc_id,)
            ) as cur:
                row = await cur.fetchone()
        return f"📄 {filename}\n{row[0] if row else ''}"
    if status == pdf_tool.STATUS_PROCESSING:
        return "还在看，等下～"
    if status in (pdf_tool.STATUS_REJECTED, pdf_tool.STATUS_FAILED):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT reject_reason FROM pdf_docs WHERE doc_id=?", (doc_id,)
            ) as cur:
                row = await cur.fetchone()
        return f"这份看不了：{row[0] if row else status}"
    # pending → 启动处理
    asyncio.create_task(pdf_tool.process_doc(doc_id, ctx.group_id, ctx.msg_id))
    return f"行，等我翻翻《{filename}》，看完叫你"


@cmd(
    "pdfdel",
    desc="[主人] 删除PDF记录并重置：/pdfdel [文件名前缀]，重处理不用再发文件",
    hidden=True,
)
async def pdfdel_cmd(ctx):
    if not _is_owner(ctx.user_id):
        return "⛔ 你没有权限使用这个指令"
    hit = await pdf_tool._find_doc(ctx.group_id, ctx.raw.strip())
    if not hit:
        return "没找到对应记录"
    doc_id, filename, status = hit
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pdf_docs WHERE doc_id=?", (doc_id,))
        await db.execute("DELETE FROM pdf_chunks WHERE doc_id=?", (doc_id,))
        await db.commit()
    from services import vector_store

    await vector_store.delete_doc_points(doc_id)
    return f"🗑 已删除《{filename}》的记录（文件还在磁盘上），重新 /pdf 即可再处理"
