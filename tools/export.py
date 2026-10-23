"""
文字データを GitHub に置く形に書き出す（CPU を全部使って並列に作る）

  g/<字体>/<ブロック>.json   … 64文字ごと（ブロック番号 = コードポイント // 64 を16進数4桁）
     {"<コードポイント16進>": {文字データ}, ...}  ※作れなかった文字は入れない
  index.json                 … 版番号・字体・含まれるブロック一覧

文字データ（版2）
  a 面積 / m 重心から一番遠い点まで / b バランス / k タイプ頭文字（P S B C）
  v 頂点 [x, z, x, z, ...]（1/100 スタッドの整数）
  t 上面の三角形（頂点番号、0 始まり）
  rl 輪郭 [[最初の頂点番号, 頂点数（逆回りならマイナス）], ...]  ※外周は反時計回り・穴は時計回りの順になる
  x 当たり判定の長方形 [[中心x, 中心z, 幅, 奥行き], ...]（1/100 スタッドの整数）
  p 尖った先端 [[x, z, 鋭さ0〜1], ...]（x, z は 1/100 スタッドの整数）

使い方:
  python3 export.py <出力先> all                 … フォントに入っている全文字
  python3 export.py <出力先> 0020-007E 3040-309F … 範囲を指定
"""
import json
import os
import sys
import time
import unicodedata
from multiprocessing import Pool

import factory as F

BLOCK = 64
VERSION = 2
STYLES = ["gothic", "mincho", "pop"]


def compact(g):
    v = [int(round(x * 100)) for x in g["v"]]
    rl = []
    s = 0
    r = g["r"]
    for n in g["rl"]:
        seg = r[s : s + n]
        asc = all(seg[i + 1] == seg[i] + 1 for i in range(n - 1))
        rl.append([seg[0], n if asc else -n])
        s += n
    return {
        "a": g["area"],
        "m": g["maxR"],
        "b": g["balance"],
        "k": g["kind"][0],
        "v": v,
        "t": g["t"],
        "rl": rl,
        "x": [[int(round(x * 100)) for x in b] for b in g["boxes"]],
        "p": [[int(round(t[0] * 100)), int(round(t[1] * 100)), t[2]] for t in g["tips"]],
    }


def usable(cp):
    if cp < 0x21 or 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000:
        return False
    return unicodedata.category(chr(cp))[0] in "LNPS"


def all_codepoints(style):
    cps = set()
    for spec in F.STYLES[style]:
        _, cmap, _, _ = F.load_font(spec)
        cps |= set(cmap)
    return sorted(c for c in cps if usable(c))


def work(args):
    """1つのブロック（64文字）を作る。並列に呼ばれる"""
    style, b, cps = args
    out = {}
    bad = 0
    for cp in cps:
        try:
            g = F.build_glyph(chr(cp), style)
        except Exception:
            bad += 1
            continue
        if not g["ok"]:
            bad += 1
            continue
        ck = g["_check"]
        # 三角形の面積がずれている文字は使わない（見た目が崩れるため）
        if abs(ck["triArea"] - ck["polyArea"]) > max(0.01, ck["polyArea"] * 0.01):
            bad += 1
            continue
        out[f"{cp:X}"] = compact(g)
    return style, b, out, bad


def parse_ranges(args):
    cps = []
    for a in args:
        lo, _, hi = a.partition("-")
        lo = int(lo, 16)
        hi = int(hi, 16) if hi else lo
        cps.extend(range(lo, hi + 1))
    return [c for c in cps if usable(c)]


def main():
    out = sys.argv[1]
    index = {"version": VERSION, "block": BLOCK, "styles": {s: [] for s in STYLES}, "count": {}}
    jobs = []
    for style in STYLES:
        cps = all_codepoints(style) if sys.argv[2:] == ["all"] else parse_ranges(sys.argv[2:])
        blocks = {}
        for cp in cps:
            blocks.setdefault(cp // BLOCK, []).append(cp)
        for b, lst in sorted(blocks.items()):
            jobs.append((style, b, lst))
    total_chars = sum(len(j[2]) for j in jobs)
    print(f"{total_chars}文字 / {len(jobs)}ブロック", flush=True)
    t0 = time.time()
    done_chars, made, bad = 0, 0, 0
    with Pool(os.cpu_count()) as pool:
        for k, (style, b, chars, nbad) in enumerate(pool.imap_unordered(work, jobs, chunksize=1)):
            done_chars += len(chars) + nbad
            made += len(chars)
            bad += nbad
            if chars:
                d = os.path.join(out, "g", style)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"{b:04X}.json"), "w") as f:
                    json.dump(chars, f, ensure_ascii=False, separators=(",", ":"))
                index["styles"][style].append(f"{b:04X}")
                index["count"][style] = index["count"].get(style, 0) + len(chars)
            if k % 50 == 0:
                el = time.time() - t0
                rest = el / max(done_chars, 1) * (total_chars - done_chars)
                print(f"{done_chars}/{total_chars}  {el / 60:.1f}分経過  残り約{rest / 60:.0f}分  作れなかった{bad}", flush=True)
    for s in STYLES:
        index["styles"][s].sort()
    with open(os.path.join(out, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"完了：{made}文字 / 作れなかった {bad} / {(time.time() - t0) / 60:.1f}分", flush=True)


if __name__ == "__main__":
    main()
