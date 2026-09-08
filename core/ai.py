import asyncio
import os
import json
import time
import aiohttp
from services.http import get_session
from config import (
    DATA_DIR,
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

PERSONA_DIR = os.path.join(DATA_DIR, "persona")

_current_token = None


async def refresh_token():
    global _current_token
    session = get_session()
    async with session.post(
        TOKEN_URL,
        json={"appId": APP_ID, "clientSecret": APP_SECRET},
        timeout=10,
    ) as r:
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


def _log_ai_call(
    tag: str,
    model: str,
    ok: bool,
    latency_ms: float,
    usage: dict,
    finish_reason: str,
    err: str = "",
):
    """JSON 行指标：journalctl 可见 + 落盘聚合。tag 用于归因到业务环节。"""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag,
        "model": model,
        "ok": ok,
        "latency_ms": round(latency_ms),
        "finish_reason": finish_reason,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "err": err[:80],
    }
    line = json.dumps(rec, ensure_ascii=False)
    print(f"[AI指标] {line}")
    try:
        d = os.path.join(DATA_DIR, "metrics")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ai_calls.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


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
    prompt_override: str = None,
    timeout: int = 15,
    enable_thinking: bool = None,
    tag: str = "chat",
) -> str | None:
    """
    统一 AI 调用入口。
    不传 model/api_url/api_key 时，默认使用主模型（DeepSeek）。
    传了则使用指定模型（如硅基流动的 Qwen3.5-4B）。
    失败返回 None（错误不外泄，由调用方决定丢弃/重试/降级）。
    tag：业务环节标签，仅用于指标归因。
    """
    t0 = time.monotonic()

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

    if prompt_override is not None:
        full_prompt = prompt_override
    elif system_override is None:
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

    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking

    def _elapsed():
        return (time.monotonic() - t0) * 1000

    try:
        session = get_session()
        for attempt in range(2):
            try:
                async with session.post(
                    api_url, headers=headers, json=payload, timeout=timeout
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
                        _log_ai_call(
                            tag, model, False, _elapsed(), None, "", f"http {r.status}"
                        )
                        return None

                    data = json.loads(raw_text)
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    reply = (msg.get("content") or "").strip()
                    finish_reason = choice.get("finish_reason") or ""
                    usage = data.get("usage") or {}
                    print(f"[AI] finish_reason={finish_reason}, 长度={len(reply)}")

                    if reply:
                        _log_ai_call(tag, model, True, _elapsed(), usage, finish_reason)
                        return reply

                    print("[AI返回空，重试中...]")
                    await asyncio.sleep(0.5)

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                # 超时/网络错误也走重试，不再直接穿透
                print(f"[AI网络异常] 尝试{attempt + 1}: {type(e).__name__}")
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                _log_ai_call(tag, model, False, _elapsed(), None, "", type(e).__name__)
                return None

        # 两次尝试都失败（空响应或重试耗尽）
        _log_ai_call(tag, model, False, _elapsed(), None, "", "retries_exhausted")
        return None

    except Exception as e:
        print(f"[AI异常] {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        _log_ai_call(tag, model, False, _elapsed(), None, "", type(e).__name__)
        return None


async def get_ai_reply_with_tools(
    system: str,
    user_message: str,
    tools: list,
    tool_executor,
    max_rounds: int = 2,
) -> str | None:
    """
    带 tool calling 的主模型对话。
    模型发起 tool_call → tool_executor(name, args) 执行 → 结果回灌 → 继续直到最终文本。
    tool_executor: async (name, args_dict) -> str

    兜底三层（预算耗尽时不静默消失）：
    ① 最后机会轮前注入 system 提醒"禁止再要工具，立即作答"
    ② 最后轮仍请求工具 → 不执行，忽略该请求
    ③ 最后轮仍无正文 → 返回确定性文案（人设化认怂，不烧 LLM）
    """
    t0 = time.monotonic()
    headers = {
        "Authorization": f"Bearer {MAIN_MODEL_KEY}",
        "Content-Type": "application/json",
    }
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    session = get_session()
    try:
        for rnd in range(max_rounds + 1):  # +1 = 强制收尾轮
            final_round = rnd == max_rounds
            if final_round:
                msgs.append(
                    {
                        "role": "system",
                        "content": "【系统提醒】工具调用次数已用完，禁止再请求工具。"
                        "基于已获得的信息立即给出最终回复，哪怕不完整。",
                    }
                )

            payload = {
                "model": MAIN_MODEL_NAME,
                "messages": msgs,
                "max_tokens": MAIN_MODEL_MAX_TOKENS,
                "temperature": MAIN_MODEL_TEMP,
                "tools": tools,
            }
            async with session.post(
                MAIN_MODEL_URL, headers=headers, json=payload, timeout=60
            ) as r:
                raw = await r.text()
                if r.status != 200:
                    print(f"[AI工具调用] HTTP {r.status}: {raw[:200]}")
                    return None
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[AI工具调用] 响应非JSON: {raw[:200]}")
                    return None
            if not isinstance(data, dict) or "choices" not in data:
                print(f"[AI工具调用] 响应缺choices: {str(data)[:200]}")
                return None

            choice = data["choices"][0]
            msg = choice.get("message", {})
            print(
                f"[AI工具调用] 轮次{rnd}返回 finish_reason={choice.get('finish_reason')}, "
                f"content长度={len(msg.get('content') or '')}, "
                f"tool_calls={len(msg.get('tool_calls') or [])}"
            )
            tool_calls = msg.get("tool_calls") or []

            if tool_calls and not final_round:
                msgs.append(msg)
                for tc in tool_calls:
                    name = tc.get("function", {}).get("name", "")
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    print(f"[tool_call] {name}({str(args)[:80]})")
                    try:
                        result = await tool_executor(name, args)
                    except Exception as e:
                        result = f"（工具执行失败：{type(e).__name__}）"
                    msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": str(result)[:1500],
                        }
                    )
                continue

            if tool_calls and final_round:
                # 最后轮仍要工具：不执行，忽略请求，逼它基于已有信息答
                print("[AI工具调用] 最后轮仍请求工具，强制基于已有信息回复")

            reply = (msg.get("content") or "").strip()
            if reply:
                _log_ai_call(
                    "chat",
                    MAIN_MODEL_NAME,
                    True,
                    (time.monotonic() - t0) * 1000,
                    data.get("usage") or {},
                    choice.get("finish_reason") or "",
                )
                return reply

            if final_round:
                # 确定性兜底：宁可人设化认怂，不可静默消失（@场景必须有回应）
                print("[AI工具调用] 最后轮仍无内容，使用兜底文案")
                return "（这个我查了查没理清，先不瞎说，等下再聊）"

            # 中间轮空内容：记账后丢弃（如 thinking 吃光预算的断头）
            _log_ai_call(
                "chat",
                MAIN_MODEL_NAME,
                False,
                (time.monotonic() - t0) * 1000,
                data.get("usage") or {},
                choice.get("finish_reason") or "",
                "empty_content",
            )
            print("[AI工具调用] 中间轮返回空内容，本轮回复丢弃")
            return None

    except Exception as e:
        print(f"[AI工具调用异常] {type(e).__name__}: {e}")
        return None
