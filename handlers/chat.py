import asyncio
import random
import time
from core.ai import get_ai_reply
from core.router import route
from core.memory import get_history_text, build_prompt
from services import image_cache
from config import (
    IMAGE_WAIT_TIMEOUT,
    IMAGE_WAIT_MAX,
    ENABLE_IMAGE_PLACEHOLDER,
    IMAGE_ACTION_COOLDOWN,
)

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "用手机联网搜索实时信息：游戏版本卡池、新番消息、时事、不认识的梗。"
        "群友让你查、或你要说不确定的事实时使用，别凭印象答时效性问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "想查的内容，自然语言一句话直接写，不要写你猜的年份版本号",
                },
                "freshness": {
                    "type": "string",
                    "enum": ["oneWeek", "oneMonth", "oneYear", "noLimit"],
                    "description": "时效范围：游戏版本/新闻/近期活动用 oneMonth（默认），"
                    "老梗、百科、历史内容用 noLimit",
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "之前搜过的结果可能有错、或被群友纠正过时设 true："
                    "跳过缓存重新搜索并覆盖旧记录",
                },
            },
            "required": ["query"],
        },
    },
}
# 占位动作冷却：group_id → 上次发动作的时间戳（"" 表示私聊）
_last_action_time: dict[str, float] = {}

_ACTION_TIERS = [
    (2, ["（凑近）", "（眯眼）", "（歪头）"]),
    (5, ["（研究）", "（思索）", "（盯）"]),
    (10, ["（挠头）", "（困惑）", "（加载中）"]),
    (30, ["（翻记录）", "（回忆）", "（刚注意到）"]),
    (float("inf"), ["（突然想起来）", "（啊——）", "（漏看了）"]),
]


def _pick_action(delta: float) -> str:
    for threshold, actions in _ACTION_TIERS:
        if delta < threshold:
            return random.choice(actions)
    return _ACTION_TIERS[-1][1][0]


