from dataclasses import dataclass
from typing import List, Any


@dataclass
class CmdContext:
    group_id: str  # 群 openid（私聊为空字符串）
    user_id: str  # 用户 openid
    msg_id: str  # 消息 id（用于回复）
    is_group: bool  # 是否是群聊
    cmd: str  # 命令名，如 "latex"
    args: List[str]  # 按空格分割的参数列表
    raw: str  # 去掉 "/cmd " 后的原始内容
    state: Any  # 全局状态对象
