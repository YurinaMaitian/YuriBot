import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
import os
import uuid
from typing import Optional

from core.registry import cmd
from services.actions import send_text, send_image

OUTPUT_DIR = "/tmp/qqbot/latex"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ========== 自动扫描并配置中文字体 ==========
def _setup_fonts():
    ttc_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

    # ========== 方案：提取 SC 子集到临时文件 ==========
    if os.path.exists(ttc_path):
        try:
            from fontTools.ttLib import TTCollection

            ttc = TTCollection(ttc_path)
            # NotoSansCJK-Regular.ttc 子集顺序: 0=JP, 1=KR, 2=SC, 3=TC
            sc_font = ttc[2]

            # 保存为临时 OTF
            tmp_dir = "/tmp/qqbot/fonts"
            os.makedirs(tmp_dir, exist_ok=True)
            sc_path = os.path.join(tmp_dir, "NotoSansCJKsc-Regular.otf")

            if not os.path.exists(sc_path):
                sc_font.save(sc_path)
                print(f"[LaTeX] 已提取 SC 子集到: {sc_path}")

            # 手动注册到 matplotlib
            fe = font_manager.FontEntry(
                fname=sc_path,
                name="Noto Sans CJK SC",
                style="normal",
                variant="normal",
                weight=400,
                stretch="normal",
                size="scalable",
            )
            font_manager.fontManager.ttflist.insert(0, fe)

            rcParams["font.family"] = ["Noto Sans CJK SC"]
            rcParams["axes.unicode_minus"] = False
            rcParams["mathtext.fontset"] = "custom"
            rcParams["mathtext.rm"] = "Noto Sans CJK SC"
            rcParams["mathtext.it"] = "Noto Sans CJK SC"
            rcParams["mathtext.bf"] = "Noto Sans CJK SC"
            rcParams["mathtext.sf"] = "Noto Sans CJK SC"
            rcParams["mathtext.tt"] = "Noto Sans CJK SC"
            rcParams["mathtext.cal"] = "Noto Sans CJK SC"

            print("[LaTeX] 已强制使用 Noto Sans CJK SC（简体中文）")
            return

        except ImportError:
            print("[LaTeX] 未安装 fontTools，尝试兜底方案...")
        except Exception as e:
            print(f"[LaTeX] 提取子集失败: {e}")

    # ========== 兜底：直接用 WenQuanYi ==========
    wqy_path = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    if os.path.exists(wqy_path):
        fp = font_manager.FontProperties(fname=wqy_path)
        name = fp.get_name()
        rcParams["font.family"] = [name]
        rcParams["axes.unicode_minus"] = False
        rcParams["mathtext.fontset"] = "custom"
        rcParams["mathtext.rm"] = name
        rcParams["mathtext.it"] = name
        rcParams["mathtext.bf"] = name
        rcParams["mathtext.sf"] = name
        rcParams["mathtext.tt"] = name
        rcParams["mathtext.cal"] = name
        print(f"[LaTeX] 兜底使用: {name}")
    else:
        print("[LaTeX] 警告: 找不到任何中文字体")


_setup_fonts()


def _render(formula: str, filename: str = None) -> Optional[str]:
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
    return None  # 已自行发送，框架不再发文字
