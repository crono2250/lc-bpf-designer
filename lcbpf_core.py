"""LC バンドパスフィルタ設計の計算部（外部ライブラリ不要、Python 標準ライブラリのみ）。

流れ:
  1. 低域プロトタイプ（正規化 ω=1, R=1）を求める
       - バタワース / チェビシェフ: 閉形式の g 値
       - エリプティック: 楕円関数で極・零点を求め、Darlington 合成（零点移動を伴う梯子展開）
  2. 帯域変換（LPF→BPF）で L・C を算出
  3. E 系列に丸める
  4. ABCD 行列で振幅特性を計算
"""
from __future__ import annotations

import cmath
import math
from decimal import Decimal, localcontext
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# E 系列
# ---------------------------------------------------------------------------
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E12 = E24[::2]
E6 = E12[::2]
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
       1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
       1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
       3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
       4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
       7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76]
assert len(E6) == 6 and len(E12) == 12 and len(E24) == 24 and len(E96) == 96

E_SERIES = {"E6": E6, "E12": E12, "E24": E24, "E96": E96}


def round_e(value: float, series: str) -> float:
    """value を E 系列の最も近い値（対数距離）に丸める。"""
    if value <= 0 or not math.isfinite(value):
        return value
    base = E_SERIES[series]
    exp = math.floor(math.log10(value))
    best, best_d = value, float("inf")
    for e in (exp - 1, exp, exp + 1):
        for m in base:
            cand = m * 10.0 ** e
            d = abs(math.log(cand / value))
            if d < best_d:
                best, best_d = cand, d
    return float(f"{best:.3g}") if series != "E96" else float(f"{best:.3g}")


def step_e(value: float, series: str, steps: int) -> float:
    """value から E 系列の標準数を steps 個（正: 大きい側、負: 小さい側）進めた値。

    value が系列の値でない（任意入力された）場合、最初の1ステップは value より厳密に
    大きい（小さい）直近の標準数になる。
    """
    if value <= 0 or not math.isfinite(value) or steps == 0:
        return value
    base = E_SERIES[series]
    exp = math.floor(math.log10(value))
    cands = sorted(float(f"{m * 10.0 ** e:.3g}") for e in range(exp - 2, exp + 3) for m in base)
    tol = 1e-9
    v = value
    for _ in range(abs(steps)):
        if steps > 0:
            nxt = [c for c in cands if c > v * (1 + tol)]
            if not nxt:
                return v
            v = nxt[0]
        else:
            prv = [c for c in cands if c < v * (1 - tol)]
            if not prv:
                return v
            v = prv[-1]
    return v


_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
       "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_eng(text: str, unit: str) -> float:
    """'330p', '330 pF', '1.2u', '1.2µH', '4.7e-9' などを数値に。unit は 'H' か 'F'。"""
    t = text.strip().replace(",", "").replace(" ", "")
    if t[-1:].upper() == unit.upper():
        t = t[:-1]
    mult = 1.0
    if t and t[-1] in _SI and not t[-1].isdigit():
        # 'm' は単位なし入力ではミリ扱い
        mult = _SI[t[-1]]
        t = t[:-1]
    try:
        v = float(t) * mult
    except ValueError:
        raise ValueError(f"数値として読めません: {text!r}（例: 330p, 1.2u, 4.7e-9）")
    if not (v > 0 and math.isfinite(v)):
        raise ValueError("素子値は正の値を入力してください。")
    return v


# ---------------------------------------------------------------------------
# 低域プロトタイプの表現
#   elements: 入力側から順に
#     ("C", g)            並列（シャント）容量
#     ("L", g)            直列インダクタ
#     ("LCtank", L, C)    直列腕の並列共振器（エリプティックの伝送零点）
#   rl: 負荷側の正規化抵抗（電源側は 1）
# ---------------------------------------------------------------------------
@dataclass
class Prototype:
    elements: list
    rl: float
    note: str = ""
    ideal_s21: object = None  # callable(omega) -> |S21|^2（エリプティック検証用）
    ws: float = 0.0           # 正規化阻止域端（エリプティックのみ）


