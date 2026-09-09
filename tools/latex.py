import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import font_manager, rcParams
from matplotlib.path import Path
import asyncio
import os
import re
import subprocess
import tempfile
import uuid
from typing import Optional

from core.registry import cmd
from services.actions import send_text, send_image

OUTPUT_DIR = "/tmp/qqbot/latex"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FONT_SIZE = 24
DPI = 200

# mathtext 可剥离的数学环境（注意必须包含 aligned / alignedat / eqnarray）
ENV_RE = r"(?:align|aligned|alignat|alignedat|gather|gathered|equation|multline|split|eqnarray)\*?"

# 真 LaTeX 模板。注意：standalone 默认会把正文放进受限水平模式测宽，
# 一切 display 数学（\[\]、equation*、align）都会报
# "Bad math environment delimiter / \eqno in restricted horizontal mode"——
# 必须加 preview 选项，它用竖直模式排版再裁剪。
TEX_TEMPLATE = (
    "\\documentclass[border=10pt,preview]{standalone}\n"
    "\\usepackage{amsmath,amssymb}\n"
    "\\usepackage{fix-cm}\n"  # 允许 Computer Modern 使用任意字号
    "\\pagestyle{empty}\n"
    "\\begin{document}\n"
    "\\fontsize{22}{27}\\selectfont\n"  # 公式本体放大到 22pt，QQ 里不再显小
    "{body}\n"
    "\\end{document}\n"
)


# ========== 自动扫描并配置中文字体（仅 matplotlib 兜底路径需要） ==========
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
            # 关键：数学符号交给 matplotlib 自带的 STIX 字体集，
            # \mathcal \mathbb \mathfrak 等字形齐全。
            # 千万不要把 mathtext.cal 指到中文字体上，否则 \mathcal{R} 渲染成 □
            rcParams["mathtext.fontset"] = "stix"

            print("[LaTeX] 中文: Noto Sans CJK SC，数学: STIX")
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
        rcParams["mathtext.fontset"] = "stix"
        print(f"[LaTeX] 兜底使用: {name}，数学: STIX")
    else:
        print("[LaTeX] 警告: 找不到任何中文字体")


_setup_fonts()


# ==================== 路径一：真 LaTeX（pdflatex + pdftoppm） ====================


def _tex_sanitize(formula: str) -> str:
    """只去包装，不改写语法：真 LaTeX 原生认识 align/cases/tag/&。"""
    f = formula.strip()
    if f.startswith("```"):  # 去代码块围栏
        inner = f.split("\n")[1:-1]
        if inner and inner[0].strip() in ("latex", "tex"):
            inner = inner[1:]
        f = "\n".join(inner).strip()
    return f.strip().strip("$").strip()  # 去首尾 $ / $$ 包裹


def _tex_body(f: str) -> str:
    """选择最稳妥的包装方式：
    - 自带 align/gather/multline 等 display 环境的直接放行（它们不能嵌套）；
    - 其余（普通公式、cases、\tag）一律包进 amsmath 的 equation*——
      绝不用 \\[...\\] 或 \\(...\\)，对齐类环境对这两种包装很敏感。
    """
    if re.search(r"\\begin\{(?:align|alignat|gather|multline|eqnarray)\*?\}", f):
        return f
    return "\\begin{equation*}\n" + f + "\n\\end{equation*}"


