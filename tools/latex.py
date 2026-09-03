import matplotlib
matplotlib.use('Agg')  # 无头模式，不需要 GUI
import matplotlib.pyplot as plt
import os

# 确保输出目录存在
OUTPUT_DIR = "/tmp/qqbot/latex"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def render_latex(formula: str, filename: str = None) -> str:
    """
    把 LaTeX 公式渲染成 PNG 图片
    返回图片路径
    """
    if not filename:
        import uuid
        filename = f"{uuid.uuid4()}.png"
    
    output_path = os.path.join(OUTPUT_DIR, filename)
    
    # 清理公式：去掉首尾 $ 如果用户带了
    clean_formula = formula.strip()
    if clean_formula.startswith('$') and clean_formula.endswith('$'):
        clean_formula = clean_formula[1:-1]
    
    try:
        # 用 matplotlib 的 mathtext 渲染
        fig = plt.figure(figsize=(max(4, len(clean_formula) * 0.25), 1.2))
        fig.text(
            0.5, 0.5,
            f'${clean_formula}$',
            fontsize=24,
            ha='center',
            va='center',
            color='black'
        )
        fig.patch.set_facecolor('white')
        fig.savefig(
            output_path,
            dpi=200,
            bbox_inches='tight',
            pad_inches=0.15,
            facecolor='white'
        )
        plt.close(fig)
        
        return output_path
        
    except Exception as e:
        print(f"[LaTeX渲染失败] {e}")
        return None
