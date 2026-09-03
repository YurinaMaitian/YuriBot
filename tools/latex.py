import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
import os
import uuid

OUTPUT_DIR = "/tmp/qqbot/latex"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ========== 自动扫描并配置中文字体 ==========
def _setup_fonts():
    cjk_candidates = []
    for font in font_manager.fontManager.ttflist:
        name = font.name
        fname = font.fname.lower()
        # 匹配常见中文字体
        if any(
            k in name
            for k in [
                "CJK",
                "WenQuanYi",
                "Noto Sans CJK",
                "SimHei",
                "Source Han",
                "Droid Sans Fallback",
                "AR PL UMing",
                "Microsoft YaHei",
                "Ubuntu",
            ]
        ):
            cjk_candidates.append(font.name)
        elif any(
            k in fname
            for k in [
                "noto/sans/cjk",
                "wqy",
                "simhei",
                "msyh",
                "simsun",
                "adobe/sourcehansans",
                "opentype/noto",
            ]
        ):
            cjk_candidates.append(font.name)

    # 去重并保持顺序
    seen = set()
    cjk_fonts = []
    for f in cjk_candidates:
        if f not in seen:
            seen.add(f)
            cjk_fonts.append(f)

    if cjk_fonts:
        # 全局 sans-serif 回退链：中文字体优先
        rcParams["font.family"] = ["sans-serif"]
        rcParams["font.sans-serif"] = cjk_fonts + ["DejaVu Sans"]
        rcParams["axes.unicode_minus"] = False

        # mathtext 自定义字体集 —— 这是 \text{} 能显示中文的关键
        rcParams["mathtext.fontset"] = "custom"
        rcParams["mathtext.rm"] = cjk_fonts[0]
        rcParams["mathtext.it"] = cjk_fonts[0] + ":italic"
        rcParams["mathtext.bf"] = cjk_fonts[0] + ":bold"
        rcParams["mathtext.sf"] = cjk_fonts[0]
        rcParams["mathtext.tt"] = cjk_fonts[0]
        rcParams["mathtext.cal"] = cjk_fonts[0]

        print(f"[LaTeX] 已配置中文字体: {cjk_fonts[0]}")
    else:
        print("[LaTeX] 警告: 系统未安装中文字体，中文将显示为方框")


_setup_fonts()


def render_latex(formula: str, filename: str = None) -> str:
    if not filename:
        filename = f"{uuid.uuid4()}.png"
    output_path = os.path.join(OUTPUT_DIR, filename)

    clean_formula = formula.strip()
    if clean_formula.startswith("$") and clean_formula.endswith("$"):
        clean_formula = clean_formula[1:-1]

    try:
        fig = plt.figure(figsize=(max(4, len(clean_formula) * 0.25), 1.2))
        fig.text(
            0.5,
            0.5,
            f"${clean_formula}$",
            fontsize=24,
            ha="center",
            va="center",
            color="black",
        )
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
