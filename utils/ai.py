import asyncio
import os
import json
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL, APP_ID, APP_SECRET, TOKEN_URL
from utils.memory import build_prompt

PERSONA_DIR = "/home/minds/qqbot/data/persona"

# ========== Token 管理（从 main.py 移过来）==========
_current_token = None

async def refresh_token():
    global _current_token
    async with aiohttp.ClientSession() as session:
        r = await session.post(TOKEN_URL, json={
            "appId": APP_ID, "clientSecret": APP_SECRET
        })
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

# ========== 人设加载 ==========
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

# ========== AI 回复 ==========
async def get_ai_reply(
    user_message: str,
    user_id: str = "",
    group_id: str = "",
    system_override: str = None,
    max_tokens: int = 120,
    use_flash: bool = False
) -> str:
    if not user_message or not user_message.strip():
        return "……（没听见）"
    
    if system_override is None:
        full_prompt = await build_prompt(group_id, user_id, user_message)
    else:
        full_prompt = user_message
    
    system = system_override or SYSTEM_PROMPT
    model = "deepseek-v4-flash" if use_flash else "deepseek-chat"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": full_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3 if system_override else 0.8
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(2):
                async with session.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=15) as r:
                    raw_text = await r.text()
                    print(f"[AI原始返回] 模型:{model}, 状态:{r.status}, 尝试:{attempt+1}")
                    
                    if r.status != 200:
                        print(f"[AI API错误] {raw_text[:200]}")
                        if attempt == 0:
                            await asyncio.sleep(0.5)
                            continue
                        return "AI服务开小差了，稍后再试"
                    
                    data = json.loads(raw_text)
                    reply = data["choices"][0]["message"]["content"].strip()
                    
                    if reply:
                        return reply
                    
                    print(f"[AI返回空，重试中...]")
                    await asyncio.sleep(0.5)
            
            return "……（正在刷手机，没注意看）"
                
    except Exception as e:
        print(f"[AI异常] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return "AI出错了"
