import json
import os
from datetime import datetime

SCHEDULE_PATH = "/home/minds/qqbot/data/schedule.json"


def load_schedule():
    if not os.path.exists(SCHEDULE_PATH):
        return {}
    with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_scene() -> str:
    """根据当前时间返回情境描述"""
    schedule = load_schedule()
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # 0=周一, 5=周六, 6=周日

    # 判断是 weekday 还是 weekend
    day_type = "weekend" if weekday >= 5 else "weekday"
    day_map = schedule.get(day_type, {})

    # 匹配时段
    for time_range, desc in day_map.items():
        start, end = map(int, time_range.split("-"))
        # 处理跨天时段（如 23-2）
        if start > end:
            if hour >= start or hour <= end:
                return desc
        else:
            if start <= hour < end:
                return desc

    return "闲着，不知道在干嘛"  # 兜底


def build_context(user_id: str, content: str, user_persona: str = "") -> str:
    """组装情境，但只给氛围，不强制Bot在回复里提"""
    lines = []

    scene = get_current_scene()
    # 只给一句轻量氛围，不展开
    lines.append(f"你现在{scene}。")

    if user_persona:
        lines.append(f"对方是{user_persona}。")

    lines.append(f'群友说："{content}"')
    lines.append("直接回复，不要解释你在干嘛。像刷手机时顺手回消息那样自然。")

    return "\n".join(lines)