def butterworth(n: int) -> Prototype:
    g = [2.0 * math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
    return Prototype(_alternate(g), 1.0)


def chebyshev(n: int, ripple_db: float) -> Prototype:
    beta = math.log(1.0 / math.tanh(ripple_db / 17.37))
    gam = math.sinh(beta / (2 * n))
    a = [math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
    b = [gam ** 2 + math.sin(k * math.pi / n) ** 2 for k in range(1, n + 1)]
    g = [2 * a[0] / gam]
    for k in range(2, n + 1):
        g.append(4 * a[k - 2] * a[k - 1] / (b[k - 2] * g[k - 2]))
    if n % 2:
        rl = 1.0
    else:
        rl = math.tanh(beta / 4) ** 2  # シャントC始まりの偶数次
    note = "" if n % 2 else (
        "偶数次チェビシェフは等終端で実現できないため、負荷抵抗を R0×%.4f にしています。" % rl)
    return Prototype(_alternate(g), rl, note)


def _alternate(g):
    """シャントC始まりで C, L, C, L ... と並べる。"""
    return [("C", v) if i % 2 == 0 else ("L", v) for i, v in enumerate(g)]


# ---------------------------------------------------------------------------
# 楕円関数（Orfanidis の Landen 変換による実装）
# ---------------------------------------------------------------------------
def _landen(k: float, M: int = 12):
    v = []
    for _ in range(M):
        kp = math.sqrt(max(0.0, 1 - k * k))
        k = (k / (1 + kp)) ** 2
        v.append(k)
    return v


def _cde(u: complex, k: float) -> complex:
    v = _landen(k)
    w = cmath.cos(u * math.pi / 2)
    for vn in reversed(v):
        w = (1 + vn) * w / (1 + vn * w * w)
    return w


def _sne(u: complex, k: float) -> complex:
    v = _landen(k)
    w = cmath.sin(u * math.pi / 2)
    for vn in reversed(v):
        w = (1 + vn) * w / (1 + vn * w * w)
    return w


def _asne(w: complex, k: float) -> complex:
    v = _landen(k)
    v1 = k
    for vn in v:
        w = w / (1 + cmath.sqrt(1 - w * w * v1 * v1)) * 2 / (1 + vn)
        v1 = vn
    return 2 * cmath.asin(w) / math.pi


def _ellipdeg(n: int, k1: float) -> float:
    """次数 n と判別係数 k1 から選択度 k を求める（次数方程式）。"""
    L = n // 2
    k1p = math.sqrt(1 - k1 * k1)
    # k' = k1'^n Π sn^4 を対数で計算し、k = √(1-k'^2) を expm1 で桁落ちなく求める
    lg = n * 0.5 * math.log1p(-k1 * k1)
    for i in range(1, L + 1):
        ui = (2 * i - 1) / n
        lg += 4 * math.log(_sne(ui, k1p).real)
    return math.sqrt(max(0.0, -math.expm1(2 * lg)))


# ---------------------------------------------------------------------------
# 実係数多項式（係数は昇べき順 c[0] + c[1] s + ...）
# ---------------------------------------------------------------------------
def _pmul(a, b):
    r = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            r[i + j] += x * y
    return r


def _padd(a, b):
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else 0.0) + (b[i] if i < len(b) else 0.0) for i in range(n)]


def _pscale(a, c):
    return [x * c for x in a]


def _ptrim(a, rel=1e-13):
    a = list(a)
    m = max((abs(x) for x in a), default=0.0)
    while len(a) > 1 and abs(a[-1]) <= rel * m:
        a.pop()
    return a


def _peval(a, s):
    r = 0j
    for c in reversed(a):
        r = r * s + c
    return r