def _render_with_tex(formula: str, engine: str = "pdflatex") -> Optional[str]:
    """用真正的 LaTeX 渲染（pdflatex 或 xelatex），效果与论文一致。失败返回 None。"""
    f = _tex_sanitize(formula)
    tex_src = TEX_TEMPLATE.replace("{body}", _tex_body(f))

    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "f.tex"), "w", encoding="utf-8") as fp:
            fp.write(tex_src)
        p = subprocess.run(
            [engine, "-interaction=nonstopmode", "-halt-on-error", "f.tex"],
            cwd=td,
            capture_output=True,
            timeout=30,
        )
        pdf = os.path.join(td, "f.pdf")
        if p.returncode != 0 or not os.path.exists(pdf):
            print(f"[LaTeX] [{engine}] 生成的 tex 源:\n{tex_src}")
            print(
                f"[LaTeX] [{engine}] 编译失败:\n{p.stdout.decode(errors='ignore')[-800:]}"
            )
            return None
        out = os.path.join(OUTPUT_DIR, f"tex_{uuid.uuid4().hex}")
        subprocess.run(
            ["pdftoppm", "-png", "-r", "300", "-singlefile", pdf, out],
            capture_output=True,
            timeout=30,
        )
        png = out + ".png"
        if os.path.exists(png):
            try:
                from PIL import Image

                with Image.open(png) as im:
                    if im.width / max(im.height, 1) > 3.5:
                        print(
                            "[LaTeX] 图片过宽（>3.5:1），公式建议使用 \\begin{align} 或 \\begin{multline} 分行"
                        )
            except Exception:
                pass
            return png
        return None


async def render_best(formula: str) -> Optional[str]:
    """真 LaTeX 优先；未安装或编译失败时回退到 matplotlib mathtext。
    全程在线程池执行，不阻塞事件循环。"""
    loop = asyncio.get_event_loop()
    for engine in ("pdflatex", "xelatex"):
        try:
            r = await loop.run_in_executor(None, _render_with_tex, formula, engine)
            if r:
                if engine != "pdflatex":
                    print(f"[LaTeX] 注意：{engine} 编译成功，pdflatex 可能有问题")
                return r
        except FileNotFoundError:
            print(f"[LaTeX] 未安装 {engine}")
        except Exception as e:
            print(f"[LaTeX] [{engine}] 渲染异常: {e}")
    return _render(formula)  # 同步函数，本身很快（ms 级）


# ==================== 路径二：matplotlib mathtext 兜底 ====================


def _split_lines(f: str) -> list:
    """按"花括号深度为 0"的 \\\\ 拆行；行首行尾残留的 $ 一并剥掉。"""
    lines, cur = [], []
    depth, i = 0, 0
    while i < len(f):
        ch = f[i]
        if ch == "\\" and i + 1 < len(f) and f[i + 1] in "{}":  # \\{ \\} 转义，不计深度
            cur.append(f[i : i + 2])
            i += 2
            continue
        if ch == "\\" and i + 1 < len(f) and f[i + 1] == "\\" and depth == 0:
            lines.append("".join(cur))
            cur = []
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        cur.append(ch)
        i += 1
    lines.append("".join(cur))
    return [ln.strip(" $") for ln in lines if ln.strip(" $")]


def _clean(formula: str) -> list:
    """把用户输入（可能被 QQ/框架预 mangled 过）的 LaTeX 清洗成 item 列表：

    - ("line", 公式)                        单行公式
    - ("cases", 前缀, [(左列, 右列)...], 编号)  分段函数块（左列=表达式，右列=条件）
    """
    f = formula.strip()

    # 1. 去掉 ``` / ```latex 代码块
    if f.startswith("```"):
        inner = f.split("\n")[1:-1]
        if inner and inner[0].strip() in ("latex", "tex"):
            inner = inner[1:]
        f = "\n".join(inner).strip()

    # 2. 按 $$ 切块再拼回：专治 $$$$ 粘连，以及 QQ markdown 把
    #    $$...$$ 从中间拆成两段的情况
    parts = [p.strip() for p in f.split("$$") if p.strip()]
    f = r"\\".join(parts)

    # 3. 去掉整段残留的 $ 包裹
    if f.startswith("$") and f.endswith("$"):
        f = f[1:-1].strip()

    items = []

    # 4. 提取 cases 块：正则跨行匹配，分支内的 & 拆成左右两列
    m = re.search(r"\\begin\{cases\}(.*?)\\end\{cases\}", f, re.S)
    if m:
        prefix = f[: m.start()].strip()
        rest = f[m.end() :].strip()
        tm = re.match(r"\\tag\{([^}]*)\}", rest)
        tag = tm.group(1) if tm else None
        if tm:
            rest = rest[tm.end() :].strip()
        pairs = []
        for b in _split_lines(m.group(1).replace("\n", r"\\")):
            b = b.strip(" $")
            if not b:
                continue
            if "&" in b:
                l, r = b.split("&", 1)
                pairs.append((l.strip(), r.strip()))
            else:
                pairs.append((b, None))
        if pairs:
            items.append(("cases", prefix, pairs, tag))
        f = rest

    # 5. 其余部分按行处理
    f = re.sub(r"\\begin\{" + ENV_RE + r"\}", "", f)
    f = re.sub(r"\\end\{" + ENV_RE + r"\}", "", f)
    f = f.replace(r"\notag", "")
    f = re.sub(r"\\tag\{([^}]*)\}", r"\\quad(\1)", f)
    f = f.replace("&", r"\quad")
    f = f.replace("\n", r"\\")
    for ln in _split_lines(f):
        items.append(("line", ln))

    return items or [("line", f)]


