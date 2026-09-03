import aiohttp
from config import BOT_OPENID
from core.ai import get_token
from core.registry import get_panel_commands

PANEL_API = "https://api.sgroup.qq.com/v2/panels"


async def _api_call(method: str, url: str, payload: dict = None):
    token = await get_token()
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(url, headers=headers) as r:
                return r.status, await r.json()
        elif method == "POST":
            async with session.post(url, headers=headers, json=payload) as r:
                return r.status, await r.json()
        elif method == "PUT":
            async with session.put(url, headers=headers, json=payload) as r:
                return r.status, await r.json()
        elif method == "DELETE":
            async with session.delete(url, headers=headers) as r:
                return r.status, await r.json()


async def sync_panel(scope: str = "group"):
    """
    同步指令面板到QQ。
    先查询该scope下是否已有面板，有则更新，无则创建。
    """
    commands = get_panel_commands()
    if not commands:
        print(f"[面板-{scope}] 注册表为空，跳过")
        return

    # 1. 查询现有面板
    status, data = await _api_call("GET", f"{PANEL_API}?scope={scope}&limit=20")
    if status != 200:
        print(f"[面板-{scope}] 查询失败: {status}, {data}")
        return

    records = data.get("records", [])
    existing = records[0] if records else None

    # 构建面板内容
    items = []
    for cmd in commands:
        name = cmd["name"]
        desc = cmd["description"]
        # 截断到QQ限制
        if len(name) > 14:
            name = name[:14]
        if len(desc) > 30:
            desc = desc[:30]
        items.append({"type": "command", "name": name, "desc": desc})

    panel_payload = {
        "scope": scope,
        "target_type": "all",
        "panel": {"items": items, "remark": f"YuriBot {scope} 指令面板"},
    }

    if existing:
        # 2. 更新现有面板
        panel_id = existing["panel_id"]
        status, result = await _api_call(
            "PUT", f"{PANEL_API}/{panel_id}", {"panel": panel_payload["panel"]}
        )
        if status in (200, 201):
            print(f"[面板-{scope}] 更新成功: {panel_id}, {len(items)} 条指令")
        else:
            print(f"[面板-{scope}] 更新失败: {status}, {result}")
    else:
        # 3. 创建新面板
        status, result = await _api_call("POST", PANEL_API, panel_payload)
        if status in (200, 201):
            panel_id = result.get("panel_id", "unknown")
            print(f"[面板-{scope}] 创建成功: {panel_id}, {len(items)} 条指令")
        else:
            print(f"[面板-{scope}] 创建失败: {status}, {result}")


async def sync_all_panels():
    """同步 group 和 c2c 两个场景的面板"""
    for scope in ["group", "c2c"]:
        try:
            await sync_panel(scope)
        except Exception as e:
            print(f"[面板-{scope}] 异常: {e}")