def _pdiv_quad(a, w2):
    """a(s) / (s^2 + w2) の商（剰余は捨てる）。高次側から割る。"""
    a = list(a)
    n = len(a) - 1
    q = [0.0] * (n - 1)
    for i in range(n, 1, -1):
        c = a[i]
        q[i - 2] = c
        a[i] -= c
        a[i - 2] -= c * w2
    return q


def _poly_from_roots(roots):
    """複素根（共役対を含む）から実係数多項式を作る（モニック）。"""
    p = [1.0 + 0j]
    for r in roots:
        p = [(p[i - 1] if i > 0 else 0) - r * (p[i] if i < len(p) else 0) for i in range(len(p) + 1)]
    return [c.real for c in p]


# ---------------------------------------------------------------------------
# エリプティック低域プロトタイプ
# ---------------------------------------------------------------------------
def elliptic_roots(n: int, ap_db: float, as_db: float):
    """正規化 LPF（通過域端 ω=1）の極、伝送零点（ω）、反射零点（ω）を返す。"""
    ep = math.sqrt(10 ** (ap_db / 10) - 1)
    es = math.sqrt(10 ** (as_db / 10) - 1)
    k1 = ep / es
    k = _ellipdeg(n, k1)
    L, r = n // 2, n % 2
    u = [(2 * i - 1) / n for i in range(1, L + 1)]
    zeta = [_cde(ui, k).real for ui in u]
    tz = [1.0 / (k * z) for z in zeta]           # 伝送零点 ω
    v0 = -1j * _asne(1j / ep, k1) / n
    poles = []
    for ui in u:
        p = 1j * _cde(ui - 1j * v0, k)
        poles += [p, p.conjugate()]
    if r:
        poles.append((1j * _sne(1j * v0, k)).real + 0j)
    # 反射零点: ω = ζ_i = cd((2i-1)/n K)（奇数次は ω=0 も）
    rz = ([0.0] if r else []) + list(zeta)
    poles = [complex(-abs(p.real), p.imag) for p in poles]
    return poles, tz, rz, ep, k


