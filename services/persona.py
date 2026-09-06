import os

from config import DATA_DIR

BACKGROUND_PATH = os.path.join(DATA_DIR, "persona", "background.txt")


def load_background() -> str:
    """Bot 自身事实设定（事实层）：按需注入主 prompt，永远注入日程生成"""
    if os.path.exists(BACKGROUND_PATH):
        with open(BACKGROUND_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""
