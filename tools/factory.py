"""
文字工場：フォントから「文字コマ」のデータを作る

1文字ごとに次を計算して JSON に書き出す
  - 輪郭（単純化した多角形、穴あり）と、上面の三角形分割 → Roblox で厚みを付けて 3D にする
  - 当たり判定の長方形（Roblox の Part にする）
  - ステータス：面積（重さ）、バランス（丸さ・偏りの少なさ）、尖った先端（クリティカル）

座標系：x = 右、z = 下（Roblox で文字を寝かせたとき -Z が文字の「上」）、原点 = 重心
単位：スタッド
"""
import json
import math
import sys
from dataclasses import dataclass

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw
from scipy.spatial import ConvexHull
from scipy import ndimage
import contourpy

EM_STUDS = 2.7  # フォントの 1em を何スタッドにするか（全文字共通 → 小さい文字は小さいコマ）
SIMPLIFY = 0.012  # 輪郭の単純化の許容誤差（スタッド）
MASK_RES = 0.01  # 面積・重心を測るラスタの 1px（スタッド）
BOX_CELL = 0.14  # 当たり判定の格子（スタッド）
BOX_MAX = 48  # 当たり判定の長方形の上限
TIP_WINDOW = 0.22  # 尖りを測る範囲（スタッド）。狭いほど「本当に鋭い角」だけになる
TIP_MIN = 0.3  # これ以上の尖りだけ「先端」とする（0〜1）
MIN_AREA = 0.04  # これより小さい文字はコマにできない（スタッド²）

# ---------------------------------------------------------------- フォント


@dataclass
class FontSpec:
    path: str
    index: int = 0  # .ttc の何番目か


