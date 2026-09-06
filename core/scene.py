import json
import os
from datetime import datetime

from config import DATA_DIR

SCHEDULE_PATH = os.path.join(DATA_DIR, "schedule.json")


def load_schedule():
    if not os.path.exists(SCHEDULE_PATH):
        return {}
    with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_scene() -> str:
    """当前时段在干嘛（供主 prompt 的【现在】块引用，文案由 schedule.json 决定）"""
    schedule = load_schedule()
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()

    day_type = "weekend" if weekday >= 5 else "weekday"
    day_map = schedule.get(day_type, {})

    for time_range, desc in day_map.items():
        start, end = map(int, time_range.split("-"))
        if start > end:
            if hour >= start or hour <= end:
                return desc
        else:
            if start <= hour < end:
                return desc

    return "闲着，不知道在干嘛"
