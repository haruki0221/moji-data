"""見本画像：形（三角形）・当たり判定（赤い枠）・尖った先端（青い丸）・重心（＋）"""
import json
import sys
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import factory as F

font_manager.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]

KIND_COLOR = {"POWER": "#ff7814", "SPEED": "#00b4e6", "BALANCE": "#28c85a", "CRITICAL": "#c846ff"}


def draw(items, out, cols=8):
    rows = (len(items) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.2, rows * 2.5))
    for ax in axes.flat:
        ax.axis("off")
    for ax, g in zip(axes.flat, items):
        ax.set_xlim(-1.9, 1.9)
        ax.set_ylim(-1.9, 1.9)
        ax.set_aspect("equal")
        if not g["ok"]:
            ax.text(0, 0, f"{g['c']}\n×\n{g['why']}", ha="center", va="center", fontsize=11, color="#999")
            continue
        v = g["v"]
        P = [(v[2 * i], -v[2 * i + 1]) for i in range(len(v) // 2)]  # 画面では上向き
        col = KIND_COLOR[g["kind"]]
        t = g["t"]
        for i in range(0, len(t), 3):
            tri = [P[t[i]], P[t[i + 1]], P[t[i + 2]]]
            ax.fill([p[0] for p in tri], [p[1] for p in tri], color=col, alpha=0.55, lw=0)
        s = 0
        for n in g["rl"]:
            r = [P[g["r"][s + k]] for k in range(n)] + [P[g["r"][s]]]
            ax.plot([p[0] for p in r], [p[1] for p in r], color="#222", lw=0.7)
            s += n
        for cx, cz, w, d in g["boxes"]:
            ax.add_patch(plt.Rectangle((cx - w / 2, -cz - d / 2), w, d, fill=False, ec="#e01b1b", lw=0.6))
        for x, z, sh in g["tips"]:
            ax.scatter([x], [-z], s=20 + 140 * sh, c="#1450ff", alpha=0.75, zorder=5)
        ax.plot([0], [0], "k+", ms=8)
        ax.set_title(
            f"{g['c']}  {g['kind']}\n面積{g['area']:.2f} バランス{g['balance']:.2f}",
            fontsize=8.5,
            color=col,
        )
    plt.tight_layout()
    plt.savefig(out, dpi=90)
    plt.close(fig)


if __name__ == "__main__":
    style = sys.argv[1]
    chars = list(sys.argv[2])
    out = sys.argv[3]
    t0 = time.time()
    items = [F.build_glyph(c, style) for c in chars]
    dt = time.time() - t0
    sizes = [len(json.dumps({k: v for k, v in g.items() if k != "_check"}, separators=(",", ":"), ensure_ascii=False).encode()) for g in items if g["ok"]]
    bad = [g["c"] for g in items if g["ok"] and abs(g["_check"]["triArea"] - g["_check"]["polyArea"]) > 0.01]
    print(f"{len(items)}文字 {dt:.1f}秒  1文字あたり平均 {sum(sizes)/max(1,len(sizes))/1024:.1f}KB 最大 {max(sizes)/1024:.1f}KB  三角形ずれ:{bad}")
    print("作れなかった:", [(g["c"], g["why"]) for g in items if not g["ok"]])
    draw(items, out)
