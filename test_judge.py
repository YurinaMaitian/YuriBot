import asyncio
import pytest
from services.interject import (
    _compose_judge_input,
    _call_judge,
    _parse_judge,
    JUDGE_SYSTEM,
)


def msg(identity: str, content: str, hm: str = "12:00") -> str:
    return f"{identity}：[{hm}]{content}"


def bot(content: str, hm: str = "12:00") -> str:
    return msg("YuriBot", content, hm)


CASES = [
    (
        "点名别人-不抢话",
        [msg("麦田", "mjl，你在吗")],
        False,
        "闲着",
        False,
        "someone:mjl",
    ),
    ("全群喊话-举手", [msg("麦田", "群里没人吗")], False, "闲着", True, "everyone"),
    ("求做事-该回", [msg("军师", "yuri 你帮我@一下mjl")], False, "闲着", True, "yuri"),
    (
        "评价她言行-必须接",
        [
            bot("这是bfnz，就是逼飞奶炸", "12:00"),
            msg("麦田", "你怎么能这么粗俗", "12:01"),
        ],
        True,
        "闲着",
        True,
        "yuri",
    ),
    (
        "喊oi-在叫她",
        [bot("今天这集还行", "12:00"), msg("麦田", "oi", "12:01")],
        True,
        "闲着",
        True,
        "yuri",
    ),
    (
        "摩托车纠正-在话题中必接",
        [
            bot("这摩托太子款？排量多大的", "12:00"),
            msg("军师", "太子个锤子，这是张雪机车", "12:01"),
        ],
        True,
        "上课",
        True,
        "yuri",
    ),
    (
        "上课没在话题-装睡",
        [
            bot("（一小时前的无关话题）", "11:00"),
            msg("麦田", "bot 你觉得原神新角色怎么样", "12:00"),
        ],
        False,
        "上课",
        False,
        None,
    ),
    ("我哭死-玩梗灰区", [msg("麦田", "这图笑死我哭死")], False, "闲着", None, None),
    (
        "表情包-灰区",
        [msg("麦田", "[图片]a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5.png")],
        False,
        "闲着",
        None,
        None,
    ),
]


from services.interject import _note_activity, _activity_block


@pytest.mark.asyncio
async def test_judge_sees_in_flight():
    """在途可见性：她正在回A时，同话题的B倾向不接"""
    _note_activity("testgrp", "pending", "今晚开黑吗")
    judge_input = _compose_judge_input(
        [msg("麦田", "对啊几点")], False, scene_text="闲着", group_id="testgrp"
    )
    assert "她的动向" in judge_input and "正在回复" in judge_input

    raw = await _call_judge(JUDGE_SYSTEM, judge_input)
    reply, reason, addressee = _parse_judge(raw)
    print(f"[在途case] reply={reply} reason={reason}")
    # 软断言：在途提示下 4B 应该倾向 false；失败只打印不报错，攒数据定阈值


@pytest.mark.asyncio
async def test_judge_golden_set():
    results = []
    for name, lines, continuation, scene, expect_reply, expect_addressee in CASES:
        judge_input = _compose_judge_input(lines, continuation, scene_text=scene)
        raw = await _call_judge(JUDGE_SYSTEM, judge_input)
        reply, reason, addressee = _parse_judge(raw)
        results.append((name, reply, reason, addressee))

        fail_msgs = []
        if expect_reply is not None and reply != expect_reply:
            fail_msgs.append(f"reply 期望{expect_reply}")
        if expect_addressee is not None and addressee != expect_addressee:
            fail_msgs.append(f"addressee 期望{expect_addressee!r}")
        status = "✗ " + " ".join(fail_msgs) if fail_msgs else "✓"
        print(f"{status} [{name}] reply={reply} addressee={addressee} reason={reason}")

        await asyncio.sleep(1)  # 免费API防限流

    hard_fails = []
    for (n, r, reason_x, ad), (_, _, _, _, er, ea) in zip(results, CASES):
        if (er is not None and r != er) or (ea is not None and ad != ea):
            hard_fails.append((n, f"reply={r}", f"addressee={ad!r}"))
    assert not hard_fails, f"失败case: {hard_fails}"
