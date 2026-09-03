import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.font_manager import FontProperties
import os

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


def render_latex(formula: str, filename: str = None) -> str:
    if not filename:
        import uuid

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
