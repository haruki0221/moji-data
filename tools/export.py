"""
文字データを GitHub に置く形に書き出す

  g/<字体>/<ブロック>.json   … 64文字ごと（ブロック番号 = コードポイント // 64 を16進数4桁以上）
     {"<コードポイント16進>": {文字データ}, ...}  ※作れなかった文字は入れない
  index.json                 … 版番号・字体・含まれるブロック一覧

使い方: python3 export.py <出力先> <範囲...>
  範囲の例: 0020-007E 3040-309F 4E00-4E3F
"""
import json
import os
import sys
import time

import factory as F

BLOCK = 64
STYLES = ["gothic", "mincho", "pop"]


def compact(g):
    r2 = lambda x: round(float(x), 2)
    return {
        "a": g["area"],
        "m": g["maxR"],
        "b": g["balance"],
        "k": g["kind"][0],  # P / S / B / C
        "v": [r2(x) for x in g["v"]],
        "t": g["t"],
        "r": g["r"],
        "rl": g["rl"],
        "x": [[r2(x) for x in b] for b in g["boxes"]],
        "p": [[r2(t[0]), r2(t[1]), r2(t[2])] for t in g["tips"]],
    }


def parse_ranges(args):
    cps = []
    for a in args:
        lo, _, hi = a.partition("-")
        lo = int(lo, 16)
        hi = int(hi or lo, 16) if hi else lo
        cps.extend(range(lo, hi + 1))
    return cps


def main():
    out = sys.argv[1]
    cps = parse_ranges(sys.argv[2:])
    index_path = os.path.join(out, "index.json")
    index = {"version": 1, "block": BLOCK, "styles": {}}
    if os.path.exists(index_path):
        index = json.load(open(index_path))
    t0 = time.time()
    total, skipped = 0, 0
    for style in STYLES:
        blocks = {}
        for cp in cps:
            ch = chr(cp)
            if ch.isspace():
                continue
            try:
                g = F.build_glyph(ch, style)
            except Exception as e:  # 1文字の失敗で全体を止めない
                print("error", style, hex(cp), e)
                skipped += 1
                continue
            if not g["ok"]:
                skipped += 1
                continue
            blocks.setdefault(cp // BLOCK, {})[f"{cp:X}"] = compact(g)
            total += 1
        d = os.path.join(out, "g", style)
        os.makedirs(d, exist_ok=True)
        have = set(index["styles"].get(style, []))
        for b, chars in blocks.items():
            path = os.path.join(d, f"{b:04X}.json")
            if os.path.exists(path):  # 既存のブロックに足す
                old = json.load(open(path))
                old.update(chars)
                chars = old
            with open(path, "w") as f:
                json.dump(chars, f, ensure_ascii=False, separators=(",", ":"))
            have.add(f"{b:04X}")
        index["styles"][style] = sorted(have)
    with open(index_path, "w") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"{total}文字（字体ごと合計） / 作れなかった {skipped} / {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