def _measure_all(strs: list, fontsize: int) -> list:
    """量出每个字符串的真实渲染宽高（英寸）。

    用 get_window_extent 拿实际像素包围盒，比按字符数估宽准确得多
    （\\frac{\\partial^2 u}{\\partial x^2} 40 个字符渲染出来只有一英寸宽，
    字符数启发式必然失真）。
    """
    fig = plt.figure(figsize=(14, 9))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    out = []
    for s in strs:
        t = fig.text(0, 0, s, fontsize=fontsize)
        bb = t.get_window_extent(renderer=renderer)
        out.append((bb.width / fig.dpi, bb.height / fig.dpi))
        t.remove()
    plt.close(fig)
    return out


def _left_brace(ax, x, y_top, y_bot):
    """在 inches 坐标里画左大括号 {（矢量路径，笔画粗细随高度自适应）。

    不能用放大版 "$\\{$" 字形代替：文本 artist 的窗口范围是整个 em 盒，
    bbox_inches='tight' 会把画布撑爆。
    """
    h = y_top - y_bot
    ym = (y_top + y_bot) / 2
    w = max(h * 0.10, 0.06)
    d = h * 0.05  # 尖端半高

    def cub(c1, c2, p1):
        return [c1, c2, p1]

    verts = [(x + w, y_top)]
    verts += cub((x + w * 0.55, y_top), (x, y_top - h * 0.30), (x, ym + d))
    verts += cub((x, ym + d * 0.5), (x + w * 0.60, ym), (x + w, ym))
    verts += cub((x + w * 0.60, ym), (x, ym - d * 0.5), (x, ym - d))
    verts += cub((x, y_bot + h * 0.30), (x + w * 0.55, y_bot), (x + w, y_bot))
    codes = [Path.MOVETO] + [Path.CURVE4] * 12
    patch = mpatches.PathPatch(
        Path(verts, codes),
        facecolor="none",
        edgecolor="black",
        lw=max(1.2, h * 72 * 0.045),
        capstyle="round",
        joinstyle="round",
    )
    ax.add_patch(patch)


