import aiosqlite
import json

from services.db import DB_PATH

GENERAL_QUERIES = [
    "本命",
    "喜欢什么",
    "推荐",
    "推什么",
    "最爱",
    "喜欢的番",
    "喜欢的游戏",
]


def is_general_query(topic: str) -> bool:
    return any(q in topic for q in GENERAL_QUERIES)


async def get_relevant_preferences(topic: str, limit: int = 3) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        if is_general_query(topic):
            async with db.execute(
                """
                SELECT name, feeling, detail FROM bot_preferences
                WHERE feeling IN ('本命', '推', '在玩')
                ORDER BY CASE feeling
                    WHEN '本命' THEN 1
                    WHEN '推' THEN 2
                    ELSE 3
                END
                LIMIT ?
            """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
            result = [
                f"{name}（{feeling}）：{detail}" for name, feeling, detail in rows
            ]
            print(f"[偏好检索] 泛化查询，返回{len(result)}条")
            return result

        topic_lower = topic.lower()
        async with db.execute(
            "SELECT name, feeling, detail, keywords FROM bot_preferences"
        ) as cursor:
            rows = await cursor.fetchall()

        matched = []
        for name, feeling, detail, keywords_json in rows:
            try:
                keywords = json.loads(keywords_json)
            except:
                keywords = []
            if any(k.lower() in topic_lower for k in keywords):
                matched.append(f"{name}（{feeling}）：{detail}")

        print(f"[偏好检索] 关键词匹配，返回{len(matched)}条")
        return matched[:limit]
