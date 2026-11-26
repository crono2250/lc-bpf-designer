"""LC ladder band-pass filter design core (pure Python, no external packages).

Flow:
  1. low-pass prototype (normalized, Rs = 1 ohm, passband edge 1 rad/s)
       - Butterworth / Chebyshev: closed-form g values
       - Elliptic (Cauer): characteristic function from Jacobi elliptic functions
         (theta-function evaluation), then Darlington ladder synthesis
         (partial pole removal at the finite transmission zeros).
         Even orders use the "type c" modification so that both
         terminations are equal.
  2. optional dual (series-first / shunt-first)
  3. low-pass -> band-pass transform (L -> series LC, C -> parallel LC)
  4. E-series rounding and ABCD simulation.
"""

import cmath
import math

# --------------------------------------------------------------------------
# E series
# --------------------------------------------------------------------------
E6 = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E96 = [v / 100.0 for v in (
    100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137,
    140, 143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191,
    196, 200, 205, 210, 215, 221, 226, 232, 237, 243, 249, 255, 261, 267,
    274, 280, 287, 294, 301, 309, 316, 324, 332, 340, 348, 357, 365, 374,
    383, 392, 402, 412, 422, 432, 442, 453, 464, 475, 487, 499, 511, 523,
    536, 549, 562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732,
    750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976)]
E_SERIES = {"E6": E6, "E12": E12, "E24": E24, "E96": E96}


def round_to_series(value, series):
    """Nearest E-series value (nearest in the logarithmic sense)."""
    if not (value > 0) or math.isinf(value):
        return value
    table = E_SERIES[series]
    d = math.floor(math.log10(value))
    m = value / 10.0 ** d
    if m < 1.0:
        d -= 1
        m *= 10.0
    elif m >= 10.0:
        d += 1
        m /= 10.0
    best = min(table + [10.0], key=lambda t: abs(math.log(t / m)))
    return float("%.4g" % (best * 10.0 ** d))


def step_series(value, series, steps):
    """Move `steps` standard values up (+) or down (-) the E series.
    A value off the series moves first to the nearest standard value
    strictly above (below) it."""
    if not (value > 0) or math.isinf(value) or steps == 0:
        return value
    table = E_SERIES[series]
    d = math.floor(math.log10(value))
    cands = sorted(float("%.4g" % (m * 10.0 ** e)) for e in range(d - 2, d + 3) for m in table)
    v = value
    for _ in range(abs(steps)):
        if steps > 0:
            nxt = [c for c in cands if c > v * (1 + 1e-9)]
            if not nxt:
                return v
            v = nxt[0]
        else:
            prv = [c for c in cands if c < v * (1 - 1e-9)]
            if not prv:
                return v
            v = prv[-1]
    return v


_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
       "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_eng(text, unit):
    """'330p', '330 pF', '1.2u', '1.2µH', '4.7e-9' -> float. unit: 'H' or 'F'."""
    t = text.strip().replace(",", "").replace(" ", "")
    if t[-1:].upper() == unit.upper():
        t = t[:-1]
    mult = 1.0
    if t and t[-1] in _SI:
        mult = _SI[t[-1]]
        t = t[:-1]
    try:
        v = float(t) * mult
    except ValueError:
        raise ValueError("数値として読めません: '%s'（例: 330p, 1.2u, 4.7e-9）" % text)
    if not (v > 0) or math.isinf(v):
        raise ValueError("素子値は正の値を入力してください")
    return v


# --------------------------------------------------------------------------
# polynomial helpers (ascending coefficient lists)
# --------------------------------------------------------------------------
def pmul(a, b):
    r = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            r[i + j] += x * y
    return r


def padd(a, b):
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else 0.0) + (b[i] if i < len(b) else 0.0)
            for i in range(n)]


def pscale(a, k):
    return [x * k for x in a]


def pshift(a):
    """Multiply by s."""
    return [0.0] + list(a)


def peval(a, x):
    r = 0.0
    for c in reversed(a):
        r = r * x + c
    return r


