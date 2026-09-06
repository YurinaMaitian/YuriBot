import json
import re
from core.ai import get_ai_reply
from config import LIGHT_MODEL_NAME, LIGHT_MODEL_URL, LIGHT_MODEL_KEY

ROUTER_SYSTEM = """你是信息调度员。根据【最近聊天记录】和【当前消息】，判断回复前需要检索哪些信息。

可选模块：
- time：时间情境（几点、在干嘛）
- preference：Bot 的喜好/雷点（番剧、角色、食物等）
- scene：情景记忆（之前聊过什么相关话题）
- persona_bg：Bot 的自身背景事实（坐标、学校、家庭、外貌等设定）
- slang：群梗/黑话/圈内新用法（消息里可能含有你不知道的圈内词、缩写、新梗）
- referenced_images：当前消息明确引用或追问的图片文件名列表。

输出严格 JSON，不要解释，不要 markdown 代码块：
{"time": true/false, "preference": true/false, "scene": true/false, "persona_bg": true/false, "slang": true/false, "referenced_images": ["文件名"]}

判断规则：
- 空消息/纯@ → 结合上文判断
- 问"在干嘛""现在""在吗" → time
- 问"你喜欢什么""推荐""xx怎么样"、提到具体作品/角色/食物 → preference
- 追问"刚才""之前""你不是说" → scene
- 问"你在哪""哪里人""住哪""哪个学校""多高""家里""背景"等自身设定 → persona_bg
- 消息含圈内黑话/缩写/明显不合字面意思的新用法、或问"xx是什么意思/啥梗" → slang
- referenced_images 的判断（重要）：
  - 历史消息中可能出现旧格式【图片：描述】（全角冒号），那是没有文件名的旧记录，无法引用，一律不要列入 referenced_images
  - 只有半角格式【图片:文件名】里的内容才能作为 filename
  - 只列当前消息明确指代的图，绝不罗列聊天记录里出现的所有图片
  - "第n张图" → 按聊天记录中图片出现的先后顺序数，最早的是第一张
  - "我刚发的图" → 当前发言者本人发的图，不一定是时间最近的那张
  - 当前消息与图片无关 → 必须为空数组
  - 找不到或超出记录范围 → 空数组"""

_KEYS = ("time", "preference", "scene", "persona_bg", "slang")


def _route_rule(user_msg: str) -> dict:
    """规则兜底：LLM 不可用时使用。referenced_images 保守置空（不瞎猜指代）"""
    m = user_msg.lower()
    base = {"referenced_images": []}
    if any(
        k in m
        for k in [
            "刚才",
            "之前",
            "你不是说",
            "刚刚聊",
            "上午",
            "昨天",
            "上次",
            "之前聊",
            "之前说",
            "之前谁",
        ]
    ):
        return {
            **base,
            "time": True,
            "preference": False,
            "scene": True,
            "persona_bg": False,
            "slang": False,
        }
    if any(
        k in m
        for k in [
            "哪里",
            "哪人",
            "住哪",
            "住在",
            "多高",
            "学校",
            "家里",
            "背景",
            "年级",
            "班级",
            "几岁",
        ]
    ):
        return {
            **base,
            "time": True,
            "preference": False,
            "scene": False,
            "persona_bg": True,
            "slang": False,
        }
    if any(k in m for k in ["什么意思", "啥梗", "是什么梗", "什么意思"]):
        return {
            **base,
            "time": False,
            "preference": False,
            "scene": False,
            "persona_bg": False,
            "slang": True,
        }
    if any(
        k in m
        for k in [
            "喜欢",
            "推荐",
            "怎么样",
            "推什么",
            "本命",
            "最爱",
            "讨厌",
            "你觉得",
            "如何",
            "好吗",
        ]
    ):
        return {
            **base,
            "time": True,
            "preference": True,
            "scene": False,
            "persona_bg": False,
            "slang": False,
        }
    return {
        **base,
        "time": True,
        "preference": False,
        "scene": False,
        "persona_bg": False,
        "slang": False,
    }


_FILENAME_RE = re.compile(r"^[0-9a-fA-F]{32}\.[a-zA-Z]{2,5}$")


def _parse_router_json(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{[^{}]*\}", raw)
    if not m:
        return None
    try:
        plan = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(plan, dict):
        return None
    if not any(k in plan for k in _KEYS):
        return None

    images = plan.get("referenced_images", [])
    if not isinstance(images, list):
        images = []
    valid, dropped = [], []
    for i in images:
        s = str(i).strip()
        (valid if _FILENAME_RE.match(s) else dropped).append(s)
    if dropped:
        print(f"[Router] 过滤非法图片引用: {dropped}")

    return {
        "time": bool(plan.get("time", False)),
        "preference": bool(plan.get("preference", False)),
        "scene": bool(plan.get("scene", False)),
        "persona_bg": bool(plan.get("persona_bg", False)),
        "slang": bool(plan.get("slang", False)),
        "referenced_images": valid,
    }


async def route(user_msg: str, history: str = "") -> dict:
    """
    LLM 路由（Qwen3-8B 免费 + 聊天记录上下文）+ 规则兜底。
    history: 带相对时间戳的最近聊天记录文本，与主 prompt 共用同一份。
    """
    plan_input = (
        f"【最近聊天记录】\n{history}\n\n【当前消息】\n{user_msg}"
        if history
        else user_msg
    )
    try:
        raw = await get_ai_reply(
            user_message=plan_input,
            system_override=ROUTER_SYSTEM,
            max_tokens=200,
            temperature=0.0,
            model=LIGHT_MODEL_NAME,
            api_url=LIGHT_MODEL_URL,
            api_key=LIGHT_MODEL_KEY,
            timeout=30,
            enable_thinking=False,  # 关键：Qwen3-8B 默认开思维链，关掉
        )
        plan = _parse_router_json(raw)
        if plan:
            print(f"[Router] LLM命中: {plan}, 消息:{user_msg[:30]!r}")
            return plan
        print(f"[Router] LLM输出无法解析: {raw[:100]!r}")
    except Exception as e:
        print(f"[Router] LLM异常: {type(e).__name__}: {e}")

    plan = _route_rule(user_msg)
    print(f"[Router] 规则兜底: {plan}, 消息:{user_msg[:30]!r}")
    return plan
