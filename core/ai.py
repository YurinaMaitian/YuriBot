import asyncio
import os
import json
import aiohttp
from config import (
    MAIN_MODEL_URL,
    MAIN_MODEL_KEY,
    MAIN_MODEL_NAME,
    MAIN_MODEL_MAX_TOKENS,
    MAIN_MODEL_TEMP,
    LIGHT_MODEL_URL,
    LIGHT_MODEL_KEY,
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_MAX_TOKENS,
    LIGHT_MODEL_TEMP,
    APP_ID,
    APP_SECRET,
    TOKEN_URL,
)
from core.memory import build_prompt

PERSONA_DIR = "/home/minds/qqbot/data/persona"

_current_token = None


async def refresh_token():
    global _current_token
    async with aiohttp.ClientSession() as session:
        r = await session.post(
            TOKEN_URL, json={"appId": APP_ID, "clientSecret": APP_SECRET}
        )
        data = await r.json()
        if "access_token" not in data:
            raise Exception(f"Token失败: {data}")
        _current_token = data["access_token"]
        print(f"[Token] 已刷新: {_current_token[:10]}...")
        return _current_token


async def get_token():
    if _current_token is None:
        return await refresh_token()
    return _current_token


def load_persona():
    core = ""
    few_shot = ""
    core_path = os.path.join(PERSONA_DIR, "core.txt")
    few_path = os.path.join(PERSONA_DIR, "few_shot.txt")

    if os.path.exists(core_path):
        with open(core_path, "r", encoding="utf-8") as f:
            core = f.read().strip()
    if os.path.exists(few_path):
        with open(few_path, "r", encoding="utf-8") as f:
            few_shot = f.read().strip()

    system = core
    if few_shot:
        system += "\n\n【回复示例】\n" + few_shot
    return system


SYSTEM_PROMPT = load_persona()


def _extract_final_answer(reasoning: str) -> str:
    """
    从推理模型的思维链里提取最终答案。
    关键：推理模型把最终答案写在思维链末尾，前面都是分析过程。
    """
    if not reasoning:
        return "AI出错了"

    # 去掉开头常见的思维链标题
    reasoning = re.sub(
        r"^(Thinking Process:|思考过程：|分析过程：)\s*",
        "",
        reasoning,
        flags=re.IGNORECASE,
    )

    # 按段落分割（双换行通常是段落分隔）
    paragraphs = [p.strip() for p in reasoning.split("\n\n") if p.strip()]

    # 从后往前找，找第一个"像最终答案"的段落
    skip_keywords = [
        "分析",
        "思考",
        "Process",
        "观察",
        "步骤",
        "方案",
        "草拟",
        "尝试",
        "评估",
        "计划",
        "优化",
        "确认",
        "总结",
        "提炼",
        "Analyze",
        "Observe",
        "Evaluate",
        "Plan",
        "Step",
        "Request",
    ]

    for para in reversed(paragraphs):
        clean = re.sub(r"\*\*|\*|#|`", "", para).strip()

        # 跳过纯编号段落（如 "1. **分析**"）
        if re.match(r"^\d+\.\s*\*\*", clean):
            continue
        # 跳过含元信息关键词的
        if any(k in clean for k in skip_keywords):
            continue
        # 跳过过短
        if len(clean) < 10:
            continue

        return clean

    # 兜底：取最后一段，清理 markdown
    if paragraphs:
        return re.sub(r"\*\*|\*|#|`", "", paragraphs[-1]).strip()

    return "AI出错了"


async def get_ai_reply(
    user_message: str,
    user_id: str = "",
    group_id: str = "",
    system_override: str = None,
    max_tokens: int = None,
    temperature: float = None,
    model: str = None,
    api_url: str = None,
    api_key: str = None,
    use_flash: bool = False,
) -> str:
    """
    统一 AI 调用入口。
    不传 model/api_url/api_key 时，默认使用主模型（DeepSeek）。
    传了则使用指定模型（如硅基流动的 Qwen3.5-4B）。
    """
    if not user_message or not user_message.strip():
        return "……（没听见）"

    # 默认主模型
    if model is None:
        model = MAIN_MODEL_NAME
    if api_url is None:
        api_url = MAIN_MODEL_URL
    if api_key is None:
        api_key = MAIN_MODEL_KEY
    if max_tokens is None:
        max_tokens = MAIN_MODEL_MAX_TOKENS
    if temperature is None:
        temperature = MAIN_MODEL_TEMP

    if system_override is None:
        full_prompt = await build_prompt(group_id, user_id, user_message)
    else:
        full_prompt = user_message

    system = system_override or SYSTEM_PROMPT

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": full_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(2):
                async with session.post(
                    api_url, headers=headers, json=payload, timeout=15
                ) as r:
                    raw_text = await r.text()
                    print(
                        f"[AI原始返回] 模型:{model}, 状态:{r.status}, 尝试:{attempt + 1}"
                    )

                    if r.status != 200:
                        print(f"[AI API错误] {raw_text[:200]}")
                        if attempt == 0:
                            await asyncio.sleep(0.5)
                            continue
                        return "AI服务开小差了，稍后再试"

                    data = json.loads(raw_text)
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    reply = msg.get("content", "").strip()

                    # 兜底：推理模型可能 content 为空
                    if not reply:
                        reasoning = msg.get("reasoning_content", "").strip()
                        if reasoning:
                            # 找 reasoning 里最后一行有实质内容的（非编号、非元信息）
                            lines = [
                                l.strip() for l in reasoning.split("\n") if l.strip()
                            ]
                            for line in reversed(lines):
                                if line.startswith(
                                    ("1.", "2.", "3.", "4.", "5.", "*", "-", "**")
                                ):
                                    continue
                                if any(
                                    k in line
                                    for k in ["分析", "思考", "Process", "方案", "草拟"]
                                ):
                                    continue
                                if len(line) > 3:
                                    reply = line[:100]
                                    break
                            # 兜底：推理模型可能 content 为空
                            if not reply:
                                reasoning = msg.get("reasoning_content", "").strip()
                                if reasoning:
                                    reply = _extract_final_answer(reasoning)
                                    print(f"[AI] 从 reasoning 提取: {reply[:50]}")

                    if reply:
                        return reply

                    print("[AI返回空，重试中...]")
                    await asyncio.sleep(0.5)

            return "……（正在刷手机，没注意看）"

    except Exception as e:
        print(f"[AI异常] {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return "AI出错了"
