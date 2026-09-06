import asyncio
import aiohttp
from core.ai import get_token
from core.registry import get_panel_commands

PANEL_API = "https://api.sgroup.qq.com/v2/panels"


async def _api_call(method: str, url: str, payload: dict = None):
    token = await get_token()
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    session = get_session()
    if method == "GET":
        async with session.get(url, headers=headers, timeout=15) as r:
            return r.status, await r.json()
    elif method == "POST":
        async with session.post(url, headers=headers, json=payload, timeout=15) as r:
            return r.status, await r.json()
    elif method == "PUT":
        async with session.put(url, headers=headers, json=payload, timeout=15) as r:
            return r.status, await r.json()
    elif method == "DELETE":
        async with session.delete(url, headers=headers, timeout=15) as r:
            return r.status, await r.json()


async def _api_call_with_retry(method: str, url: str, payload: dict = None):
    """
    带限流容错：撞 30013（窗口抖动，实测窗口比文档严格）时等 65s 重试一次。
    灰度期面板 API 的 QPM 窗口不稳定，重启撞上是常态，重试即可自愈。
    """
    status, data = -1, {}
    for attempt in range(2):
        status, data = await _api_call(method, url, payload)
        if (
            status == 400
            and isinstance(data, dict)
            and data.get("err_code") == 40030013
            and attempt == 0
        ):
            print("[面板] 命中限流窗口(30013)，65s 后重试...")
            await asyncio.sleep(65)
            continue
        break
    return status, data


async def sync_panel(scope: str = "group"):
    """
    同步指令面板到QQ（幂等：有则更新，无则创建）。
    名字不带斜杠：用户先打"/"唤起面板，点击后条目名插入输入框拼成完整指令。
    """
    commands = get_panel_commands()
    if not commands:
        print(f"[面板-{scope}] 注册表为空，跳过")
        return

    # 1. 查询现有面板（空结果时返回 {"is_end": true}，无 records 字段）
    status, data = await _api_call_with_retry(
        "GET", f"{PANEL_API}?scope={scope}&limit=20"
    )
    if status != 200:
        print(f"[面板-{scope}] 查询失败: {status}, {data}")
        return

    records = data.get("records", []) if isinstance(data, dict) else []
    existing = records[0] if records else None

    # 2. 构建面板内容
    items = []
    for cmd in commands:
        name = cmd["name"][:14]
        desc = cmd["description"][:30]
        items.append({"type": "command", "name": name, "desc": desc})

    panel_body = {
        "items": items,
        "remark": f"YuriBot {scope} 指令面板",
    }

    if existing:
        # 3a. 更新现有面板
        panel_id = existing["panel_id"]
        status, result = await _api_call_with_retry(
            "PUT", f"{PANEL_API}/{panel_id}", {"panel": panel_body}
        )
        if status == 200:
            print(f"[面板-{scope}] 更新成功: {panel_id}, {len(items)} 条指令")
        else:
            print(f"[面板-{scope}] 更新失败: {status}, {result}")
    else:
        # 3b. 创建新面板
        payload = {
            "scope": scope,
            "target_type": "all",
            "panel": panel_body,
        }
        status, result = await _api_call_with_retry("POST", PANEL_API, payload)
        if status == 200:
            print(
                f"[面板-{scope}] 创建成功: {result.get('panel_id')}, {len(items)} 条指令"
            )
        else:
            print(f"[面板-{scope}] 创建失败: {status}, {result}")


async def sync_all_panels(scopes: tuple = ("c2c",)):
    """
    同步指令面板到QQ。
    默认只同步私聊（c2c）：私聊面板是 owner's 速查表，无@唤起问题；
    群面板因 @Bot 自动唤起会打断群聊，已撤回（见设计文档 §4.3）。
    如需恢复群面板：await sync_all_panels(scopes=("group", "c2c"))
    """
    for scope in scopes:
        try:
            await sync_panel(scope)
        except Exception as e:
            print(f"[面板-{scope}] 异常: {type(e).__name__}: {e}")
