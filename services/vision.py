import json
import base64
import aiohttp
from config import (
    VISION_MODEL_URL,
    VISION_MODEL_KEY,
    VISION_MODEL_NAME,
    VISION_MODEL_MAX_TOKENS,
    VISION_MODEL_TEMP,
)
from services import image_cache
from services.http import get_session

# 同图并发解析防护（消息去重漏网时的兜底）
_inflight: set[str] = set()

_OCR_KEYWORDS = [
    "写了什么",
    "什么字",
    "文字",
    "内容",
    "上面写",
    "写了",
    "什么意思",
    "翻译",
    "提取",
]


def _is_ocr_request(user_text: str) -> bool:
    return any(k in user_text for k in _OCR_KEYWORDS)


async def describe_image(
    image_url: str,
    filename: str,
    content_type: str = "image/jpeg",
    user_text: str = "",
    force: bool = False,
) -> str:
    """
    后台解析图片。状态机流转在内部完成，永不抛异常。
    force=True 忽略缓存重解析（OCR 重解析预留口子）。
    """
    if not force:
        if filename in _inflight:
            return "一张图片"  # 同图任务在跑，等它通知
        info = await image_cache.get_image(filename)
        if info and info["status"] == "success" and info["description"]:
            print(f"[Vision缓存命中] {filename[:20]} -> {info['description'][:30]}")
            return info["description"]

    await image_cache.mark_pending(filename)
    _inflight.add(filename)
    session = get_session()
    try:
        async with session.get(image_url, timeout=10) as r:
            if r.status != 200:
                print(f"[Vision下载失败] {r.status} {filename[:20]}")
                await image_cache.mark_failed(filename)
                return "一张图片"
            img_bytes = await r.read()

        img_base64 = base64.b64encode(img_bytes).decode()
        mime = content_type or "image/jpeg"

        prompt_text = (
            "提取图片中的所有文字内容，如实转录，不要描述图片外观"
            if _is_ocr_request(user_text)
            else "一句话描述这张图片的内容"
        )

        headers = {
            "Authorization": f"Bearer {VISION_MODEL_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": VISION_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{img_base64}"},
                        },
                    ],
                }
            ],
            "max_tokens": VISION_MODEL_MAX_TOKENS,
            "temperature": VISION_MODEL_TEMP,
            "enable_thinking": False,
        }

        async with session.post(
            VISION_MODEL_URL, headers=headers, json=payload, timeout=30
        ) as r:
            if r.status != 200:
                print(f"[Vision API错误] {r.status} {(await r.text())[:200]}")
                await image_cache.mark_failed(filename)
                return "一张图片"
            data = await r.json()

        desc = (data["choices"][0].get("message", {}).get("content") or "").strip()
        if not desc:
            await image_cache.mark_failed(filename)
            return "一张图片"

        if len(desc) > 100:
            desc = desc[:100]

        await image_cache.mark_success(filename, desc)
        print(f"[Vision识别成功] {filename[:20]} -> {desc[:40]}")
        return desc

    except Exception as e:
        print(f"[Vision异常] {type(e).__name__}: {e}")
        await image_cache.mark_failed(filename)
        return "一张图片"

    finally:
        _inflight.discard(filename)