def pdiv_quad(a, w2):
    """Divide a(s) by (s^2 + w2); returns quotient (remainder dropped)."""
    a = list(a)
    n = len(a) - 1
    if n < 2:
        return [0.0]
    q = [0.0] * (n - 1)
    for k in range(n, 1, -1):
        c = a[k]
        q[k - 2] = c
        a[k] -= c
        a[k - 2] -= c * w2
    return q


def poly_roots(coef):
    """All roots of a real/complex polynomial (ascending coefficients),
    Aberth-Ehrlich iteration followed by Newton polishing."""
    c = [complex(x) for x in coef]
    while len(c) > 1 and c[-1] == 0:
        c.pop()
    n = len(c) - 1
    if n < 1:
        return []
    lead = c[-1]
    c = [x / lead for x in c]
    dc = [i * c[i] for i in range(1, n + 1)]
    radius = 1.0 + max(abs(x) for x in c[:-1])
    # geometric-mean radius gives better starting points for wide spreads
    g = abs(c[0]) ** (1.0 / n) if c[0] != 0 else 1.0
    r0 = min(radius, max(g, 1e-12))
    z = [r0 * cmath.exp(1j * (2 * math.pi * k / n + 0.4)) for k in range(n)]
    for _ in range(500):
        maxstep = 0.0
        for i in range(n):
            p = peval(c, z[i])
            dp = peval(dc, z[i])
            if dp == 0:
                dp = 1e-30
            ratio = p / dp
            s = 0.0
            for j in range(n):
                if j != i:
                    d = z[i] - z[j]
                    if d != 0:
                        s += 1.0 / d
            denom = 1.0 - ratio * s
            off = ratio / denom if denom != 0 else ratio
            z[i] -= off
            maxstep = max(maxstep, abs(off) / max(abs(z[i]), 1e-300))
        if maxstep < 1e-15:
            break
    # Newton polishing
    for i in range(n):
        for _ in range(5):
            dp = peval(dc, z[i])
            if dp == 0:
                break
            z[i] -= peval(c, z[i]) / dp
    return z


