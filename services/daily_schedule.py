"""
每日日程生成：一天一份 data/schedule/daily/{YYYY-MM-DD}.json，轻量模型生成。
设计要点：
- 聊天链路只读不写；生成全部在后台兜底循环里（30 分钟自愈）
- seed=日期，同一天重新生成结果稳定；/reschedule 换 seed 强制重抽
- 任何环节失败 → 今日回退静态表，绝不影响主链路
- blocks 预留 micro_event 槽位（Phase 2 微事件层用，现在忽略即可）
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime

from config import DATA_DIR, LIGHT_MODEL_KEY, LIGHT_MODEL_NAME, LIGHT_MODEL_URL
from core.ai import get_ai_reply
from services.persona import load_background

DAILY_DIR = os.path.join(DATA_DIR, "schedule", "daily")

# Phase 1 静态事件：每天以此概率带 1 个事件（写入当天 JSON）
EVENT_PROBABILITY = 0.35

GENERATE_SYSTEM = """你是 YuriBot 的日程规划器。根据人设背景和今日骨架，生成她今天的日程。

硬性规则：
1. blocks 必须覆盖 0-24 全天，按 start 升序、相邻不重叠；禁止跨天时段（不要写 23-2，深夜写成 0-2）
   start/end 一律是 0-23 的整数小时，绝不能用分钟
2. 23:00 到次日 7:00 前后必须是睡觉（可微调 ±30 分钟）
3. 活动地点必须符合人设背景（城市/学校/通勤），不得发明背景中没有的新地点、新人物、新设定
4. activity ≤10字；在学校的大段时段拆成 2~3 段（如 上课/课间/午休），不要一块"上课"糊 8 小时
5. note ≤15字，是她此刻的具体状态、小动作或内心OS——像偷拍镜头，不像档案。
   ✅好note：地铁上打瞌睡 / 课间偷刷手机 / 草稿纸上画小人 / 排队等微波炉
   ❌坏note：数学语文偏强 / 回家部 / 成绩中上游（这些是档案，抄进来算错误）
   实在想不出就留空字符串
6. events：按 {event_hint} 的概率生成，恰好 1 个，20字内具体事件。
   events 必须与所在时段兼容：上课时段只能是校园里的事（小测/换座位/体育课），
   宅家时段才是补番/游戏/谷子相关。不兼容就不生成
7. mood 是全天整体心情 ≤8字，带一点具体由头更好（"周一综合征犯困"优于"平静"），不得与 events 矛盾
8. 只输出严格 JSON，不要解释、不要 markdown 代码块