# 字体ごとの探す順番（左から順に、その文字を持っているフォントを使う）
# 試作用：この環境に入っているフォント。本番では Noto の各言語版・Fredoka などに差し替える
STYLES = {
    "gothic": [
        FontSpec("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        FontSpec("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        FontSpec("/usr/share/fonts/opentype/tlwg/Loma-Bold.otf"),
        FontSpec("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ],
    "mincho": [
        FontSpec("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 0),
        FontSpec("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        FontSpec("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
    ],
    "pop": [
        FontSpec("/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"),
        FontSpec("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", 0),
        FontSpec("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        FontSpec("/usr/share/fonts/opentype/tlwg/Loma-Bold.otf"),
        FontSpec("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ],
}

_font_cache = {}


def load_font(spec):
    key = (spec.path, spec.index)
    if key not in _font_cache:
        f = TTFont(spec.path, fontNumber=spec.index, lazy=True)
        _font_cache[key] = (f, f.getBestCmap(), f.getGlyphSet(), f["head"].unitsPerEm)
    return _font_cache[key]


def find_glyphs(ch, style):
    """その文字を持っているフォントを順番に返す（描けなかったら次のフォントを試す）"""
    cp = ord(ch)
    for spec in STYLES[style]:
        font, cmap, gs, upem = load_font(spec)
        name = cmap.get(cp)
        if name and name in gs:
            yield gs[name], upem, spec.path, gs


def find_glyph(ch, style):
    for g in find_glyphs(ch, style):
        return g[:3]
    return None, None, None


# ---------------------------------------------------------------- 輪郭の取り出し


class FlattenPen(BasePen):
    """曲線を細かい折れ線にしながら輪郭を集める"""

    def __init__(self, glyphSet, step):
        super().__init__(glyphSet)
        self.step = step
        self.contours = []
        self.cur = None

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        n = int(max(2, (math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)) // self.step + 2))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
            y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
            self.cur.append((x, y))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        n = int(max(2, (math.dist(p0, p1) + math.dist(p1, p2)) // self.step + 2))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.cur.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))

    def _closePath(self):
        if self.cur and len(self.cur) > 2:
            if math.dist(self.cur[0], self.cur[-1]) < 1e-6:
                self.cur.pop()
            self.contours.append(self.cur)
        self.cur = None

    _endPath = _closePath


def rdp(pts, eps):
    """Douglas-Peucker（閉じた輪郭用：一番遠い2点で2分割して処理）。距離はまとめて numpy で計算"""
    if len(pts) < 4:
        return pts
    P = np.asarray(pts, dtype=float)
    b = int(np.argmax(np.hypot(P[:, 0] - P[0, 0], P[:, 1] - P[0, 1])))

    def simp(Q):
        n = len(Q)
        if n < 3:
            return list(range(n))
        keep = np.zeros(n, dtype=bool)
        keep[0] = keep[-1] = True
        stack = [(0, n - 1)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            seg = Q[i + 1 : j]
            v = Q[j] - Q[i]
            L = math.hypot(v[0], v[1])
            if L > 1e-12:
                d = np.abs(v[0] * (seg[:, 1] - Q[i, 1]) - v[1] * (seg[:, 0] - Q[i, 0])) / L
            else:
                d = np.hypot(seg[:, 0] - Q[i, 0], seg[:, 1] - Q[i, 1])
            k = int(np.argmax(d))
            if d[k] > eps:
                m = i + 1 + k
                keep[m] = True
                stack.append((i, m))
                stack.append((m, j))
        return list(np.nonzero(keep)[0])

    left = P[: b + 1]
    right = np.vstack([P[b:], P[:1]])
    li = simp(left)
    ri = simp(right)
    out = [tuple(left[i]) for i in li[:-1]] + [tuple(right[i]) for i in ri[:-1]]
    return out


def signed_area(r):
    a = 0.0
    n = len(r)
    for i in range(n):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2


def point_in_ring(p, r):
    x, y = p
    inside = False
    n = len(r)
    for i in range(n):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


# ---------------------------------------------------------------- 三角形分割（耳切り＋穴の橋渡し）


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _in_tri(p, a, b, c):
    # 頂点と同じ位置の点（穴の橋渡しで重なる点）は数えない。辺の上の点は「中」とみなす
    if p == a or p == b or p == c:
        return False
    d1, d2, d3 = _cross(a, b, p), _cross(b, c, p), _cross(c, a, p)
    return d1 >= -1e-12 and d2 >= -1e-12 and d3 >= -1e-12


def _seg_intersect(p1, p2, p3, p4):
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _in_sector(prev, v, nxt, p):
    """CCW の輪の頂点 v の内側の向きに p があるか"""
    if _cross(prev, v, nxt) >= 0:  # 凸
        return _cross(prev, v, p) >= 0 and _cross(v, nxt, p) >= 0
    return _cross(prev, v, p) >= 0 or _cross(v, nxt, p) >= 0


def bridge_holes(outer, holes, coords):
    """outer/holes は頂点番号の列。穴を外周につないで 1 本の輪にする"""
    ring = list(outer)
    holes = sorted(holes, key=lambda h: -max(coords[i][0] for i in h))
    for n_done, hole in enumerate(holes):
        hi = max(range(len(hole)), key=lambda k: coords[hole[k]][0])
        hp = coords[hole[hi]]
        edges = [(ring[k], ring[(k + 1) % len(ring)]) for k in range(len(ring))]
        for h2 in holes[n_done:]:
            edges += [(h2[k], h2[(k + 1) % len(h2)]) for k in range(len(h2))]
        cands = sorted(range(len(ring)), key=lambda k: math.dist(coords[ring[k]], hp))
        chosen = None
        for k in cands:
            q = coords[ring[k]]
            if not _in_sector(coords[ring[k - 1]], q, coords[ring[(k + 1) % len(ring)]], hp):
                continue
            ok = True
            for a, b in edges:
                if ring[k] in (a, b) or hole[hi] in (a, b):
                    continue
                if _seg_intersect(hp, q, coords[a], coords[b]):
                    ok = False
                    break
            if ok:
                chosen = k
                break
        if chosen is None:
            chosen = cands[0]
        h_rot = hole[hi:] + hole[:hi]
        ring = ring[: chosen + 1] + h_rot + [h_rot[0], ring[chosen]] + ring[chosen + 1 :]
    return ring


def earclip(ring, coords):
    """CCW の 1 本の輪（橋渡しで同じ頂点番号が 2 回出てもよい）を三角形に分ける
    へこんだ点（reflex）だけを覚えておき、耳の判定はその点だけで行う"""
    n = len(ring)
    if n < 3:
        return []
    P = [coords[i] for i in ring]
    prv = [(k - 1) % n for k in range(n)]
    nxt = [(k + 1) % n for k in range(n)]

    def cr(k):
        return _cross(P[prv[k]], P[k], P[nxt[k]])

    reflex = {k for k in range(n) if cr(k) <= 1e-14}
    tris = []
    alive = n
    k = 0
    stall = 0
    while alive > 3:
        a, c = prv[k], nxt[k]
        ear = False
        if k not in reflex:
            ear = True
            pa, pb, pc = P[a], P[k], P[c]
            ga, gb, gc = ring[a], ring[k], ring[c]
            for r in reflex:
                if r in (a, k, c) or ring[r] in (ga, gb, gc):
                    continue
                if _in_tri(P[r], pa, pb, pc):
                    ear = False
                    break
        if not ear and stall > alive:
            # 形が崩れている所は一番とがった点を切り落として先へ進む
            best, bc, j = k, -1e18, k
            for _ in range(alive):
                v = cr(j)
                if v > bc:
                    bc, best = v, j
                j = nxt[j]
            k = best
            a, c = prv[k], nxt[k]
            ear = True
        if ear:
            tris.append((ring[a], ring[k], ring[c]))
            nxt[a], prv[c] = c, a
            reflex.discard(k)
            alive -= 1
            for j in (a, c):
                if cr(j) > 1e-14:
                    reflex.discard(j)
                else:
                    reflex.add(j)
            k = c
            stall = 0
        else:
            k = nxt[k]
            stall += 1
    a, c = prv[k], nxt[k]
    tris.append((ring[a], ring[k], ring[c]))
    return [t for t in tris if abs(_cross(coords[t[0]], coords[t[1]], coords[t[2]])) > 1e-12]


def delaunay_fill(outer, holes, coords, spacing=0.03):
    """保険：境界を細かく区切って三角形に分け、穴の外・外周の中にあるものだけ残す"""
    from scipy.spatial import Delaunay

    rings = [outer] + holes
    base = len(coords)
    pts_idx = []
    for r in rings:
        for k in range(len(r)):
            a, b = coords[r[k]], coords[r[(k + 1) % len(r)]]
            pts_idx.append(r[k])
            n = int(math.dist(a, b) / spacing)
            for s in range(1, n):
                t = s / n
                coords.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                pts_idx.append(len(coords) - 1)
    P = np.array([coords[i] for i in pts_idx])
    D = Delaunay(P)
    from matplotlib.path import Path as MPath

    S = D.simplices
    G = (P[S[:, 0]] + P[S[:, 1]] + P[S[:, 2]]) / 3
    inside = MPath(np.array([coords[i] for i in outer])).contains_points(G)
    for h in holes:
        inside &= ~MPath(np.array([coords[i] for i in h])).contains_points(G)
    tris = []
    for (a, b, c), ok in zip(S, inside):
        ia, ib, ic = pts_idx[a], pts_idx[b], pts_idx[c]
        if ok:
            # 外周は反時計回りにそろえる
            if _cross(coords[ia], coords[ib], coords[ic]) < 0:
                ib, ic = ic, ib
            tris.append((ia, ib, ic))
    return tris


# ---------------------------------------------------------------- 1文字の処理


def build_glyph(ch, style):
    pen = None
    for glyph, upem, fontpath, gs in find_glyphs(ch, style):
        try:
            p = FlattenPen(gs, step=upem * 0.02)  # 組み合わせ文字（Ć など）の部品を読むためにフォントを渡す
            glyph.draw(p)
            pen = p
            break
        except Exception:
            continue
    if pen is None:
        return {"c": ch, "style": style, "ok": False, "why": "no font"}
    k = EM_STUDS / upem
    raw = [[(x * k, -y * k) for x, y in c] for c in pen.contours if len(c) >= 3]  # z = 下向き
    if not raw:
        return {"c": ch, "style": style, "ok": False, "why": "empty"}

    # --- 塗りをラスタにする（非ゼロ回転数ルール：線が重なって描かれていても正しく合成）
    allp = np.array([p for r in raw for p in r])
    mn, mx = allp.min(0) - 0.05, allp.max(0) + 0.05
    W, H = int((mx[0] - mn[0]) / MASK_RES) + 2, int((mx[1] - mn[1]) / MASK_RES) + 2
    wind = np.zeros((H, W), dtype=np.int16)
    for r in raw:
        sa = signed_area(r)
        if abs(sa) < 1e-9:
            continue
        layer = Image.new("L", (W, H), 0)
        ImageDraw.Draw(layer).polygon([((x - mn[0]) / MASK_RES, (z - mn[1]) / MASK_RES) for x, z in r], fill=1)
        wind += np.array(layer, dtype=np.int16) * (1 if sa > 0 else -1)
    m = wind != 0
    area = float(m.sum()) * MASK_RES**2
    if area < MIN_AREA:
        return {"c": ch, "style": style, "ok": False, "why": "too small", "area": round(area, 4)}
    ys, xs = np.nonzero(m)
    cx = mn[0] + (xs.mean() + 0.5) * MASK_RES
    cz = mn[1] + (ys.mean() + 0.5) * MASK_RES
    mn = mn - np.array([cx, cz])

    # --- 合成した塗りから、重なりのないきれいな輪郭（外周＋穴）を取り直す
    zf = ndimage.gaussian_filter(m.astype(np.float32), 0.8)
    gen = contourpy.contour_generator(z=zf, fill_type=contourpy.FillType.OuterOffset)
    polys_px, offs_list = gen.filled(0.5, 10.0)
    coords = []
    polys = []  # [(外周の頂点番号, [穴の頂点番号, ...])]
    rings_len = []
    for pts, offs in zip(polys_px, offs_list):
        ids_list = []
        for a, b in zip(offs[:-1], offs[1:]):
            ring = [((px + 0.5) * MASK_RES + mn[0], (py + 0.5) * MASK_RES + mn[1]) for px, py in pts[a:b]]
            if math.dist(ring[0], ring[-1]) < 1e-9:
                ring = ring[:-1]
            ring = rdp(ring, SIMPLIFY)
            if len(ring) < 3 or abs(signed_area(ring)) < 1e-4:
                ids_list.append(None)
                continue
            start = len(coords)
            coords.extend(ring)
            ids_list.append(list(range(start, start + len(ring))))
            rings_len.append(len(ring))
        if ids_list and ids_list[0] is not None:
            polys.append((ids_list[0], [h for h in ids_list[1:] if h is not None]))
        else:
            for h in ids_list:
                if h is not None:  # 外周が消えた場合は穴も捨てる
                    del coords[h[0]:h[-1] + 1]
                    rings_len.pop()
    if not polys:
        return {"c": ch, "style": style, "ok": False, "why": "empty"}

    def ring_area(ids):
        return signed_area([coords[i] for i in ids])

    # 外周は反時計回り、穴は時計回り（x右・z下 の座標での数学的な向き）
    fixed = []
    for outer, holes in polys:
        if ring_area(outer) < 0:
            outer = outer[::-1]
        holes = [h[::-1] if ring_area(h) > 0 else h for h in holes]
        fixed.append((outer, holes))
    polys = fixed
    ring_out = [i for o, hs in polys for r in [o] + hs for i in r]
    ring_len = [len(r) for o, hs in polys for r in [o] + hs]
    tris = []
    for outer, holes in polys:
        want = ring_area(outer) + sum(ring_area(h) for h in holes)
        ring = bridge_holes(outer, holes, coords)
        part = earclip(ring, coords)
        got = sum(abs(_cross(coords[a], coords[b], coords[c])) / 2 for a, b, c in part)
        if abs(got - want) > max(0.002, want * 0.005):
            part = delaunay_fill(outer, holes, coords)  # 詰まった形だけ別の方法で
        tris += part
    poly_area = sum(ring_area(o) + sum(ring_area(h) for h in hs) for o, hs in polys)
    tri_area = sum(abs(_cross(coords[a], coords[b], coords[c])) / 2 for a, b, c in tris)

    # --- 当たり判定：格子で塗られている所を長方形にまとめる
    cell = BOX_CELL
    while True:
        gw, gh = int(math.ceil((W * MASK_RES) / cell)), int(math.ceil((H * MASK_RES) / cell))
        step = cell / MASK_RES
        occ = np.zeros((gh, gw), dtype=bool)
        for gj in range(gh):
            for gi in range(gw):
                blk = m[int(gj * step) : int((gj + 1) * step), int(gi * step) : int((gi + 1) * step)]
                occ[gj, gi] = blk.size > 0 and blk.mean() >= 0.45
        boxes = []
        used = np.zeros_like(occ)
        for gj in range(gh):
            gi = 0
            while gi < gw:
                if occ[gj, gi] and not used[gj, gi]:
                    gi2 = gi
                    while gi2 + 1 < gw and occ[gj, gi2 + 1] and not used[gj, gi2 + 1]:
                        gi2 += 1
                    gj2 = gj
                    while gj2 + 1 < gh and occ[gj2 + 1, gi : gi2 + 1].all() and not used[gj2 + 1, gi : gi2 + 1].any():
                        gj2 += 1
                    used[gj : gj2 + 1, gi : gi2 + 1] = True
                    x0, z0 = mn[0] + gi * cell, mn[1] + gj * cell
                    w, d = (gi2 - gi + 1) * cell, (gj2 - gj + 1) * cell
                    boxes.append((x0 + w / 2, z0 + d / 2, w, d))
                    gi = gi2 + 1
                else:
                    gi += 1
        if len(boxes) <= BOX_MAX or cell > 0.5:
            break
        cell *= 1.25

    # --- 凸包、丸さ、偏り
    pts = np.array(coords)
    hull = ConvexHull(pts)
    hv = pts[hull.vertices]
    h_area, h_per = hull.volume, hull.area
    circ = 4 * math.pi * h_area / (h_per**2)  # 円 = 1
    fill_ratio = area / h_area  # 凸包をどれだけ埋めているか
    bb_c = (pts.min(0) + pts.max(0)) / 2
    max_r = float(np.max(np.hypot(pts[:, 0], pts[:, 1])))
    offset = float(np.hypot(*bb_c)) / max_r
    balance = max(0.0, min(1.0, 0.75 * circ + 0.25 * fill_ratio - 1.2 * offset))

    # --- 尖った先端：凸包の角の曲がり具合を、近くの角とまとめて測る
    n = len(hv)
    turn = []
    for i in range(n):
        a, b, c = hv[i - 1], hv[i], hv[(i + 1) % n]
        v1, v2 = a - b, c - b
        cosang = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12)
        turn.append(180 - math.degrees(math.acos(max(-1, min(1, cosang)))))
    win = []
    for i in range(n):
        near = [j for j in range(n) if np.linalg.norm(hv[j] - hv[i]) < TIP_WINDOW]
        win.append(sum(turn[j] for j in near))
    order = sorted(range(n), key=lambda i: -win[i])
    tips, taken = [], []
    for i in order:
        sharp = win[i] / 180
        if sharp < TIP_MIN:
            break
        if any(np.linalg.norm(hv[i] - hv[t]) < TIP_WINDOW * 1.4 for t in taken):
            continue
        near = [j for j in range(n) if np.linalg.norm(hv[j] - hv[i]) < TIP_WINDOW]
        wsum = sum(turn[j] for j in near) + 1e-9
        p = sum(hv[j] * turn[j] for j in near) / wsum
        taken.append(i)
        tips.append((float(p[0]), float(p[1]), round(min(1.0, sharp), 3)))

    # --- タイプ（いちばん目立つ特徴）
    max_sharp = max([t[2] for t in tips], default=0)
    # 基準（O 前後）からどれだけ外れているかで、いちばん目立つ特徴を選ぶ
    scores = {
        "POWER": (area - 2.2) / 0.8,
        "SPEED": (1.6 - area) / 0.6,
        "BALANCE": (balance - 0.75) / 0.12,
        "CRITICAL": (max_sharp - 0.5) / 0.15,
    }
    kind = max(scores, key=scores.get)

    R = lambda v: round(float(v), 3)
    return {
        "c": ch,
        "style": style,
        "font": fontpath.rsplit("/", 1)[-1],
        "ok": True,
        "area": R(area),
        "maxR": R(max_r),
        "balance": R(balance),
        "circ": R(circ),
        "offset": R(offset),
        "kind": kind,
        "v": [R(x) for p in coords for x in p],
        "r": ring_out,
        "rl": ring_len,
        "t": [i for tri in tris for i in tri],
        "boxes": [[R(b[0]), R(b[1]), R(b[2]), R(b[3])] for b in boxes],
        "tips": [[R(t[0]), R(t[1]), t[2]] for t in tips],
        "_check": {"triArea": R(tri_area), "polyArea": R(poly_area), "maskArea": R(area)},
    }


if __name__ == "__main__":
    style = sys.argv[1] if len(sys.argv) > 1 else "gothic"
    for ch in sys.argv[2:] or ["A"]:
        g = build_glyph(ch, style)
        print(json.dumps({k: v for k, v in g.items() if k not in ("v", "t", "boxes")}, ensure_ascii=False))