def elliptic(n: int, ap_db: float, as_db: float) -> Prototype:
    if n == 1:
        p = butterworth(1)  # 1次は全極形と同じ（リップル規定で正規化）
        ep = math.sqrt(10 ** (ap_db / 10) - 1)
        g = 2 * ep  # |S21|^2 = 1/(1+(ω g/2)^2) が ω=1 で 1/(1+ep^2)
        return Prototype([("C", g)], 1.0, "1次エリプティックは伝送零点を持たず、1次チェビシェフと同一です。")
    poles, tz, rz, ep, k = elliptic_roots(n, ap_db, as_db)
    ws_norm = 1.0 / k
    note = ""
    if n % 2 == 0:
        # Type-b 変換: 最高域の伝送零点を無限遠へ移し、梯子で実現可能にする
        wz = max(tz)
        a = 1.0 / (wz * wz - 1.0)

        def mp(x):  # ω^2 → ω'^2（x' = x / (1 + a - a x)）
            return x / (1 + a - a * x)

        newpoles = []
        for p in poles:
            x = -p * p
            xp = x / (1 + a - a * x)
            s = cmath.sqrt(-xp)
            if s.real > 0:
                s = -s
            newpoles.append(s)
        poles = newpoles
        ws_norm = math.sqrt(mp(ws_norm * ws_norm))
        tz = sorted(math.sqrt(mp(w * w)) for w in tz if w != wz)
        rz = [math.sqrt(mp(w * w)) for w in rz]
        note = ("偶数次エリプティックは梯子型で実現するため Type-b 変換（最高域の伝送零点を無限遠へ移動）を"
                "適用し、負荷抵抗が R0 と異なります。")
    # ---- 以降は多倍長（Decimal）で、P と F を厳密値とみなして E を作り直し、梯子展開する ----
    F = [1.0]
    for w in rz:
        F = _pmul(F, [0.0, 1.0] if w == 0 else [w * w, 0.0, 1.0])
    P = [1.0]
    for w in tz:
        P = _pmul(P, [w * w, 0.0, 1.0])
    with localcontext() as ctx:
        ctx.prec = _HP_PREC
        Fd = [Decimal(1)]
        for w in rz:
            wd = Decimal(w)
            Fd = _hmul(Fd, [Decimal(0), Decimal(1)] if w == 0 else [wd * wd, Decimal(0), Decimal(1)])
        Pd = [Decimal(1)]
        for w in tz:
            wd = Decimal(w)
            Pd = _hmul(Pd, [wd * wd, Decimal(0), Decimal(1)])
        # |S21|^2 = 1/(1 + ε^2 R^2)、R = F/P は通過域端 ω=1 で |R| = 1 に正規化
        j1 = (Decimal(0), Decimal(1))
        fp, pp = _hceval(Fd, j1), _hceval(Pd, j1)
        eps = (Decimal(10) ** (Decimal(ap_db) / 10) - 1).sqrt()
        cf = eps * ((pp[0] ** 2 + pp[1] ** 2) / (fp[0] ** 2 + fp[1] ** 2)).sqrt()
        Fd = [c * cf for c in Fd]
        Q = _hadd(_hmul(Pd, _hneg_s(Pd)), _hmul(Fd, _hneg_s(Fd)))
        roots = [_newton_refine(Q, p) for p in poles]
        Ed = _hpoly_from_roots(roots, (abs(Q[-1])).sqrt())
        num = _hadd(Ed, Fd)
        den = _hadd(Ed, [-c for c in Fd])
        if abs(den[-1]) > Decimal("1e-20") * abs(num[-1]):
            raise ValueError("エリプティックの多項式の整合に失敗しました。")
        den = den[:-1]
        cff = float(cf)

        def ideal_s21(w):
            # 因数分解形で評価（展開多項式より桁落ちが少ない）
            fr = cff
            for z in rz:
                fr *= (w if z == 0 else (z * z - w * w))
            for z in tz:
                fr /= (z * z - w * w) if z != w else 1e-300
            return 1.0 / (1.0 + fr * fr)

        best = None
        for order in _zero_orders(tz):
            try:
                els, rl = _synthesize_hp(num, den, [Decimal(tz[i]) for i in order])
            except (ZeroDivisionError, ValueError, ArithmeticError):
                continue
            els = [tuple([e[0]] + [float(v) for v in e[1:]]) for e in els]
            rl = float(rl)
            if not _all_positive(els, rl):
                continue
            err = _proto_error(els, rl, ideal_s21)
            if best is None or err < best[0]:
                best = (err, els, rl)
    if best is None:
        raise ValueError("エリプティックの梯子合成に失敗しました（この次数・仕様では正の素子値で実現できません）。")
    err, els, rl = best
    if err > 0.05:
        raise ValueError("エリプティックの梯子合成の数値誤差が大きすぎます（最大 %.2f dB）。" % err)
    return Prototype(els, rl, note, ideal_s21, ws_norm)


# ---------------------------------------------------------------------------
# 多倍長の補助（Decimal 実係数多項式、複素数は (re, im) の組）
# ---------------------------------------------------------------------------
_HP_PREC = 80


def _hmul(a, b):
    r = [Decimal(0)] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            r[i + j] += x * y
    return r


def _hadd(a, b):
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else Decimal(0)) + (b[i] if i < len(b) else Decimal(0)) for i in range(n)]


def _hneg_s(a):
    """a(-s)"""
    return [c if i % 2 == 0 else -c for i, c in enumerate(a)]


def _cmul(x, y):
    return (x[0] * y[0] - x[1] * y[1], x[0] * y[1] + x[1] * y[0])


def _cdiv(x, y):
    d = y[0] * y[0] + y[1] * y[1]
    return ((x[0] * y[0] + x[1] * y[1]) / d, (x[1] * y[0] - x[0] * y[1]) / d)