# --------------------------------------------------------------------------
# low-pass prototypes
# --------------------------------------------------------------------------
def butterworth_g(n):
    g = [2.0 * math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
    return g, 1.0


def chebyshev_g(n, ripple_db):
    beta = math.log(1.0 / math.tanh(ripple_db / 17.37))
    gamma = math.sinh(beta / (2 * n))
    a = [math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
    b = [gamma ** 2 + math.sin(k * math.pi / n) ** 2 for k in range(1, n + 1)]
    g = [2 * a[0] / gamma]
    for k in range(1, n):
        g.append(4 * a[k - 1] * a[k] / (b[k - 1] * g[k - 1]))
    gl = 1.0 if n % 2 == 1 else 1.0 / math.tanh(beta / 4) ** 2
    return g, gl


def _agm(a, b):
    for _ in range(200):
        a, b = (a + b) / 2.0, math.sqrt(a * b)
        if abs(a - b) <= 1e-16 * a:
            break
    return a


class _Theta(object):
    def __init__(self, q):
        self.q = q
        t2 = 0.0
        m = 0
        while True:
            term = q ** ((m + 0.5) ** 2)
            t2 += term
            if term < 1e-20 or m > 500:
                break
            m += 1
        self.t2 = 2.0 * t2
        t3 = t4 = 1.0
        m = 1
        while True:
            term = q ** (m * m)
            t3 += 2 * term
            t4 += 2 * term * (-1) ** m
            if term < 1e-20 or m > 500:
                break
            m += 1
        self.t3, self.t4 = t3, t4

    def sn(self, z):
        """sn(u, k) with z = pi*u / (2K)."""
        q = self.q
        th1 = 0.0
        m = 0
        while True:
            term = q ** ((m + 0.5) ** 2)
            th1 += (-1) ** m * term * math.sin((2 * m + 1) * z)
            if term < 1e-20 or m > 500:
                break
            m += 1
        th1 *= 2
        th4 = 1.0
        m = 1
        while True:
            term = q ** (m * m)
            th4 += 2 * (-1) ** m * term * math.cos(2 * m * z)
            if term < 1e-20 or m > 500:
                break
            m += 1
        return (self.t3 / self.t2) * th1 / th4


def elliptic_function(n, ap_db, as_db):
    """Characteristic function K(s) = F(s)/P(s) of an elliptic low-pass.

    F(s) = C * s^odd * prod(s^2 + zx_i),  P(s) = prod(s^2 + px_j)
    (zx: squared reflection-zero frequencies, px: squared transmission zeros).
    Even orders: type c (lowest reflection zero moved to DC, highest
    transmission zero moved to infinity) -> equal terminations.
    """
    eps2 = 10 ** (ap_db / 10.0) - 1
    eps = math.sqrt(eps2)
    k1 = math.sqrt(eps2 / (10 ** (as_db / 10.0) - 1))
    k1p = math.sqrt(1 - k1 * k1)
    q1 = math.exp(-math.pi * _agm(1.0, k1p) / _agm(1.0, k1))
    q = q1 ** (1.0 / n)
    th = _Theta(q)
    k = (th.t2 / th.t3) ** 2
    odd = n % 2
    if odd:
        zs = [i * math.pi / n for i in range(1, (n - 1) // 2 + 1)]
    else:
        zs = [(2 * i - 1) * math.pi / (2 * n) for i in range(1, n // 2 + 1)]
    oz = [th.sn(z) for z in zs]
    op = [1.0 / (k * w) for w in oz]
    zx = [w * w for w in oz]
    px = [w * w for w in op]
    ws2 = 1.0 / (k * k)        # squared stopband edge
    if not odd:
        r = min(zx)
        p = max(px)
        cc = (1 - r) / (p - 1)

        def mp(x):
            return (x - r) / (cc * (p - x))
        zx = [0.0] + [mp(x) for x in zx if x != r]
        px = [mp(x) for x in px if x != p]
        ws2 = mp(ws2)
    # scale so that |K(j1)| = eps
    fmag = 1.0
    for x in zx:
        fmag *= abs(x - 1.0)
    pmag = 1.0
    for x in px:
        pmag *= abs(x - 1.0)
    C = eps * pmag / fmag
    return {"zx": zx, "px": px, "odd": odd, "C": C, "eps": eps,
            "ws": math.sqrt(ws2), "k": k}


def _char_polys(ef):
    F = [ef["C"]]
    for x in ef["zx"]:
        F = pmul(F, [x, 0.0, 1.0])
    if ef["odd"]:
        F = pshift(F)
    P = [1.0]
    for x in ef["px"]:
        P = pmul(P, [x, 0.0, 1.0])
    return F, P


def elliptic_s21_db(ef, w):
    """Target |S21| (dB) of the normalized elliptic prototype at w rad/s."""
    # product form: expanded polynomials lose all precision at high order
    w2 = w * w
    f = ef["C"] * (abs(w) if ef["odd"] else 1.0)
    for x in ef["zx"]:
        f *= abs(x - w2)
    p = 1.0
    for x in ef["px"]:
        p *= abs(x - w2)
    return 10 * math.log10(p * p / (p * p + f * f)) if (p > 0 or f > 0) else 0.0


class SynthesisError(Exception):
    pass


# ---- high-precision helpers for the elliptic synthesis (decimal) ----------
_PREC = 80


def _dz(a):
    return a[0] * 0


def _dmul(a, b):
    z = _dz(a)
    r = [z] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            r[i + j] += x * y
    return r


def _dadd(a, b):
    z = _dz(a if a else b)
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else z) + (b[i] if i < len(b) else z) for i in range(n)]


def _dscale(a, k):
    return [x * k for x in a]


def _dshift(a):
    return [_dz(a)] + list(a)


def _dquad(a, w2):
    a = list(a)
    n = len(a) - 1
    if n < 2:
        return [_dz(a)]
    q = [_dz(a)] * (n - 1)
    for k in range(n, 1, -1):
        c = a[k]
        q[k - 2] = c
        a[k] -= c
        a[k - 2] -= c * w2
    return q


def _deval_j(p, w):
    """p(j*w) for real-coefficient p -> (re, im)."""
    re = _dz(p)
    im = _dz(p)
    wk = w ** 0
    for k, c in enumerate(p):
        t = c * wk
        m = k % 4
        if m == 0:
            re += t
        elif m == 1:
            im += t
        elif m == 2:
            re -= t
        else:
            im -= t
        wk *= w
    return re, im


def _cdiv(a, b):
    d = b[0] * b[0] + b[1] * b[1]
    return ((a[0] * b[0] + a[1] * b[1]) / d, (a[1] * b[0] - a[0] * b[1]) / d)


def _cmulD(a, b):
    return (a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0])


def _csqrtD(x):
    re, im = x
    r = (re * re + im * im).sqrt()
    zero = re * 0
    # rounding can leave (r - |re|) slightly negative
    a = max((r + re) / 2, zero).sqrt()
    b = max((r - re) / 2, zero).sqrt()
    if im < 0:
        b = -b
    return (a, b)


def _droots(coefD, approx):
    """Polish complex roots of a Decimal polynomial (Aberth, decimal complex)."""
    D = type(coefD[0])
    n = len(coefD) - 1
    lead = coefD[-1]
    c = [x / lead for x in coefD]
    dc = [c[i] * i for i in range(1, n + 1)]
    z = [(D(repr(r.real)), D(repr(r.imag))) for r in approx]
    zero = D(0)
    tol = D(10) ** (-(_PREC - 10))

    def ev(p, x):
        re, im = zero, zero
        for co in reversed(p):
            re, im = re * x[0] - im * x[1] + co, re * x[1] + im * x[0]
        return (re, im)

    for _ in range(200):
        worst = zero
        for i in range(n):
            p = ev(c, z[i])
            dp = ev(dc, z[i])
            if dp[0] == 0 and dp[1] == 0:
                continue
            ratio = _cdiv(p, dp)
            sr, si = zero, zero
            for j in range(n):
                if j != i:
                    d = (z[i][0] - z[j][0], z[i][1] - z[j][1])
                    if d[0] != 0 or d[1] != 0:
                        inv = _cdiv((D(1), zero), d)
                        sr += inv[0]
                        si += inv[1]
            rs = _cmulD(ratio, (sr, si))
            off = _cdiv(ratio, (1 - rs[0], -rs[1]))
            z[i] = (z[i][0] - off[0], z[i][1] - off[1])
            mag = (off[0] * off[0] + off[1] * off[1]).sqrt()
            zm = (z[i][0] * z[i][0] + z[i][1] * z[i][1]).sqrt()
            rel = mag / zm if zm != 0 else mag
            if rel > worst:
                worst = rel
        if worst < tol:
            break
    return z


def elliptic_ladder(n, ap_db, as_db):
    """Normalized elliptic ladder: list of (position, tree), load resistance.

    Synthesis runs in 80-digit decimal arithmetic: high-order elliptic
    filters have transmission zeros crowded against the passband edge and
    the ladder extraction is badly conditioned in double precision.
    """
    import decimal
    from decimal import Decimal as D
    ef = elliptic_function(n, ap_db, as_db)
    ctx = decimal.localcontext()
    with ctx as cx:
        cx.prec = _PREC
        Cd = D(ef["C"])
        zxd = [D(x) for x in ef["zx"]]
        pxd = [D(x) for x in ef["px"]]
        one = D(1)
        # F(s), E(s)E(-s) in x = s^2
        F = [Cd]
        for x in zxd:
            F = _dmul(F, [x, D(0), one])
        if ef["odd"]:
            F = _dshift(F)
        Gx = [Cd]
        for x in zxd:
            Gx = _dmul(Gx, [x, one])
        Px = [one]
        for x in pxd:
            Px = _dmul(Px, [x, one])
        if ef["odd"]:
            EEx = _dadd(_dmul(Px, Px), _dscale(_dshift(_dmul(Gx, Gx)), D(-1)))
        else:
            EEx = _dadd(_dmul(Px, Px), _dmul(Gx, Gx))
        approx = poly_roots([float(v) for v in EEx])
        xr = _droots(EEx, approx)
        E = [(Cd, D(0))]
        for x in xr:
            sr = _csqrtD(x)
            sr = (-sr[0], -sr[1]) if sr[0] > 0 else sr
            # multiply by (s - sr)
            newE = [(D(0), D(0))] * (len(E) + 1)
            for i, co in enumerate(E):
                newE[i + 1] = (newE[i + 1][0] + co[0], newE[i + 1][1] + co[1])
                t = _cmulD(co, sr)
                newE[i] = (newE[i][0] - t[0], newE[i][1] - t[1])
            E = newE
        E = [co[0] for co in E]
        E[-1] = F[-1]
        num = _dadd(E, F)
        den = _dadd(E, _dscale(F, D(-1)))[:-1]
        zeros = sorted(x.sqrt() for x in pxd)
        small = D(10) ** (-(_PREC // 3))

        def trim_lead(p, ref):
            # drop a leading coefficient that must vanish structurally
            mx = max(abs(v) for v in ref)
            if abs(p[-1]) <= small * mx:
                return p[:-1]
            return None

        def tail(num, den, branches):
            mode = "Y"
            for _ in range(4 * n + 8):
                if len(num) == 1 and len(den) == 1:
                    val = num[0] / den[0]
                    rl = one / val if mode == "Y" else val
                    if rl <= 0:
                        return None
                    return branches, rl
                if len(num) == len(den) + 1:
                    v = num[-1] / den[-1]
                    if v <= 0:
                        return None
                    branches = branches + [("shunt", ("C", v)) if mode == "Y"
                                           else ("series", ("L", v))]
                    rem = _dadd(num, _dscale(_dshift(den), -v))[:-1]
                    while len(rem) >= len(den) and len(rem) > 1:
                        t = trim_lead(rem, num)
                        if t is None:
                            return None
                        rem = t
                    num, den = den, rem
                    mode = "Z" if mode == "Y" else "Y"
                elif len(num) < len(den):
                    num, den = den, num
                    mode = "Z" if mode == "Y" else "Y"
                else:
                    return None
            return None

        budget = [20000]

        def dfs(num, den, left, branches):
            budget[0] -= 1
            if budget[0] < 0:
                return None
            if not left:
                return tail(num, den, branches)
            cinf = num[-1] / den[-1] if len(num) == len(den) + 1 else D(0)
            for idx, W in enumerate(left):
                w2 = W * W
                y = _cdiv(_deval_j(num, W), _deval_j(den, W))
                c = y[1] / W                     # Re(Y/(jW))
                if not (c > 0 and c <= cinf * (one + small)):
                    continue
                n1 = _dadd(num, _dscale(_dshift(den), -c))
                q = _dquad(n1, w2)
                qv = _deval_j(q, W)
                if qv[0] == 0 and qv[1] == 0:
                    continue
                dv = _deval_j(den, W)
                ratio = _cdiv(dv, qv)            # den/Q at jW
                a = ratio[1] / W                 # Re(ratio/(jW))
                if not a > 0:
                    continue
                r = _dquad(_dadd(den, _dscale(_dshift(q), -a)), w2)
                nb = branches + [("shunt", ("C", c)),
                                 ("series", ("par", [("L", a / w2), ("C", one / a)]))]
                res = dfs(q, r, left[:idx] + left[idx + 1:], nb)
                if res:
                    return res
            return None

        res = dfs(num, den, zeros, [])
    if res is None:
        raise SynthesisError(
            "エリプティックの梯子回路合成に失敗しました（%d 次、リップル %g dB、減衰量 %g dB）。\n"
            "この条件では遷移帯域が実質ゼロです（正規化阻止域端 Ωs − 1 = %.1e）。\n"
            "次数を下げるか、阻止域減衰量を大きくしてください。" % (n, ap_db, as_db, ef["ws"] - 1))
    branches, rl = res

    def tofloat(t):
        if t[0] in ("L", "C"):
            return (t[0], float(t[1]))
        return (t[0], [tofloat(ch) for ch in t[1]])
    branches = [(p, tofloat(t)) for p, t in branches]
    return branches, float(rl), ef


# --------------------------------------------------------------------------
# networks
# --------------------------------------------------------------------------
def tree_z(node, s):
    kind = node[0]
    if kind == "L":
        return s * node[1]
    if kind == "C":
        return 1.0 / (s * node[1])
    if kind == "ser":
        return sum(tree_z(ch, s) for ch in node[1])
    if kind == "par":
        y = 0.0
        for ch in node[1]:
            z = tree_z(ch, s)
            if z == 0:
                return 0.0
            y += 1.0 / z
        return 1.0 / y if y != 0 else 1e300
    if kind == "el":  # element dict wrapper used after denormalization
        return tree_z((node[1]["kind"], node[1]["_v"]), s)
    raise ValueError(kind)


def ladder_s21(branches, rs, rl, s):
    A, B, C, D = 1.0, 0.0, 0.0, 1.0
    for pos, tree in branches:
        z = tree_z(tree, s)
        if pos == "series":
            A, B, C, D = A, A * z + B, C, C * z + D
        else:
            y = 1.0 / z if z != 0 else 1e300
            A, B, C, D = A + B * y, B, C + D * y, D
    return 2.0 * math.sqrt(rs * rl) / (A * rl + B + C * rs * rl + D * rs)


def _dual_tree(t):
    k = t[0]
    if k == "L":
        return ("C", t[1])
    if k == "C":
        return ("L", t[1])
    return ("par" if k == "ser" else "ser", [_dual_tree(c) for c in t[1]])


def dual_ladder(branches, rl):
    out = [("series" if p == "shunt" else "shunt", _dual_tree(t)) for p, t in branches]
    return out, 1.0 / rl


def _flatten(t):
    if t[0] in ("ser", "par"):
        kids = []
        for c in t[1]:
            c = _flatten(c)
            if c[0] == t[0]:
                kids.extend(c[1])
            else:
                kids.append(c)
        return (t[0], kids)
    return t


def lp_to_bp(tree, r0, w0, bw):
    k = tree[0]
    if k == "L":
        v = tree[1]
        return ("ser", [("L", v * r0 / bw), ("C", bw / (v * r0 * w0 * w0))])
    if k == "C":
        v = tree[1]
        return ("par", [("C", v / (r0 * bw)), ("L", r0 * bw / (v * w0 * w0))])
    return (k, [lp_to_bp(c, r0, w0, bw) for c in tree[1]])


# --------------------------------------------------------------------------
# top-level design
# --------------------------------------------------------------------------
TYPES = ("バタワース", "チェビシェフ", "エリプティック")


class Design(object):
    pass


def design_bpf(fl, fh, order, ftype, r0=50.0, ripple_db=0.5, as_db=40.0,
               l_series="E12", c_series="E12", first="shunt"):
    if not (fl > 0 and fh > fl):
        raise ValueError("fL < fH の正の周波数を指定してください")
    if not (1 <= order <= 20):
        raise ValueError("次数は 1～20 です")
    if r0 <= 0:
        raise ValueError("インピーダンスは正の値にしてください")
    info = {}
    if ftype == "バタワース":
        g, gl = butterworth_g(order)
        lp, rl = _from_g(g, gl)
        info["g"] = g + [gl]
    elif ftype == "チェビシェフ":
        if ripple_db <= 0:
            raise ValueError("リップルは正の値にしてください")
        g, gl = chebyshev_g(order, ripple_db)
        lp, rl = _from_g(g, gl)
        info["g"] = g + [gl]
    elif ftype == "エリプティック":
        if ripple_db <= 0 or as_db <= ripple_db:
            raise ValueError("リップル > 0、阻止域減衰量 > リップル にしてください")
        lp, rl, ef = elliptic_ladder(order, ripple_db, as_db)
        info["ef"] = ef
        info["ws"] = ef["ws"]
        # synthesis check against the target function
        err = 0.0
        for i in range(400):
            w = 10 ** (-1.5 + 2.5 * i / 399.0)
            got = 20 * math.log10(max(abs(ladder_s21(lp, 1.0, rl, 1j * w)), 1e-300))
            tgt = elliptic_s21_db(ef, w)
            if tgt > -60:
                err = max(err, abs(got - tgt))
        info["synth_err_db"] = err
    else:
        raise ValueError("unknown type")
    if first == "series":
        lp, rl = dual_ladder(lp, rl)
    info["lp"] = lp
    w0 = 2 * math.pi * math.sqrt(fl * fh)
    bw = 2 * math.pi * (fh - fl)
    branches = []
    counters = {"L": 0, "C": 0}
    elements = []

    def wrap(t):
        if t[0] in ("L", "C"):
            counters[t[0]] += 1
            ser = l_series if t[0] == "L" else c_series
            rv = round_to_series(t[1], ser)
            el = {"kind": t[0], "name": "%s%d" % (t[0], counters[t[0]]),
                  "ideal": t[1], "rounded": rv, "adjusted": rv, "series": ser,
                  "_v": t[1]}
            elements.append(el)
            return ("el", el)
        return (t[0], [wrap(c) for c in t[1]])

    for pos, t in lp:
        bp = _flatten(lp_to_bp(t, r0, w0, bw))
        wt = wrap(bp)
        branches.append((pos, wt))
        for el in elements:
            if "pos" not in el:
                el["pos"] = "直列腕" if pos == "series" else "並列腕"
    d = Design()
    d.fl, d.fh, d.order, d.ftype = fl, fh, order, ftype
    d.r0, d.rs, d.rl = r0, r0, rl * r0
    d.f0 = math.sqrt(fl * fh)
    d.ripple_db, d.as_db = ripple_db, as_db
    d.l_series, d.c_series = l_series, c_series
    d.branches, d.elements, d.info = branches, elements, info
    if "ws" in info:
        a = info["ws"] * (fh - fl) / (2 * d.f0)
        fu = d.f0 * (a + math.sqrt(a * a + 1))
        d.stop_edges = (d.f0 * d.f0 / fu, fu)
    else:
        d.stop_edges = None
    return d


def _from_g(g, gl):
    n = len(g)
    lp = []
    for i, v in enumerate(g):
        lp.append(("shunt", ("C", v)) if i % 2 == 0 else ("series", ("L", v)))
    # g_{n+1}: load resistance after a shunt C, load conductance after a series L
    rl = gl if n % 2 == 1 else 1.0 / gl
    return lp, rl


def reset_adjust(design):
    for el in design.elements:
        el["adjusted"] = el["rounded"]


def is_adjusted(design):
    return any(el["adjusted"] != el["rounded"] for el in design.elements)


def response_db(design, freqs, rounded=False):
    """rounded: False -> ideal values, True -> E-series (design) values,
    'adjusted' -> the user-adjusted values."""
    key = "adjusted" if rounded == "adjusted" else ("rounded" if rounded else "ideal")
    for el in design.elements:
        el["_v"] = el[key]
    out = []
    for f in freqs:
        s = 2j * math.pi * f
        try:
            h = ladder_s21(design.branches, design.rs, design.rl, s)
            out.append(20 * math.log10(max(abs(h), 1e-15)))
        except (ZeroDivisionError, OverflowError):
            out.append(-300.0)
    for el in design.elements:
        el["_v"] = el["ideal"]
    return out


def fmt_eng(v, unit, digits=4):
    if v == 0 or math.isinf(v) or math.isnan(v):
        return "%g %s" % (v, unit)
    prefixes = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"),
                (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]
    a = abs(v)
    for mul, p in prefixes:
        if a >= mul * 0.9995:
            break
    x = v / mul
    s = ("%." + str(digits) + "g") % x
    return "%s %s%s" % (s, p, unit)
