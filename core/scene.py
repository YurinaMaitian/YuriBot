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
    """
    当前时段她在干嘛。
    优先级：日程事件 > 日程时段 > 深夜兜底(睡觉) > 静态表 > 闲着。
    心情不在这里拼，由 build_prompt 读当天 mood 追加。
    """
    now = datetime.now()
    hour = now.hour

    from services.daily_schedule import get_today_schedule

    data = get_today_schedule()
    if data:
        # 1. 事件优先（覆盖普通时段）
        for ev in data.get("events") or []:
            try:
                if int(ev.get("start", -1)) <= hour < int(ev.get("end", -1)):
                    desc = str(ev.get("desc", "")).strip()
                    mood = str(ev.get("mood", "")).strip()
                    return f"{desc}，{mood}" if mood else desc
            except (TypeError, ValueError):
                continue
        # 2. 普通时段
        blocks = sorted(
            data.get("blocks") or [],
            key=lambda b: (
                int(b.get("start", 0))
                if str(b.get("start", "")).lstrip("-").isdigit()
                else 0
            ),
        )
        for b in blocks:
            try:
                s, e = int(b["start"]), int(b["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if s <= hour < e:
                act = str(b.get("activity", "")).strip()
                note = str(b.get("note", "")).strip()
                if act:
                    return f"{act}，{note}" if note else act

    # 3. 深夜兜底
    if hour >= 23 or hour < 6:
        return "睡觉"

    # 4. 静态表回退（旧逻辑，schedule.json 仍在就生效）
    schedule = load_schedule()
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
