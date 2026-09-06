import asyncio
import random
import time
from core.ai import get_ai_reply
from core.router import route
from core.memory import get_history_text, build_prompt
from services import image_cache
from services.actions import send_text
from config import (
    IMAGE_WAIT_TIMEOUT,
    IMAGE_WAIT_MAX,
    ENABLE_IMAGE_PLACEHOLDER,
    IMAGE_ACTION_COOLDOWN,
)

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
            await send_text(
                group_id,
                user_id,
                action_tag,
                msg_id,
                is_group=is_group,
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

    return await get_ai_reply(
        content,
        user_id=user_id,
        group_id=group_id,
        prompt_override=prompt,
        timeout=60,  # 主模型生成 120 tokens，给足时间
    )
