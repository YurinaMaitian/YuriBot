import os
from dotenv import load_dotenv

load_dotenv()

# ========== QQ Bot ==========
APP_ID = os.getenv("APP_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
WS_URL = "wss://api.sgroup.qq.com/websocket"
BOT_OPENID = "9175508C1645C4F31E68CD266A985225"
BOT_OWNER = os.getenv("MASTER_ID", "")

# ========== 主模型（聊天回复） ==========
MAIN_MODEL_URL = os.getenv(
    "MAIN_MODEL_URL", "https://api.deepseek.com/chat/completions"
)
MAIN_MODEL_KEY = os.getenv("MAIN_MODEL_KEY", "")
MAIN_MODEL_NAME = os.getenv("MAIN_MODEL_NAME", "deepseek-chat")
MAIN_MODEL_MAX_TOKENS = int(os.getenv("MAIN_MODEL_MAX_TOKENS", "120"))
MAIN_MODEL_TEMP = float(os.getenv("MAIN_MODEL_TEMP", "0.8"))

# ========== 轻量模型（Router / 摘要 / 日记） ==========
LIGHT_MODEL_URL = os.getenv("LIGHT_MODEL_URL", "")
LIGHT_MODEL_KEY = os.getenv("LIGHT_MODEL_KEY", "")
LIGHT_MODEL_NAME = os.getenv("LIGHT_MODEL_NAME", "")
LIGHT_MODEL_MAX_TOKENS = int(os.getenv("LIGHT_MODEL_MAX_TOKENS", "100"))
LIGHT_MODEL_TEMP = float(os.getenv("LIGHT_MODEL_TEMP", "0.1"))

# ========== 嵌入模型 ==========
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "")
EMBEDDING_KEY = os.getenv("EMBEDDING_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# ========== Qdrant ==========
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "scenes"