输出格式：
{"mood":"...","events":[{"start":15,"end":16,"desc":"...","mood":"..."}],"blocks":[{"start":0,"end":7,"activity":"睡觉","note":""},...]}"""
# 防并发重复生成：date_str 集合
_inflight: set[str] = set()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _daily_path(date_str: str) -> str:
    return os.path.join(DAILY_DIR, f"{date_str}.json")


def _default_skeleton(weekday: int) -> str:
    if weekday >= 5:
        return "周末骨架：9-10点自然醒，上午宅家/补番/打游戏，下午出门（书店/漫展/逛街）或继续宅，晚上补番/游戏/和群友聊天，23:00-7:00睡觉"
    return "工作日骨架：7:00起，7-8点通勤上学，8-16点在学校（12-13午休），16-17点回家，17-19点自由时间（补番/游戏/写作业），19-21点写作业或宅，21-23点刷手机/聊天，23:00-7:00睡觉"


def _extract_json(raw: str) -> dict | None:
    """提取第一个可解析的 JSON 对象（容忍尾部废话/重复输出）"""
    if not raw:
        return None
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            obj, _ = decoder.raw_decode(raw[m.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _validate(data: dict) -> tuple[bool, str]:
    """
    归一化 + 清洗 + 语义校验。返回 (是否可用, 修正说明)。
    策略：清洗而非拒绝——坏块丢弃、重叠裁剪，至少剩 1 个合法时段就算成功；
    语义问题（时段词矛盾/事件不兼容）记入 notes，由 _generate 决定是否带反馈重试。
    """
    notes = []
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, list):
        return False, "blocks 不是数组"

    norm = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            notes.append("丢弃非对象块")
            continue
        try:
            s, e = int(b["start"]), int(b["end"])
        except (KeyError, TypeError, ValueError):
            notes.append(f"丢弃缺字段块:{str(b)[:40]}")
            continue
        if not (0 <= s < e <= 24):
            notes.append(f"丢弃越界块 {s}-{e}")
            continue
        act = str(b.get("activity", "")).strip()
        if not act or act.lower() == "none":
            notes.append(f"丢弃空活动块 {s}-{e}")
            continue
        raw_note = b.get("note", "")
        norm.append(
            {
                "start": s,
                "end": e,
                "activity": act[:30],
                "note": "" if raw_note in (None, "None") else str(raw_note)[:20],
                # Phase 2 微事件槽位，现在恒为空
                "micro_event": "",
            }
        )
    if not norm:
        return False, "无有效时段"

    norm.sort(key=lambda x: x["start"])
    kept = [norm[0]]
    for b in norm[1:]:
        if b["start"] < kept[-1]["end"]:
            notes.append(f"丢弃重叠块 {b['start']}-{b['end']}")
            continue
        kept.append(b)
    data["blocks"] = kept

    # 时段词一致性：晚修/晚自习 出现在白天 → 语义矛盾（触发带反馈重试）
    for b in kept:
        if any(w in b["activity"] for w in ("晚修", "晚自习")) and b["start"] < 17:
            notes.append(f"时段词矛盾:{b['start']}点安排{b['activity'][:6]}")

    events = []
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        try:
            s, e = int(ev["start"]), int(ev["end"])
        except (KeyError, TypeError, ValueError):
            continue
        desc = str(ev.get("desc", "")).strip()
        if 0 <= s < e <= 24 and desc and desc.lower() != "none":
            events.append(
                {
                    "start": s,
                    "end": e,
                    "desc": desc[:30],
                    "mood": str(ev.get("mood", "") or "")[:10],
                }
            )
    # 事件与所在时段的 activity 粗校验：上课/学校时段不允许宅家事件
    _SCHOOL_KW = ("上课", "学校", "晚自习", "自习")
    _HOME_KW = ("补番", "游戏", "谷子", "番", "宅", "漫画", "同", "打机")
    _CAMPUS_KW = ("上课", "学校", "自习", "社团", "晚修", "晚自习", "考试", "测验")
    _SPORT_KW = ("体育", "球", "跑", "运动会")
    ok_events = []
    for e in events:
        act = ""
        for b in kept:
            if b["start"] <= e["start"] < b["end"]:
                act = b["activity"]
                break
        if any(k in act for k in _SCHOOL_KW) and any(k in e["desc"] for k in _HOME_KW):
            notes.append(f"丢弃不兼容事件 {e['start']}-{e['end']}:{e['desc'][:12]}")
            continue
        # 反向兼容：体育/运动类事件不能在非校园时段（自习除外）
        if any(k in e["desc"] for k in _SPORT_KW) and not any(
            k in act for k in _CAMPUS_KW
        ):
            notes.append(f"事件不兼容 {e['start']}-{e['end']}:{e['desc'][:12]}")
            continue
        ok_events.append(e)
    data["events"] = ok_events[:1]
    mood = str(data.get("mood", "") or "").strip()
    data["mood"] = (mood[:10] if mood.lower() != "none" else "") or "还行"
    return True, "; ".join(notes) if notes else ""


async def _generate(date_str: str, weekday: int):
    if date_str in _inflight:
        return
    _inflight.add(date_str)
    try:
        if os.path.exists(_daily_path(date_str)):
            return  # 双检：循环和命令可能并发进来

        background = load_background() or "（未配置背景，按普通高中宅女人设生成）"
        skeleton = _default_skeleton(weekday)
        # seed 锚定日期：同一天重试/重生成结果一致；换日期即换一天
        rng = random.Random(f"{date_str}|yuribot-schedule")
        event_hint = "35%" if rng.random() < EVENT_PROBABILITY else "0%"
        seed = rng.randint(1, 10**9)
        weekday_cn = "一二三四五六日"[weekday]

        user_msg = (
            f"【日期】{date_str} 星期{weekday_cn}\n"
            f"【seed】{seed}\n"
            f"【人设背景】\n{background}\n\n"
            f"【今日骨架】\n{skeleton}\n\n"
            "生成今天的日程 JSON。"
        )

        data = None
        last_note = ""
        for attempt in range(2):
            hint = ""
            if attempt > 0:
                hint = (
                    "\n\n（上次输出不合规：注意 blocks 覆盖全天、时段不重叠、严格 JSON、"
                    "不要输出多余文字。"
                    + (f"校验反馈：{last_note}" if last_note else "")
                    + "）"
                )
            raw = await get_ai_reply(
                user_message=user_msg + hint,
                system_override=GENERATE_SYSTEM.replace("{event_hint}", event_hint),
                max_tokens=1500,
                temperature=0.8,
                model=LIGHT_MODEL_NAME,
                api_url=LIGHT_MODEL_URL,
                api_key=LIGHT_MODEL_KEY,
                timeout=60,
                enable_thinking=False,
                tag="schedule",
            )
            data = _extract_json(raw)
            if data is None:
                print(
                    f"[日程] {date_str} JSON解析失败(尝试{attempt + 1}): {(raw or '')[:300]!r}"
                )
                data = None
                continue
            ok, note = _validate(data)
            if not ok:
                print(
                    f"[日程] {date_str} 结构不可用(尝试{attempt + 1}): {note}; 原文: {(raw or '')[:300]!r}"
                )
                last_note = note
                data = None
                continue
            # 语义问题（时段词矛盾/事件不兼容）也算未过：带具体反馈重试一次
            if note and attempt == 0:
                print(f"[日程] {date_str} 语义校验未过，带反馈重试: {note}")
                last_note = note
                data = None
                continue
            if note:
                print(f"[日程] {date_str} 校验修正(尝试{attempt + 1}): {note}")
            break

        if not data:
            print(f"[日程] {date_str} 生成失败，今日回退静态表")
            return

        data["date"] = date_str
        data["generated_at"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(DAILY_DIR, exist_ok=True)
        tmp_path = _daily_path(date_str) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _daily_path(date_str))  # 原子写入

        ev_desc = data["events"][0]["desc"] if data["events"] else "无事件"
        print(
            f"[日程] {date_str} 生成成功: {len(data['blocks'])}个时段, "
            f"心情:{data['mood']}, 事件:{ev_desc}"
        )
    finally:
        _inflight.discard(date_str)


def get_today_schedule() -> dict | None:
    """读取今日日程（聊天链路调用，只读）。"""
    try:
        path = _daily_path(_today_str())
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[日程] 读取失败: {e}")
    return None


def get_today_mood() -> str:
    data = get_today_schedule()
    return str(data.get("mood", "")).strip() if data else ""


async def ensure_today():
    """确保今日日程已生成（后台循环调用，幂等）。"""
    date_str = _today_str()
    if os.path.exists(_daily_path(date_str)):
        return
    await _generate(date_str, datetime.now().weekday())


async def reschedule_today():
    """丢弃今天已生成的日程并重新生成（/reschedule 用）。"""
    path = _daily_path(_today_str())
    if os.path.exists(path):
        os.remove(path)
    await _generate(_today_str(), datetime.now().weekday())


async def schedule_ensure_loop():
    """后台兜底：每 30 分钟确认今日日程存在，开机后立即跑一轮。"""
    while True:
        try:
            await ensure_today()
        except Exception as e:
            print(f"[日程兜底] {type(e).__name__}: {e}")
        await asyncio.sleep(1800)