def _render(formula: str, filename: str = None) -> Optional[str]:
    """matplotlib mathtext 兜底渲染（服务器没装 pdflatex 时走这里）。"""
    if not filename:
        filename = f"{uuid.uuid4()}.png"
    output_path = os.path.join(OUTPUT_DIR, filename)

    items = _clean(formula)
    try:
        line_gap = FONT_SIZE * 1.2 / 72  # 块间距（英寸）
        pad_w, pad_h = 0.5, 0.4
        COL_GAP = 0.22  # cases 左右两列间距
        BRACE_W = 0.34  # cases 大括号预留列宽

        # ---------- 第一遍：测量 ----------
        blocks = []
        for it in items:
            if it[0] == "line":
                w, h = _measure_all([f"${it[1]}$"], FONT_SIZE)[0]
                blocks.append(["line", it[1], w, h])
            else:
                _, prefix, pairs, tag = it
                strs = []
                for l, r in pairs:
                    strs.append(f"${l}$")
                    if r:
                        strs.append(f"${r}$")
                pw = _measure_all([f"${prefix}$"], FONT_SIZE)[0][0] if prefix else 0.0
                tw = _measure_all([f"$({tag})$"], FONT_SIZE)[0][0] if tag else 0.0
                ms = _measure_all(strs, FONT_SIZE)
                rows, idx = [], 0
                max_lw = max_rw = 0.0
                hs = []
                for l, r in pairs:
                    lw, lh = ms[idx]
                    idx += 1
                    if r:
                        rw, rh = ms[idx]
                        idx += 1
                    else:
                        rw, rh = 0.0, lh
                    rows.append((l, r, lw, rw, max(lh, rh)))
                    max_lw = max(max_lw, lw)
                    max_rw = max(max_rw, rw)
                    hs.append(max(lh, rh))
                row_gap = line_gap * 0.7
                block_h = sum(hs) + row_gap * (len(rows) - 1)
                inner_w = (
                    pw
                    + 0.10
                    + BRACE_W
                    + 0.06
                    + max_lw
                    + ((COL_GAP + max_rw) if max_rw else 0)
                )
                w = inner_w + ((0.30 + tw) if tag else 0)
                blocks.append(
                    ["cases", (prefix, rows, tag, pw, tw, max_lw, row_gap), w, block_h]
                )

        W = max(b[2] for b in blocks) + pad_w
        H = sum(b[3] for b in blocks) + line_gap * (len(blocks) - 1) + pad_h

        # ---------- 第二遍：绘制（全程英寸坐标，不换算 0~1 比例） ----------
        fig = plt.figure(figsize=(W, H))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.axis("off")

        y = H - pad_h / 2
        for b in blocks:
            y -= b[3] / 2
            if b[0] == "line":
                ax.text(
                    W / 2,
                    y,
                    f"${b[1]}$",
                    fontsize=FONT_SIZE,
                    ha="center",
                    va="center",
                    color="black",
                )
            else:
                prefix, rows, tag, pw, tw, max_lw, row_gap = b[1]
                x0 = (W - b[2]) / 2
                x = x0
                if prefix:
                    ax.text(
                        x + pw,
                        y,
                        f"${prefix}$",
                        fontsize=FONT_SIZE,
                        ha="right",
                        va="center",
                        color="black",
                    )
                    x += pw + 0.10
                _left_brace(ax, x, y + b[3] / 2, y - b[3] / 2)
                x += BRACE_W + 0.06
                x_l, x_r = x, x + max_lw + COL_GAP
                ry = y + b[3] / 2
                for l, r, lw, rw, rh in rows:
                    ry -= rh / 2
                    ax.text(
                        x_l,
                        ry,
                        f"${l}$",
                        fontsize=FONT_SIZE,
                        ha="left",
                        va="center",
                        color="black",
                    )
                    if r:
                        ax.text(
                            x_r,
                            ry,
                            f"${r}$",
                            fontsize=FONT_SIZE,
                            ha="left",
                            va="center",
                            color="black",
                        )
                    ry -= rh / 2 + row_gap
                if tag:
                    ax.text(
                        x0 + b[2] - tw,
                        y,
                        f"$({tag})$",
                        fontsize=FONT_SIZE,
                        ha="left",
                        va="center",
                        color="black",
                    )
            y -= b[3] / 2 + line_gap

        fig.savefig(
            output_path,
            dpi=DPI,
            bbox_inches="tight",
            pad_inches=0.15,
            facecolor="white",
        )
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"[LaTeX渲染失败] {e}")
        return None


# ==================== 命令入口 ====================


@cmd("latex", desc="渲染LaTeX公式为图片，用法: /latex \\int_0^1 x^2 dx")
async def latex_cmd(ctx):
    formula = ctx.raw.strip()
    if not formula:
        return "用法：/latex \\int_0^1 x^2 dx"

    print(f"[LaTeX] raw = {formula!r}")  # 调试用：看 QQ 实际发来的是什么

    img_path = await render_best(formula)
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
