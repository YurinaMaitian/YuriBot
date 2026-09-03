import sqlite3
import json

DB_PATH = "/home/minds/qqbot/data/bot.db"

GENERAL_QUERIES = ["本命", "喜欢什么", "推荐", "推什么", "最爱", "喜欢的番", "喜欢的游戏"]

def is_general_query(topic: str) -> bool:
    return any(q in topic for q in GENERAL_QUERIES)

def get_relevant_preferences(topic: str, limit: int = 3) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if is_general_query(topic):
        # 只取本命 + 推，最多3条，本命优先
        cursor.execute('''
            SELECT name, feeling, detail FROM bot_preferences
            WHERE feeling IN ('本命', '推', '在玩')
            ORDER BY CASE feeling 
                WHEN '本命' THEN 1 
                WHEN '推' THEN 2 
                ELSE 3 
            END
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        result = [f"{name}（{feeling}）：{detail}" for name, feeling, detail in rows]
        print(f"[偏好检索] 泛化查询，返回{len(result)}条")
        return result
    
    # 关键词匹配（具体问题）
    topic_lower = topic.lower()
    cursor.execute('SELECT name, feeling, detail, keywords FROM bot_preferences')
    rows = cursor.fetchall()
    conn.close()
    
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