async def _wait_for_images(
    group_id: str, user_id: str, filenames: list, msg_id: str, is_group: bool
) -> tuple[float, str, list[str]] | None:
    """
    等待被引用的图片解析完成。
    返回 (等待秒数, 动作标签, 成功解析的文件名列表)；
    None = 引用的图全部不可用（超龄/超时），调用方静默丢弃整条消息。
    部分图失败时不再丢弃整句，只跳过坏图（调用方在 prompt 里注明）。
    """
    start = time.time()
    need_wait = []
    resolved = []

    for fn in filenames:
        info = await image_cache.get_image(fn)
        if info and info["status"] == "success":
            resolved.append(fn)
            continue
        if info and info["status"] == "blocked":
            continue
        age = (
            (time.time() - info["created_at"].timestamp())
            if info and info["created_at"]
            else 0
        )
        if age > IMAGE_WAIT_MAX:
            print(f"[图片等待] {fn} 超龄 {age:.0f}s，跳过")
            continue
        need_wait.append(fn)

    if not need_wait and not resolved:
        return None  # 引用的图全不可用，整条丢弃（维持拟人"忘了回复"）

    action_tag = ""
    if need_wait and ENABLE_IMAGE_PLACEHOLDER:
        now = time.time()
        if now - _last_action_time.get(group_id, 0) > IMAGE_ACTION_COOLDOWN:
            action_tag = _pick_action(0)
            from services.sender import enqueue_chat  # 队列内会带 memory_tag 记记忆

            await enqueue_chat(
                group_id,
                user_id,
                action_tag,
                msg_id,
                is_group=is_group,
                priority=True,  # 插队首，先于正常回复发出
                memory_tag="[动作] ",
            )
            _last_action_time[group_id] = now

    for fn in need_wait:
        ev = await image_cache.subscribe(fn)
        if ev is None:
            # 订阅时已终态，补查一次（可能刚好解析完）
            info = await image_cache.get_image(fn)
            if info and info["status"] == "success":
                resolved.append(fn)
            continue
        try:
            await asyncio.wait_for(ev.wait(), timeout=IMAGE_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            info = await image_cache.get_image(fn)
            if info and info["status"] == "success":
                resolved.append(fn)  # Event 丢失兜底：超时瞬间刚好完成
            else:
                print(f"[图片等待超时] {fn}，跳过这张图")
            continue
        # 正常唤醒，二次确认结果
        info = await image_cache.get_image(fn)
        if info and info["status"] == "success":
            resolved.append(fn)

    return (time.time() - start, action_tag, resolved)


async def handle_chat(
    content: str,
    user_id: str = "",
    group_id: str = "",
    msg_id: str = "",
    is_group: bool = True,
    prompt_hint: str = "",
) -> str | None:
    """
    AI 聊天入口。返回 None 表示静默丢弃（拟人"忘了回复"），调用方不要发消息。
    """

    # ===== B站分享：卡片反查 / 链接解析 =====
    from services import bili_tool

    video_note = ""
    card_title, preview_url = bili_tool.detect_bili_card(content)
    if card_title:
        vid, how = await bili_tool.video_from_card(card_title, preview_url)
        if not vid:
            video_note = (
                f"\n\n【系统提示】群友分享了B站视频卡片《{card_title}》，"
                "按标题没搜到对应视频。用人设自然地请对方直接发链接，可顺带吐槽标题。"
            )
        else:
            video_note = "\n\n" + await bili_tool.build_video_block(vid)
            if how == "guessed":
                video_note += (
                    "\n（此视频是按标题搜索匹配的第一个结果，如不对群友会纠正）"
                )
    else:
        vid, p = await bili_tool.resolve_bv(content)
        if vid:
            video_note = "\n\n" + await bili_tool.build_video_block(vid, p)

    # 1. 历史 + 路由（两者共用，后续 build_prompt 复用不重复调用）
    history_text = await get_history_text(group_id, user_id)
    plan = await route(content, history_text)

    # 2. 有待解析的图片引用 → 占位动作 + 异步等待
    referenced = plan.get("referenced_images") or []
    delta, action, resolved = 0.0, "", []
    if referenced:
        result = await _wait_for_images(group_id, user_id, referenced, msg_id, is_group)
        if result is None:
            return None  # 静默丢弃
        delta, action, resolved = result

    # 3. 组装 prompt 并调用主模型
    prompt = await build_prompt(
        group_id, user_id, content, plan=plan, history_text=history_text
    )

    from services import pdf_tool

    if plan.get("docs"):
        from services import pdf_tool

        doc_block = await pdf_tool.retrieve_for(group_id, content)
        if doc_block:
            prompt += (
                "\n\n"
                + doc_block
                + "\n（上面是群里文档的相关内容；群友问文档内容时据此回答"
                "并带上 P页码 引用，与文档无关则忽略此块。）"
            )

    if video_note:
        prompt += video_note

    if delta > 0.5:
        prompt += f"\n\n【时间感知】你刚才花了 {delta:.0f} 秒才看清图。"
        if action:
            prompt += f"\n【动作状态】你的动作是：{action}"
        prompt += "\n接着这个动作自然回复，不要解释你在干嘛。"

    if referenced and len(resolved) < len(referenced):
        prompt += (
            f"\n\n【系统提示】群友引用的图中，有 {len(referenced) - len(resolved)} 张"
            "没能看清（解析超时），回复时可以自然带过或请对方重发，不要硬编内容。"
        )
    if not content or not content.strip():
        prompt += "\n\n对方@了你一下，应该是想让你接话回复点什么。"

    from core.ai import get_ai_reply_with_tools, SYSTEM_PROMPT

    async def _tool_executor(name, args):
        if name == "web_search":
            from services import web_search

            return await web_search.web_search(
                args.get("query", ""),
                freshness=args.get("freshness", "oneMonth"),
                force_refresh=bool(args.get("force_refresh", False)),
                group_id=group_id,
                user_id=user_id,
            )
        return f"（未知工具：{name}）"

    return await get_ai_reply_with_tools(
        SYSTEM_PROMPT,
        prompt,
        tools=[WEB_SEARCH_TOOL],
        tool_executor=_tool_executor,
        max_rounds=3,
    )
