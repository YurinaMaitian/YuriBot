import json
import base64
import aiohttp

# import 换成
from config import (
    VISION_MODEL_URL,
    VISION_MODEL_KEY,
    VISION_MODEL_NAME,
    VISION_MODEL_MAX_TOKENS,
    VISION_MODEL_TEMP,
)
from services.image_cache import get_cached_desc, set_cached_desc


async def describe_image(
    image_url: str, filename: str, content_type: str = "image/jpeg", user_text: str = ""
) -> str:
    """
    下载 QQ 图片并调用多模态模型描述/识别。
    user_text: 用户 accompanying 文字，用于判断是 OCR 还是描述。
    """

    cached = await get_cached_desc(filename)
    if cached:
        print(f"[Vision缓存命中] {filename[:20]}... -> {cached[:30]}")
        return cached

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=10) as r:
                if r.status != 200:
                    return "一张图片"
                img_bytes = await r.read()

        img_base64 = base64.b64encode(img_bytes).decode()
        mime = content_type if content_type else "image/jpeg"

        # ========== 智能判断：OCR 还是描述 ==========
        is_ocr = any(
            k in user_text
            for k in [
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
        )

        if is_ocr:
            prompt_text = "提取图片中的所有文字内容，如实转录，不要描述图片外观"
        else:
            prompt_text = "一句话描述这张图片的内容"

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
            "enable_thinking": False,  # 关键：关掉推理，content 直接可用
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                VISION_MODEL_URL, headers=headers, json=payload, timeout=20
            ) as r:
                raw = await r.text()
                print(f"[Vision API原始返回] {raw[:800]}")

                if r.status != 200:
                    return "一张图片"

                data = json.loads(raw)
                msg = data["choices"][0].get("message", {})

                desc = (msg.get("content") or "").strip()
                if not desc:
                    desc = "一张图片"
                # 截断
                if len(desc) > 100:
                    desc = desc[:100]

                await set_cached_desc(filename, desc)
                print(f"[Vision识别成功] {filename[:20]}... -> {desc[:40]}")
                return desc

    except Exception as e:
        print(f"[Vision异常] {e}")
        return "一张图片"
