import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.font_manager import FontProperties
import os
import uuid
from core.registry import cmd
from services.actions import send_text, send_image

OUTPUT_DIR = "/tmp/qqbot/latex"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _get_cjk_font():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return FontProperties(fname=path, size=24)
    return None


_cjk_font = _get_cjk_font()
if _cjk_font:
    rcParams["mathtext.fontset"] = "custom"


def _render(formula: str, filename: str = None) -> str:
    if not filename:
        filename = f"{uuid.uuid4()}.png"
    output_path = os.path.join(OUTPUT_DIR, filename)

    clean_formula = formula.strip()
    if clean_formula.startswith("$") and clean_formula.endswith("$"):
        clean_formula = clean_formula[1:-1]

    try:
        fig = plt.figure(figsize=(max(4, len(clean_formula) * 0.25), 1.2))
        text_args = {
            "x": 0.5,
            "y": 0.5,
            "s": f"${clean_formula}$",
            "fontsize": 24,
            "ha": "center",
            "va": "center",
            "color": "black",
        }
        if _cjk_font:
            text_args["fontproperties"] = _cjk_font
        fig.text(**text_args)
        fig.patch.set_facecolor("white")
        fig.savefig(
            output_path,
            dpi=200,
            bbox_inches="tight",
            pad_inches=0.15,
            facecolor="white",
        )
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"[LaTeX渲染失败] {e}")
        return None


@cmd("latex", desc="渲染LaTeX公式为图片，用法: /latex \\int_0^1 x^2 dx")
async def latex_cmd(ctx):
    formula = ctx.raw.strip()
    if not formula:
        return "用法：/latex \\int_0^1 x^2 dx"

    img_path = _render(formula)
    if not img_path:
        return "公式渲染失败了，检查一下语法？"

    success = await send_image(
        ctx.group_id,
        ctx.user_id,
        img_path,
        description=f"LaTeX公式：{formula[:50]}",
        msg_id=ctx.msg_id,
        is_group=ctx.is_group,
    )
    if not success:
        return "图片上传失败了..."
    return None  # 返回 None 表示已经自己处理过发送，框架不再发文字