def _hceval(a, z):
    r = (Decimal(0), Decimal(0))
    for c in reversed(a):
        r = _cmul(r, z)
        r = (r[0] + c, r[1])
    return r


def _hderiv(a):
    return [a[i] * i for i in range(1, len(a))]


def _newton_refine(Q, p0):
    z = (Decimal(p0.real), Decimal(p0.imag))
    dQ = _hderiv(Q)
    tol = Decimal(10) ** (-(_HP_PREC - 15))
    for _ in range(200):
        step = _cdiv(_hceval(Q, z), _hceval(dQ, z))
        z = (z[0] - step[0], z[1] - step[1])
        if abs(step[0]) + abs(step[1]) < tol * (abs(z[0]) + abs(z[1]) + 1):
            break
    if z[0] > 0:
        z = (-z[0], z[1])
    return z


def _hpoly_from_roots(roots, lead):
    p = [(Decimal(1), Decimal(0))]
    for r in roots:
        q = [(Decimal(0), Decimal(0))] * (len(p) + 1)
        for i, c in enumerate(p):
            q[i + 1] = (q[i + 1][0] + c[0], q[i + 1][1] + c[1])
            m = _cmul(c, r)
            q[i] = (q[i][0] - m[0], q[i][1] - m[1])
        p = q
    return [c[0] * lead for c in p]


def _hpdiv_quad(a, w2):
    a = list(a)
    n = len(a) - 1
    q = [Decimal(0)] * (n - 1)
    for i in range(n, 1, -1):
        c = a[i]
        q[i - 2] = c
        a[i] -= c
        a[i - 2] -= c * w2
    return q


def _synthesize_hp(num, den, zeros):
    """Y = num/den（deg num = deg den + 1、シャントC始まり）から零点移動で梯子を展開する（Decimal）。"""
    els = []
    S = [Decimal(0), Decimal(1)]
    for w in zeros:
        if len(num) != len(den) + 1:
            raise ValueError
        jw = (Decimal(0), w)
        Yw = _cdiv(_hceval(num, jw), _hceval(den, jw))
        C = Yw[1] / w
        if C <= 0:
            raise ValueError
        els.append(("C", C))
        n1 = _hadd(num, [-C * c for c in _hmul(S, den)])
        n1q = _hpdiv_quad(n1, w * w)
        t = _cdiv(_hceval(den, jw), _cmul(jw, _hceval(n1q, jw)))
        twoK = t[0]
        if twoK <= 0:
            raise ValueError
        els.append(("LCtank", twoK / (w * w), 1 / twoK))
        z2n = _hadd(den, [-twoK * c for c in _hmul(S, n1q)])[:len(den)]
        num, den = n1q, _hpdiv_quad(z2n, w * w)
    if len(num) != len(den) + 1:
        raise ValueError
    C = num[-1] / den[-1]
    els.append(("C", C))
    rem = _hadd(num, [-C * c for c in _hmul(S, den)])
    if len(den) == 1:
        rl = den[0] / rem[0]
    elif len(den) == 2:
        els.append(("L", den[1] / rem[0]))
        rl = den[0] / rem[0]
    else:
        raise ValueError
    return els, rl


def _zero_orders(tz):
    """伝送零点の抽出順の候補（外側に高い零点、内側に低い零点を置く並びを優先）。"""
    m = len(tz)
    idx = sorted(range(m), key=lambda i: tz[i])  # 昇順
    cands = []
    # 外側(先頭・末尾)が高い零点、中央が低い零点
    seq = [None] * m
    lo, hi = 0, m - 1
    for j, i in enumerate(reversed(idx)):
        if j % 2 == 0:
            seq[lo] = i; lo += 1
        else:
            seq[hi] = i; hi -= 1
    cands.append(seq)
    cands.append(list(reversed(seq)))
    cands.append(list(reversed(idx)))
    cands.append(list(idx))
    out = []
    for c in cands:
        if c not in out:
            out.append(c)
    return out


