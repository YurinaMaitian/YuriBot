"""
启动自检：把"改丢 import / 配置缺失"从运行时报错提前到启动日志。

设计原则：只报告、不拦截。
- 配置缺失不阻止启动（模块自己有兜底，比如 Router 有规则 fallback），
  但必须在日志里显眼地喊出来；
- 真正的致命错误（import 失败等）会在 auto_discover 阶段就抛异常，
  这里管的是"能跑但带病"的状态。
"""

import os

from core import registry
from core.ai import SYSTEM_PROMPT
from config import (
    APP_ID,
    APP_SECRET,
    DATA_DIR,
    EMBEDDING_URL,
    LIGHT_MODEL_KEY,
    LIGHT_MODEL_NAME,
    LIGHT_MODEL_URL,
    MAIN_MODEL_KEY,
)


def run_self_check() -> list[str]:
    """返回问题列表（空列表 = 全绿）。在 auto_discover 之后调用。"""
    problems = []

    # 1. 关键配置非空
    if not APP_ID or not APP_SECRET:
        problems.append(
            "APP_ID/APP_SECRET 未配置 → Token 获取与 webhook 签名校验将失败"
        )
    if not MAIN_MODEL_KEY:
        problems.append("MAIN_MODEL_KEY 未配置 → 主模型聊天不可用")
    if not (LIGHT_MODEL_URL and LIGHT_MODEL_KEY and LIGHT_MODEL_NAME):
        problems.append(
            "轻量模型三件套未配齐 → Router/情景判断将全程走规则兜底（可用但降智）"
        )
    if not EMBEDDING_URL:
        problems.append(
            "EMBEDDING_URL 未配置 → 情景语义检索不可用（已回退时间倒序兜底）"
        )

    # 2. 指令注册表非空（依赖 auto_discover 已执行）
    if not registry._registry:
        problems.append(
            "指令注册表为空 → auto_discover 失效？检查 handlers/ 与 tools/ 目录"
        )

    # 3. persona 已加载
    if not SYSTEM_PROMPT:
        problems.append(
            "persona 未加载 → 检查 data/persona/core.txt 是否存在（Bot 将无人设裸奔）"
        )

    # 4. 数据目录可写
    if not os.access(DATA_DIR, os.W_OK):
        problems.append(f"数据目录不可写：{DATA_DIR}")

    from services.persona import load_background

    if not load_background():
        problems.append(
            "background.txt 未配置 → 日程生成将按通用人设，persona_bg 注入为空（建议补全）"
        )

    # 汇总输出
    if problems:
        print("=" * 52)
        print("[启动自检] 发现以下问题：")
        for p in problems:
            print(f"  ⚠️  {p}")
        print("=" * 52)
    else:
        print("[启动自检] 通过 ✓")

    return problems
