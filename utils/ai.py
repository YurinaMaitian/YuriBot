import os
import aiohttp
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL
from utils.scene import build_context  # ← 新增导入

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


async def get_ai_reply(user_message: str, user_id: str = "") -> str:
    if not user_message or not user_message.strip():
        return "……（没听见）"

    # 组装带情境的 prompt
    full_prompt = build_context(user_id, user_message)

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
        "max_tokens": 120,
        "temperature": 0.8,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DEEPSEEK_URL, headers=headers, json=payload, timeout=15
            ) as r:
                raw_text = await r.text()
                print(f"[AI原始返回] 状态:{r.status}")

                if r.status != 200:
                    return "AI服务开小差了，稍后再试"

                data = json.loads(raw_text)
                reply = data["choices"][0]["message"]["content"].strip()

                if not reply:
                    return "……（正在刷手机，没注意看）"

                return reply

    except Exception as e:
        print(f"[AI异常] {type(e).__name__}: {e}")
        return "AI出错了"