def _all_positive(els, rl):
    for e in els:
        for v in e[1:]:
            if not (v > 0 and math.isfinite(v)):
                return False
    return rl > 0 and math.isfinite(rl)


def _proto_error(els, rl, ideal):
    worst = 0.0
    for i in range(1, 400):
        w = 10 ** (-1.5 + 3.0 * i / 400)
        a = _s21_db_proto(els, rl, w)
        b = 10 * math.log10(max(ideal(w), 1e-30))
        if b > -60:  # 阻止域の深い谷は比較しない
            worst = max(worst, abs(a - b))
    return worst


def _s21_db_proto(els, rl, w):
    s = 1j * w
    stages = []
    for e in els:
        if e[0] == "C":
            stages.append(("Y", s * e[1]))
        elif e[0] == "L":
            stages.append(("Z", s * e[1]))
        else:
            y = s * e[2] + 1.0 / (s * e[1])
            stages.append(("Z", 1.0 / y if abs(y) > 1e-300 else 1e300 + 0j))
    return _transducer_db(stages, 1.0, rl)


# ---------------------------------------------------------------------------
# BPF 変換
# ---------------------------------------------------------------------------
@dataclass
class Arm:
    kind: str          # "series_LC"（直列腕・直列共振）/ "shunt_LC"（並列腕・並列共振）/ "series_ell"（直列腕・楕円）
    L: list            # 理想 L [H]
    C: list            # 理想 C [F]
    Le: list = field(default_factory=list)  # E 系列 L
    Ce: list = field(default_factory=list)  # E 系列 C
    La: list = field(default_factory=list)  # 調整後 L（初期値 = E 系列値）
    Ca: list = field(default_factory=list)  # 調整後 C（初期値 = E 系列値）

    def reset_adjust(self):
        self.La = list(self.Le)
        self.Ca = list(self.Ce)


@dataclass
class Design:
    arms: list
    r0: float
    rl: float
    f0: float
    fl: float
    fh: float
    note: str
    proto: Prototype
    fs_lo: float = 0.0
    fs_hi: float = 0.0


def design_bpf(fl, fh, n, kind, r0=50.0, ripple_db=0.5, as_db=40.0, e_l="E12", e_c="E12") -> Design:
    if not (fl > 0 and fh > fl):
        raise ValueError("fL < fH となるように正の周波数を入力してください。")
    if not (1 <= n <= 20):
        raise ValueError("次数は 1～20 です。")
    if r0 <= 0:
        raise ValueError("インピーダンスは正の値を入力してください。")
    if kind == "butterworth":
        proto = butterworth(n)
    elif kind == "chebyshev":
        if ripple_db <= 0:
            raise ValueError("リップルは正の値を入力してください。")
        proto = chebyshev(n, ripple_db)
    elif kind == "elliptic":
        if ripple_db <= 0 or as_db <= ripple_db:
            raise ValueError("リップル > 0、阻止域減衰量 > リップル としてください。")
        proto = elliptic(n, ripple_db, as_db)
    else:
        raise ValueError(kind)
    wl, wh = 2 * math.pi * fl, 2 * math.pi * fh
    w0 = math.sqrt(wl * wh)
    bw = wh - wl
    arms = []
    for e in proto.elements:
        if e[0] == "L":   # 直列 L → 直列共振器
            g = e[1]
            arms.append(Arm("series_LC", [g * r0 / bw], [bw / (w0 * w0 * g * r0)]))
        elif e[0] == "C":  # シャント C → 並列共振器
            g = e[1]
            arms.append(Arm("shunt_LC", [r0 * bw / (w0 * w0 * g)], [g / (r0 * bw)]))
        else:  # 直列腕の並列タンク(Lt ∥ Ct) → (Ls-Cs 直列) ∥ Lp ∥ Cp
            Lt, Ct = e[1], e[2]
            Ls = Lt * r0 / bw
            Cs = bw / (w0 * w0 * Lt * r0)
            Cp = Ct / (r0 * bw)
            Lp = r0 * bw / (w0 * w0 * Ct)
            arms.append(Arm("series_ell", [Ls, Lp], [Cs, Cp]))
    for a in arms:
        a.Le = [round_e(v, e_l) for v in a.L]
        a.Ce = [round_e(v, e_c) for v in a.C]
        a.reset_adjust()
    d = Design(arms, r0, proto.rl * r0, w0 / (2 * math.pi), fl, fh, proto.note, proto)
    if proto.ws > 0:  # 阻止域端（LPF Ω → BPF の上下2周波数）
        f0 = d.f0
        x = proto.ws * (fh - fl) / (2 * f0)
        d.fs_lo = f0 * (math.sqrt(x * x + 1) - x)
        d.fs_hi = f0 * (math.sqrt(x * x + 1) + x)
    return d


