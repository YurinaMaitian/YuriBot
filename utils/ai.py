import os
import aiohttp
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL
from utils.memory import build_prompt

PERSONA_DIR = "/home/minds/qqbot/data/persona"


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


async def get_ai_reply(user_message: str, user_id: str = "", group_id: str = "") -> str:
    if not user_message or not user_message.strip():
        return "……（没听见）"

    # 组装带上下文的 prompt
    full_prompt = build_prompt(group_id, user_id, user_message)

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": full_prompt},
        ],
        "max_tokens": 250,
        "temperature": 0.8,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DEEPSEEK_URL, headers=headers, json=payload, timeout=15
            ) as r:
                raw_text = await r.text()

                if r.status != 200:
                    return "AI服务开小差了，稍后再试"

                data = json.loads(raw_text)
                reply = data["choices"][0]["message"]["content"].strip()

                if not reply:
                    return "……（正在刷手机，没注意看）"

                return reply

    except Exception:
        return "AI出错了"
