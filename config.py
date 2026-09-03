import os
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("APP_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
WS_URL = "wss://api.sgroup.qq.com/websocket"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
BOT_OPENID = "9175508C1645C4F31E68CD266A985225"

# ========== 主人配置 ==========
# 先启动一次 Bot，私聊发 /myid 拿到自己的 openid，填到这里，再重启
BOT_OWNER = "E98EFE5B1DE766EBC8307244C2332E9F"