# ---------------------------------------------------------------------------
# 周波数特性
# ---------------------------------------------------------------------------
def _transducer_db(stages, rs, rl):
    # ABCD 連鎖
    A, B, C, D = 1 + 0j, 0j, 0j, 1 + 0j
    for t, v in stages:
        if t == "Z":
            A, B, C, D = A, A * v + B, C, C * v + D
        else:
            A, B, C, D = A + B * v, B, C + D * v, D
    denom = A * rl + B + C * rs * rl + D * rs
    s21 = 2 * math.sqrt(rs * rl) / denom
    return 20 * math.log10(max(abs(s21), 1e-30))


def arm_stages(arms, f, use_e=False, use_adj=False):
    """use_adj=True: 調整後の値、use_e=True: E 系列値（設計値）、どちらも False: 理想値。"""
    w = 2 * math.pi * f
    s = 1j * w
    st = []
    for a in arms:
        if use_adj:
            L, C = a.La, a.Ca
        else:
            L = a.Le if use_e else a.L
            C = a.Ce if use_e else a.C
        if a.kind == "series_LC":
            st.append(("Z", s * L[0] + 1 / (s * C[0])))
        elif a.kind == "shunt_LC":
            st.append(("Y", s * C[0] + 1 / (s * L[0])))
        else:
            zs = s * L[0] + 1 / (s * C[0])
            if abs(zs) < 1e-300:  # 直列枝がちょうど共振（f0）→ 腕は短絡
                st.append(("Z", 0j))
                continue
            y = 1 / zs + 1 / (s * L[1]) + s * C[1]
            st.append(("Z", 1 / y if abs(y) > 1e-300 else 1e300 + 0j))
    return st


def response(design: Design, freqs, use_e=False, use_adj=False):
    rl_used = design.rl
    return [_transducer_db(arm_stages(design.arms, f, use_e, use_adj), design.r0, rl_used) for f in freqs]


def reset_adjust(design: Design):
    """全素子の調整値を設計値（E 系列値）に戻す。"""
    for a in design.arms:
        a.reset_adjust()


def is_adjusted(design: Design) -> bool:
    return any(a.La != a.Le or a.Ca != a.Ce for a in design.arms)


def log_freqs(fmin, fmax, npts=801):
    a, b = math.log10(fmin), math.log10(fmax)
    return [10 ** (a + (b - a) * i / (npts - 1)) for i in range(npts)]


def fmt_eng(v, unit):
    if v == 0 or not math.isfinite(v):
        return f"{v} {unit}"
    prefixes = [(1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                (1, ""), (1e3, "k"), (1e6, "M"), (1e9, "G")]
    for scale, p in reversed(prefixes):
        if abs(v) >= scale * 0.9999:
            return f"{v / scale:.4g} {p}{unit}"
    return f"{v / 1e-15:.4g} f{unit}"
