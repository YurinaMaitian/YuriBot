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
LIGHT_MODEL_MAX_TOKENS = int(os.getenv("LIGHT_MODEL_MAX_TOKENS", "1000"))
LIGHT_MODEL_TEMP = float(os.getenv("LIGHT_MODEL_TEMP", "0.3"))

# ========== 嵌入模型 ==========
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "")
EMBEDDING_KEY = os.getenv("EMBEDDING_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# ========== Qdrant ==========
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "scenes"

# ========== Vision 模型（多模态，关思考） ==========
VISION_MODEL_URL = os.getenv("VISION_MODEL_URL", LIGHT_MODEL_URL)
VISION_MODEL_KEY = os.getenv("VISION_MODEL_KEY", LIGHT_MODEL_KEY)
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "Qwen/Qwen3.5-4B")
VISION_MODEL_MAX_TOKENS = int(os.getenv("VISION_MODEL_MAX_TOKENS", "300"))
VISION_MODEL_TEMP = float(os.getenv("VISION_MODEL_TEMP", "0.3"))


# 图片年龄超过此值，追问时静默丢弃
# ========== 图片等待队列 ==========
IMAGE_WAIT_TIMEOUT = float(
    os.getenv("IMAGE_WAIT_TIMEOUT", "10")
)  # 占位后等 Event 的秒数
IMAGE_WAIT_MAX = float(
    os.getenv("IMAGE_WAIT_MAX", "15")
)  # 图片年龄超过此值，追问时静默丢弃
IMAGE_FAIL_BLOCK = int(os.getenv("IMAGE_FAIL_BLOCK", "3"))  # 失败几次进 blocked
ENABLE_IMAGE_PLACEHOLDER = (
    os.getenv("ENABLE_IMAGE_PLACEHOLDER", "true").lower() == "true"
)
IMAGE_ACTION_COOLDOWN = float(
    os.getenv("IMAGE_ACTION_COOLDOWN", "5")
)  # 占位动作冷却（秒/群）

# ========== 短期记忆 ==========
HISTORY_LOAD_COUNT = int(
    os.getenv("HISTORY_LOAD_COUNT", "20")
)  # 重启后从 DB 懒加载条数
HISTORY_HOT_MAX = int(os.getenv("HISTORY_HOT_MAX", "50"))  # 热缓存环形缓冲上限
HISTORY_CHAR_BUDGET = int(
    os.getenv("HISTORY_CHAR_BUDGET", "600")
)  # 历史文本字符预算，超了截断
HISTORY_RECENT_LINES = int(
    os.getenv("HISTORY_RECENT_LINES", "15")
)  # 超预算后保留最近 N 行


# ========== 情景记忆（LLM 分割） ==========
SCENE_MIN_JUDGE = int(os.getenv("SCENE_MIN_JUDGE", "16"))  # 队列多长到值得首次判断
SCENE_JUDGE_INTERVAL = int(
    os.getenv("SCENE_JUDGE_INTERVAL", "12")
)  # 之后每再攒多少条判一次
SCENE_IDLE_FORCE = int(os.getenv("SCENE_IDLE_FORCE", "600"))  # 静默秒数，强制整队结算
SCENE_MAX_QUEUE = int(os.getenv("SCENE_MAX_QUEUE", "120"))  # 队列硬顶，超过必须切
SCENE_MIN_CUT = int(os.getenv("SCENE_MIN_CUT", "4"))  # 最小成段长度
SCENE_SCAN_INTERVAL = int(os.getenv("SCENE_SCAN_INTERVAL", "30"))  # 后台扫描间隔（秒）

# ========== B站解析 ==========
BILI_COOKIE = os.getenv("BILI_COOKIE", "")  # SESSDATA=...; bili_jct=...; buvid3=...
