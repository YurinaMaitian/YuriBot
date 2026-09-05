import json
import base64
import aiohttp
import re
from config import LIGHT_MODEL_URL, LIGHT_MODEL_KEY, LIGHT_MODEL_NAME
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
            "Authorization": f"Bearer {LIGHT_MODEL_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": LIGHT_MODEL_NAME,
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
            "max_tokens": 10000,  # 推理模型需要大量空间
            "temperature": 0.3,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LIGHT_MODEL_URL, headers=headers, json=payload, timeout=20
            ) as r:
                raw = await r.text()
                print(f"[Vision API原始返回] {raw[:800]}")

                if r.status != 200:
                    return "一张图片"

                data = json.loads(raw)
                choice = data["choices"][0]
                msg = choice.get("message", {})

                # 优先取 content
                desc = msg.get("content", "").strip()

                # 如果 content 为空，从 reasoning 提取
                if not desc:
                    reasoning = msg.get("reasoning_content", "").strip()
                    if reasoning:
                        desc = _extract_from_reasoning(reasoning)
                        print(f"[Vision] 从 reasoning 提取: {desc[:50]}")
                    else:
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


def _extract_from_reasoning(reasoning: str) -> str:
    """
    从思维链末尾提取最终结论。
    推理模型通常把结论写在最后，前面都是分析过程。
    """
    lines = [l.strip() for l in reasoning.split("\n") if l.strip()]

    # 跳过词：分析过程中的元信息
    skip = [
        "方案",
        "草拟",
        "尝试",
        "分析",
        "Process",
        "思考",
        "步骤",
        "提炼",
        "观察",
        "总结",
        "评估",
        "计划",
        "策略",
        "优化",
        "精简",
        "确认",
    ]

    # 从后往前找，找最长的一句实质性内容
    candidates = []
    for line in reversed(lines):
        clean = re.sub(r"\*\*|\*|#|`", "", line).strip()

        # 跳过纯编号
        if re.match(r"^\d+\.\s*$", clean) or re.match(r"^\d+\.\s*\*\*", clean):
            continue
        # 跳过含元信息关键词的
        if any(k in clean for k in skip):
            continue
        # 跳过过短（可能是半截）或过长
        if len(clean) < 8:
            continue

        candidates.append(clean)
        if len(candidates) >= 3:  # 收集最后 3 句候选
            break

    if not candidates:
        return "一张图片"

    # 选最长的一句（通常是最完整的结论）
    best = max(candidates, key=len)
    return best
