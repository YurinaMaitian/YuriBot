import importlib
import pkgutil
from typing import Dict, Callable, Optional
from core.context import CmdContext

_registry: Dict[str, Callable] = {}
_help: Dict[str, str] = {}


def cmd(name: str, desc: str = ""):
    """装饰器：注册一个命令"""

    def decorator(func: Callable) -> Callable:
        _registry[name] = func
        _help[name] = desc
        return func

    return decorator


def get_handler(name: str) -> Optional[Callable]:
    return _registry.get(name)


def get_help() -> str:
    lines = ["📋 可用指令："]
    for name, desc in _help.items():
        lines.append(f"  /{name} - {desc}")
    return "\n".join(lines)


def get_cmd_list() -> str:
    """返回紧凑指令列表，如：/on /off /status /latex"""
    return " ".join([f"/{k}" for k in _registry.keys()])


def auto_discover(package: str = "tools"):
    """自动扫描包内所有模块，触发装饰器注册"""
    try:
        parent = importlib.import_module(package)
        path = getattr(parent, "__path__", [])
        for _, name, _ in pkgutil.iter_modules(path):
            full = f"{package}.{name}"
            try:
                importlib.import_module(full)
                print(f"[注册] 加载模块: {full}")
            except Exception as e:
                print(f"[注册失败] {full}: {e}")
    except ImportError as e:
        print(f"[注册] 包 {package} 不存在: {e}")


def get_panel_commands() -> list[dict]:
    """生成QQ指令面板需要的格式"""
    result = []
    for name, desc in _help.items():
        result.append({"name": name, "description": desc or f"使用 /{name}"})
    return result
