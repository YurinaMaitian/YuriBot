import os
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("APP_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
WS_URL = "wss://api.sgroup.qq.com/websocket"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
BOT_OPENID = "9175508C1645C4F31E68CD266A985225"  # 从日志提取的 Bot openid
