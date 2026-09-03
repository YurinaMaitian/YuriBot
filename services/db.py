import os
import aiosqlite

DB_DIR = "/home/minds/qqbot/data"
DB_PATH = os.path.join(DB_DIR, "bot.db")


async def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                speaker TEXT NOT NULL,
                speaker_id TEXT NOT NULL,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def save_message(group_id: str, speaker: str, speaker_id: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO messages (group_id, speaker, speaker_id, content)
            VALUES (?, ?, ?, ?)
        """,
            (group_id or "", speaker, speaker_id, content),
        )
        await db.commit()
