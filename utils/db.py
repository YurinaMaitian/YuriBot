import os
import sqlite3

DB_DIR = "/home/minds/qqbot/data"
DB_PATH = os.path.join(DB_DIR, "bot.db")

def init_db():
    """初始化数据库，建表"""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            speaker TEXT NOT NULL,
            speaker_id TEXT NOT NULL,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_message(group_id: str, speaker: str, speaker_id: str, content: str):
    """写入单条消息"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (group_id, speaker, speaker_id, content)
        VALUES (?, ?, ?, ?)
    ''', (group_id or "", speaker, speaker_id, content))
    conn.commit()
    conn.close()
