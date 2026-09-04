import aiohttp
from config import EMBEDDING_KEY, EMBEDDING_URL, EMBEDDING_MODEL


async def embed_text(text: str) -> list[float]:
    if not EMBEDDING_URL:
        raise RuntimeError("EMBEDDING_URL 未配置")

    headers = {
        "Authorization": f"Bearer {EMBEDDING_KEY}",
        "Content-Type": "application/json",
    }

    payload = {"model": EMBEDDING_MODEL, "input": text[:2000]}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            EMBEDDING_URL, headers=headers, json=payload, timeout=10
        ) as r:
            if r.status != 200:
                raw = await r.text()
                raise RuntimeError(f"嵌入 API 错误: {r.status}, {raw[:200]}")

            data = await r.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["embedding"]

            if "embedding" in data:
                return data["embedding"]

            raise RuntimeError(f"嵌入 API 返回格式异常: {data.keys()}")
