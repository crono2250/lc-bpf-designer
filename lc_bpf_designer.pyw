#!/usr/bin/env python3
"""LC バンドパスフィルタ設計ツール (tkinter GUI)."""

import csv
import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpf_core as bc  # noqa: E402

UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
FONT = ("Meiryo UI", 9)
FONT_B = ("Meiryo UI", 9, "bold")
FONT_S = ("Meiryo UI", 8)
COL_IDEAL = "#1f5fbf"
COL_ROUND = "#d0471c"
COL_ADJ = "#13873a"
COL_WIRE = "#222222"


# ==========================================================================
# circuit drawing
# ==========================================================================
H_LEAF = (104, 80, 66)   # w, h, terminal y-offset (horizontal element)
V_LEAF = (126, 84, 14)   # w, h, terminal x-offset (vertical element)
PAD = 14
GAP = 8


def _size(node, o):
    k = node[0]
    if k == "el":
        return H_LEAF if o == "h" else V_LEAF
    kids = [_size(c, o) for c in node[1]]
    if (k == "ser" and o == "h") or (k == "par" and o == "v"):
        # laid out along x
        if k == "ser":
            w = sum(c[0] for c in kids)
            t = max(c[2] for c in kids)
            h = t + max(c[1] - c[2] for c in kids)
            return (w, h, t)
        # par, vertical orientation: side by side, buses top & bottom
        w = sum(c[0] for c in kids) + GAP * (len(kids) - 1)
        h = max(c[1] for c in kids) + 2 * PAD
        return (w, h, kids[0][2])
    # laid out along y
    if k == "ser":  # ser, vertical
        h = sum(c[1] for c in kids)
        t = max(c[2] for c in kids)
        w = t + max(c[0] - c[2] for c in kids)
        return (w, h, t)
    # par, horizontal: stacked, buses left & right
    w = max(c[0] for c in kids) + 2 * PAD
    h = sum(c[1] for c in kids) + GAP * (len(kids) - 1)
    return (w, h, kids[0][2])


class CircuitView(object):
    def __init__(self, canvas, on_wheel=None, on_click=None):
        self.cv = canvas
        self.show_rounded = True
        # element labels: wheel steps the value, click selects it in the adjust panel
        # (canvas items cannot take <MouseWheel>; the canvas-wide handler calls handle_wheel)
        canvas.tag_bind("ellabel", "<Button-1>", self._click)
        canvas.tag_bind("ellabel", "<Enter>", lambda e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("ellabel", "<Leave>", lambda e: canvas.configure(cursor=""))
        self.on_wheel = on_wheel
        self.on_click = on_click

    def _name_at(self):
        for tag in self.cv.gettags("current"):
            if tag.startswith("el:"):
                return tag[3:]
        return None

    def handle_wheel(self, ev):
        """Wheel over an element label steps its value; returns True if consumed."""
        name = self._name_at()
        if name and self.on_wheel:
            self.on_wheel(name, 1 if ev.delta > 0 else -1)
            return True
        return False

    def _click(self, ev):
        name = self._name_at()
        if name and self.on_click:
            self.on_click(name)

    def line(self, *pts, **kw):
        kw.setdefault("fill", COL_WIRE)
        kw.setdefault("width", 1.5)
        self.cv.create_line(*pts, **kw)

    # ---- symbols ---------------------------------------------------------
    def sym_inductor(self, x1, y1, x2, y2):
        if y1 == y2:
            n = 4
            step = (x2 - x1) / float(n)
            r = step / 2
            for i in range(n):
                xa = x1 + i * step
                self.cv.create_arc(xa, y1 - r, xa + step, y1 + r, start=0, extent=180,
                                   style="arc", outline=COL_WIRE, width=1.5)
        else:
            n = 4
            step = (y2 - y1) / float(n)
            r = step / 2
            for i in range(n):
                ya = y1 + i * step
                self.cv.create_arc(x1 - r, ya, x1 + r, ya + step, start=-90, extent=180,
                                   style="arc", outline=COL_WIRE, width=1.5)

    def sym_capacitor(self, x1, y1, x2, y2):
        if y1 == y2:
            xm = (x1 + x2) / 2.0
            self.line(x1, y1, xm - 4, y1)
            self.line(xm + 4, y1, x2, y1)
            self.line(xm - 4, y1 - 11, xm - 4, y1 + 11, width=2.5)
            self.line(xm + 4, y1 - 11, xm + 4, y1 + 11, width=2.5)
        else:
            ym = (y1 + y2) / 2.0
            self.line(x1, y1, x1, ym - 4)
            self.line(x1, ym + 4, x1, y2)
            self.line(x1 - 11, ym - 4, x1 + 11, ym - 4, width=2.5)
            self.line(x1 - 11, ym + 4, x1 + 11, ym + 4, width=2.5)

    def sym_resistor(self, x1, y1, x2, y2):
        pts = []
        if y1 == y2:
            n = 6
            step = (x2 - x1) / float(n)
            pts = [x1, y1]
            for i in range(n):
                pts += [x1 + (i + 0.5) * step, y1 + (-7 if i % 2 == 0 else 7)]
            pts += [x2, y1]
        else:
            n = 6
            step = (y2 - y1) / float(n)
            pts = [x1, y1]
            for i in range(n):
                pts += [x1 + (-7 if i % 2 == 0 else 7), y1 + (i + 0.5) * step]
            pts += [x1, y2]
        self.line(*pts)

    def labels(self, el, x, y, anchor):
        u = "H" if el["kind"] == "L" else "F"
        ser = self.design.l_series if el["kind"] == "L" else self.design.c_series
        tags = ("ellabel", "el:" + el["name"])
        self.cv.create_text(x, y, text=el["name"], font=FONT_B, anchor=anchor, tags=tags)
        self.cv.create_text(x, y + 14, text=bc.fmt_eng(el["ideal"], u, 4),
                            font=FONT_S, fill=COL_IDEAL, anchor=anchor, tags=tags)
        if self.show_rounded:
            self.cv.create_text(x, y + 27, text="%s: %s" % (ser, bc.fmt_eng(el["rounded"], u, 3)),
                                font=FONT_S, fill=COL_ROUND, anchor=anchor, tags=tags)
        if el["adjusted"] != el["rounded"]:
            dev = (el["adjusted"] / el["rounded"] - 1) * 100
            self.cv.create_text(x, y + 40, text="→ %s (%+.1f%%)" % (bc.fmt_eng(el["adjusted"], u, 4), dev),
                                font=FONT_B, fill=COL_ADJ, anchor=anchor, tags=tags)

    # ---- recursive layout -----------------------------------------------
    def draw(self, node, o, x, y):
        w, h, t = _size(node, o)
        k = node[0]
        if k == "el":
            el = node[1]
            if o == "h":
                yy = y + t
                self.line(x, yy, x + 26, yy)
                self.line(x + w - 26, yy, x + w, yy)
                (self.sym_inductor if el["kind"] == "L" else self.sym_capacitor)(
                    x + 26, yy, x + w - 26, yy)
                self.labels(el, x + w / 2.0, y + 4, "n")
            else:
                xx = x + t
                self.line(xx, y, xx, y + 14)
                self.line(xx, y + h - 14, xx, y + h)
                (self.sym_inductor if el["kind"] == "L" else self.sym_capacitor)(
                    xx, y + 14, xx, y + h - 14)
                self.labels(el, xx + 16, y + 12, "nw")
            return
        kids = node[1]
        sizes = [_size(c, o) for c in kids]
        if o == "h" and k == "ser":
            cx = x
            for c, (cw, ch, ct) in zip(kids, sizes):
                self.draw(c, o, cx, y + t - ct)
                cx += cw
        elif o == "v" and k == "ser":
            cy = y
            for c, (cw, ch, ct) in zip(kids, sizes):
                self.draw(c, o, x + t - ct, cy)
                cy += ch
        elif o == "h" and k == "par":
            xl, xr = x + PAD / 2.0, x + w - PAD / 2.0
            cy = y
            ys = []
            for c, (cw, ch, ct) in zip(kids, sizes):
                self.draw(c, o, x + PAD, cy)
                ty = cy + ct
                ys.append(ty)
                self.line(xl, ty, x + PAD, ty)
                self.line(x + PAD + cw, ty, xr, ty)
                cy += ch + GAP
            self.line(xl, ys[0], xl, ys[-1])
            self.line(xr, ys[0], xr, ys[-1])
            self.line(x, y + t, xl, y + t)
            self.line(xr, y + t, x + w, y + t)
            self.dot(xl, y + t)
            self.dot(xr, y + t)
        else:  # par, vertical
            yt, yb = y + PAD / 2.0, y + h - PAD / 2.0
            cx = x
            xs = []
            for c, (cw, ch, ct) in zip(kids, sizes):
                self.draw(c, o, cx, y + PAD)
                tx = cx + ct
                xs.append(tx)
                self.line(tx, yt, tx, y + PAD)
                self.line(tx, y + PAD + ch, tx, yb)
                cx += cw + GAP
            self.line(xs[0], yt, xs[-1], yt)
            self.line(xs[0], yb, xs[-1], yb)
            self.line(x + t, y, x + t, yt)
            self.line(x + t, yb, x + t, y + h)
            self.dot(x + t, yt)
            self.dot(x + t, yb)

    def dot(self, x, y):
        self.cv.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=COL_WIRE, outline=COL_WIRE)

    def render(self, design):
        self.design = design
        cv = self.cv
        cv.delete("all")
        top = 20
        ser_sizes = [_size(t, "h") for p, t in design.branches if p == "series"]
        sh_sizes = [_size(t, "v") for p, t in design.branches if p == "shunt"]
        rail = top + max([s[2] for s in ser_sizes] + [40])
        below = max([s[1] for s in sh_sizes] + [s[1] - s[2] for s in ser_sizes] + [70])
        gnd = rail + below + 24
        # source
        x0 = 30
        ym = (rail + gnd) / 2.0
        self.line(x0, rail, x0, ym - 16)
        self.line(x0, ym + 16, x0, gnd)
        cv.create_oval(x0 - 16, ym - 16, x0 + 16, ym + 16, outline=COL_WIRE, width=1.5)
        cv.create_text(x0, ym, text="~", font=("Meiryo UI", 14))
        self.line(x0, rail, x0 + 20, rail)
        self.sym_resistor(x0 + 20, rail, x0 + 80, rail)
        cv.create_text(x0 + 50, rail - 26, text="Rs", font=FONT_B)
        cv.create_text(x0 + 50, rail - 13, text=bc.fmt_eng(design.rs, "Ω", 4), font=FONT_S)
        cx = x0 + 80
        self.line(cx, rail, cx + 20, rail)
        cx += 20
        for pos, t in design.branches:
            if pos == "series":
                w, h, tt = _size(t, "h")
                self.draw(t, "h", cx, rail - tt)
                cx += w
                self.line(cx, rail, cx + 12, rail)
                cx += 12
            else:
                w, h, tt = _size(t, "v")
                nx = cx + 16 + tt
                self.line(cx, rail, cx + w + 32, rail)
                self.draw(t, "v", cx + 16, rail)
                self.line(nx, rail + h, nx, gnd)
                self.dot(nx, rail)
                self.dot(nx, gnd)
                cx += w + 32
        # load
        xl = cx + 30
        self.line(cx, rail, xl, rail)
        self.line(xl, rail, xl, ym - 30)
        self.sym_resistor(xl, ym - 30, xl, ym + 30)
        self.line(xl, ym + 30, xl, gnd)
        cv.create_text(xl + 14, ym - 8, text="RL", font=FONT_B, anchor="w")
        cv.create_text(xl + 14, ym + 8, text=bc.fmt_eng(design.rl, "Ω", 4), font=FONT_S, anchor="w")
        self.line(x0, gnd, xl, gnd)
        # ground symbol
        gx = (x0 + xl) / 2.0
        self.line(gx, gnd, gx, gnd + 8)
        for i, hw in enumerate((10, 6, 2)):
            self.line(gx - hw, gnd + 8 + 3 * i, gx + hw, gnd + 8 + 3 * i)
        # legend
        cv.create_text(xl + 70, top, text="青: 理想値", fill=COL_IDEAL, font=FONT_S, anchor="nw")
        if self.show_rounded:
            cv.create_text(xl + 70, top + 14, text="赤: E系列値", fill=COL_ROUND, font=FONT_S, anchor="nw")
        bb = cv.bbox("all")
        cv.configure(scrollregion=(0, 0, bb[2] + 30, bb[3] + 20))


# ==========================================================================
# frequency response plot
# ==========================================================================
def _log_ticks(fmin, fmax):
    for mults in ([1], [1, 2, 5], list(range(1, 10))):
        ticks = []
        d = int(math.floor(math.log10(fmin)))
        while 10.0 ** d <= fmax * 1.0000001:
            for m in mults:
                v = m * 10.0 ** d
                if fmin * 0.9999999 <= v <= fmax * 1.0000001:
                    ticks.append(v)
            d += 1
        if len(ticks) >= 4:
            return ticks, mults == [1]
    # narrow range: linear nice ticks
    span = fmax - fmin
    raw = span / 6.0
    mag = 10.0 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 5, 10)), key=lambda s: abs(s - raw))
    v = math.ceil(fmin / step) * step
    ticks = []
    while v <= fmax:
        ticks.append(v)
        v += step
    return ticks, False


class PlotView(object):
    ML, MR, MT, MB = 64, 18, 26, 42

    def __init__(self, canvas, status_cb):
        self.cv = canvas
        self.data = None
        self.stopspec = None
        self.status_cb = status_cb
        canvas.bind("<Configure>", lambda e: self.redraw())
        canvas.bind("<Motion>", self.on_motion)
        canvas.bind("<Leave>", lambda e: self.cv.delete("cursor"))

    def set_data(self, freqs, ideal, rounded, marks, yrange, title, adjusted=None, show_ideal=True):
        self.data = (freqs, ideal, rounded, marks, yrange, title)
        self.adjusted = adjusted
        self.show_ideal = show_ideal
        self.redraw()

    def geom(self):
        w = max(self.cv.winfo_width(), 200)
        h = max(self.cv.winfo_height(), 150)
        return self.ML, self.MT, w - self.MR, h - self.MB

    def xpix(self, f):
        x0, y0, x1, y1 = self.geom()
        fmin, fmax = self.data[0][0], self.data[0][-1]
        return x0 + (math.log10(f) - math.log10(fmin)) / (math.log10(fmax) - math.log10(fmin)) * (x1 - x0)

    def ypix(self, db):
        x0, y0, x1, y1 = self.geom()
        ytop, ybot = self.data[4]
        db = max(min(db, ytop), ybot)
        return y0 + (ytop - db) / (ytop - ybot) * (y1 - y0)

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        if not self.data:
            cv.create_text(cv.winfo_width() / 2, cv.winfo_height() / 2,
                           text="「設計」を押すと周波数特性を表示します", font=FONT, fill="#888")
            return
        freqs, ideal, rounded, marks, (ytop, ybot), title = self.data
        x0, y0, x1, y1 = self.geom()
        fmin, fmax = freqs[0], freqs[-1]
        cv.create_rectangle(x0, y0, x1, y1, outline="#888")
        # x grid
        ticks, decades = _log_ticks(fmin, fmax)
        if decades:
            d = int(math.floor(math.log10(fmin)))
            while 10.0 ** d <= fmax:
                for m in range(2, 10):
                    v = m * 10.0 ** d
                    if fmin < v < fmax:
                        xp = self.xpix(v)
                        cv.create_line(xp, y0, xp, y1, fill="#eeeeee")
                d += 1
        last = -1e9
        for v in ticks:
            xp = self.xpix(v)
            cv.create_line(xp, y0, xp, y1, fill="#d6d6d6")
            if xp - last >= 62:  # thin out labels that would overlap
                cv.create_text(xp, y1 + 4, text=bc.fmt_eng(v, "Hz", 4), anchor="n", font=FONT_S)
                last = xp
        # y grid
        span = ytop - ybot
        step = 20 if span > 90 else 10 if span > 45 else 5 if span > 12 else 1
        v = math.floor(ytop / step) * step
        while v >= ybot - 1e-9:
            yp = self.ypix(v)
            cv.create_line(x0, yp, x1, yp, fill="#d6d6d6")
            cv.create_text(x0 - 4, yp, text="%g" % v, anchor="e", font=FONT_S)
            v -= step
        cv.create_text(16, (y0 + y1) / 2, text="|S21| [dB]", angle=90, font=FONT_S)
        cv.create_text((x0 + x1) / 2, y1 + 24, text="周波数（対数軸）", font=FONT_S)
        cv.create_text(x0, 6, text=title, anchor="nw", font=FONT_S, fill="#444")
        # markers
        for f, label, col in marks:
            if fmin < f < fmax:
                xp = self.xpix(f)
                cv.create_line(xp, y0, xp, y1, fill=col, dash=(4, 3))
                cv.create_text(xp + 3, y0 + 3, text=label, anchor="nw", font=FONT_S, fill=col)
        # stopband requirement guide (elliptic): -As outside the stopband edges
        if self.stopspec:
            fs_lo, fs_hi, a = self.stopspec
            ya = self.ypix(-a)
            if fs_lo > fmin:
                cv.create_line(x0, ya, self.xpix(fs_lo), ya, fill="#8a2a8a", dash=(2, 3))
            if fs_hi < fmax:
                cv.create_line(self.xpix(fs_hi), ya, x1, ya, fill="#8a2a8a", dash=(2, 3))
        # curves: ideal solid, E-series dashed (distinguishable without colour),
        # adjusted: thick green, drawn on top
        curves = []
        if self.show_ideal:
            curves.append((ideal, COL_IDEAL, None, 1.8, "理想値（実線）"))
        curves.append((rounded, COL_ROUND, (6, 3), 1.8, "E系列値＝設計値（破線）"))
        if self.adjusted:
            curves.append((self.adjusted, COL_ADJ, None, 2.4, "調整後（太線）"))
        for vals, col, dash, wdt, _ in curves:
            pts = []
            for f, d in zip(freqs, vals):
                pts += [self.xpix(f), self.ypix(d)]
            cv.create_line(*pts, fill=col, width=wdt, dash=dash)
        # legend
        lx, ly = x1 - 200, y1 - 14 - 16 * len(curves)
        cv.create_rectangle(lx, ly, x1 - 8, y1 - 8, fill="white", outline="#bbb")
        for i, (vals, col, dash, wdt, label) in enumerate(curves):
            yy = ly + 12 + 16 * i
            cv.create_line(lx + 8, yy, lx + 32, yy, fill=col, width=2, dash=dash)
            cv.create_text(lx + 38, yy, text=label, anchor="w", font=FONT_S)

    def on_motion(self, ev):
        if not self.data:
            return
        x0, y0, x1, y1 = self.geom()
        self.cv.delete("cursor")
        if not (x0 <= ev.x <= x1 and y0 <= ev.y <= y1):
            return
        freqs, ideal, rounded = self.data[0], self.data[1], self.data[2]
        fmin, fmax = freqs[0], freqs[-1]
        lf = math.log10(fmin) + (ev.x - x0) / float(x1 - x0) * (math.log10(fmax) - math.log10(fmin))
        i = int(round((lf - math.log10(fmin)) / (math.log10(fmax) - math.log10(fmin)) * (len(freqs) - 1)))
        i = max(0, min(len(freqs) - 1, i))
        xp = self.xpix(freqs[i])
        self.cv.create_line(xp, y0, xp, y1, fill="#666", tags="cursor")
        txt = "%s   理想 %.2f dB   E系列 %.2f dB" % (bc.fmt_eng(freqs[i], "Hz", 5), ideal[i], rounded[i])
        bw = 256
        if self.adjusted:
            txt += "   調整後 %.2f dB (%+.2f)" % (self.adjusted[i], self.adjusted[i] - rounded[i])
            bw = 400
        tx = min(max(xp + 6, x0 + 4), x1 - bw - 4)
        self.cv.create_rectangle(tx - 3, y0 + 20, tx + bw, y0 + 38, fill="#ffffe8",
                                 outline="#999", tags="cursor")
        self.cv.create_text(tx, y0 + 29, text=txt, anchor="w", font=FONT_S, tags="cursor")


# ==========================================================================
# main window
# ==========================================================================
YRANGES = [("0 ～ -120 dB", (5, -120)), ("0 ～ -80 dB", (5, -80)),
           ("0 ～ -40 dB", (2, -40)), ("通過域 (+1 ～ -6 dB)", (1, -6))]
FIRSTS = [("並列共振（シャント）先頭", "shunt"), ("直列共振先頭", "series")]


class App(object):
    def __init__(self, root):
        self.root = root
        self.design = None
        root.title("LC BPF 設計ツール")
        root.geometry("1440x900")
        root.minsize(1000, 650)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("Treeview.Heading", font=FONT_B)

        main = ttk.Frame(root, padding=6)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = ttk.PanedWindow(main, orient="vertical")
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        self.v = {}
        frm = ttk.LabelFrame(left, text="設計条件", padding=6)
        frm.pack(fill="x")
        r = 0

        def row(label):
            nonlocal r
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=2)
            r += 1
            return r - 1

        for key, label, default in (("fl", "カットオフ fL（低域側）", "1"),
                                    ("fh", "カットオフ fH（高域側）", "2")):
            rr = row(label)
            self.v[key] = tk.StringVar(value=default)
            ttk.Entry(frm, textvariable=self.v[key], width=10).grid(row=rr, column=1, sticky="we")
            self.v[key + "_u"] = tk.StringVar(value="MHz")
            ttk.Combobox(frm, textvariable=self.v[key + "_u"], values=list(UNITS), width=5,
                         state="readonly").grid(row=rr, column=2, padx=(3, 0))
        rr = row("次数（LPF プロトタイプ）")
        self.v["n"] = tk.StringVar(value="5")
        ttk.Spinbox(frm, from_=1, to=20, textvariable=self.v["n"], width=8).grid(row=rr, column=1, sticky="we")
        rr = row("フィルタ特性")
        self.v["type"] = tk.StringVar(value=bc.TYPES[0])
        cb = ttk.Combobox(frm, textvariable=self.v["type"], values=list(bc.TYPES), state="readonly", width=13)
        cb.grid(row=rr, column=1, columnspan=2, sticky="we")
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_states())
        rr = row("通過域リップル [dB]")
        self.v["rip"] = tk.StringVar(value="0.5")
        self.e_rip = ttk.Entry(frm, textvariable=self.v["rip"], width=10)
        self.e_rip.grid(row=rr, column=1, sticky="we")
        rr = row("阻止域減衰量 [dB]")
        self.v["as"] = tk.StringVar(value="40")
        self.e_as = ttk.Entry(frm, textvariable=self.v["as"], width=10)
        self.e_as.grid(row=rr, column=1, sticky="we")
        rr = row("入出力インピーダンス [Ω]")
        self.v["r0"] = tk.StringVar(value="50")
        ttk.Entry(frm, textvariable=self.v["r0"], width=10).grid(row=rr, column=1, sticky="we")
        rr = row("L の E 系列")
        self.v["les"] = tk.StringVar(value="E12")
        ttk.Combobox(frm, textvariable=self.v["les"], values=list(bc.E_SERIES), state="readonly",
                     width=8).grid(row=rr, column=1, sticky="we")
        rr = row("C の E 系列")
        self.v["ces"] = tk.StringVar(value="E12")
        ttk.Combobox(frm, textvariable=self.v["ces"], values=list(bc.E_SERIES), state="readonly",
                     width=8).grid(row=rr, column=1, sticky="we")
        rr = row("回路構成")
        self.v["first"] = tk.StringVar(value=FIRSTS[0][0])
        ttk.Combobox(frm, textvariable=self.v["first"], values=[f[0] for f in FIRSTS],
                     state="readonly", width=18).grid(row=rr + 1, column=0, columnspan=3, sticky="we")
        r += 1
        frm.columnconfigure(1, weight=1)

        frm2 = ttk.LabelFrame(left, text="表示", padding=6)
        frm2.pack(fill="x", pady=(6, 0))
        ttk.Label(frm2, text="表示範囲 fmin / fmax（空欄=自動、単位は fL と同じ）").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.v["pmin"] = tk.StringVar()
        self.v["pmax"] = tk.StringVar()
        ttk.Entry(frm2, textvariable=self.v["pmin"], width=10).grid(row=1, column=0, sticky="we")
        ttk.Label(frm2, text="～").grid(row=1, column=1)
        ttk.Entry(frm2, textvariable=self.v["pmax"], width=10).grid(row=1, column=2, sticky="we")
        ttk.Label(frm2, text="縦軸").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.v["yr"] = tk.StringVar(value=YRANGES[0][0])
        cb = ttk.Combobox(frm2, textvariable=self.v["yr"], values=[y[0] for y in YRANGES],
                          state="readonly", width=18)
        cb.grid(row=3, column=0, columnspan=3, sticky="we")
        cb.bind("<<ComboboxSelected>>", lambda e: self.replot())
        self.v["showr"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm2, text="回路図に E 系列値を表示", variable=self.v["showr"],
                        command=self.redraw_circuit).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.v["showi"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm2, text="理想値の曲線を表示", variable=self.v["showi"],
                        command=lambda: self.replot(keep=True)).grid(row=5, column=0, columnspan=3, sticky="w")
        frm2.columnconfigure(0, weight=1)
        frm2.columnconfigure(2, weight=1)

        bf = ttk.Frame(left)
        bf.pack(fill="x", pady=8)
        ttk.Button(bf, text="設計", command=self.run).pack(side="left", fill="x", expand=True)
        ttk.Button(bf, text="結果をコピー", command=self.copy).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Button(bf, text="CSV 保存", command=self.save_csv).pack(side="left", fill="x", expand=True, padx=(6, 0))
        root.bind("<Return>", lambda e: self.run())

        self.info = tk.Text(left, height=14, font=FONT_S, wrap="word", relief="solid", borderwidth=1)
        self.info.pack(fill="both", expand=True)

        # circuit
        cf = ttk.LabelFrame(right, text="回路図（梯子型 LC バンドパス）", padding=2)
        self.ccv = tk.Canvas(cf, background="white", highlightthickness=0)
        hs = ttk.Scrollbar(cf, orient="horizontal", command=self.ccv.xview)
        vs = ttk.Scrollbar(cf, orient="vertical", command=self.ccv.yview)
        self.ccv.configure(xscrollcommand=hs.set, yscrollcommand=vs.set)
        self.ccv.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        cf.rowconfigure(0, weight=1)
        cf.columnconfigure(0, weight=1)
        self.ccv.bind("<Shift-MouseWheel>", lambda e: self.ccv.xview_scroll(-int(e.delta / 120), "units"))
        self.ccv.bind("<MouseWheel>", lambda e: self.circuit.handle_wheel(e)
                      or self.ccv.yview_scroll(-int(e.delta / 120), "units"))
        self.circuit = CircuitView(self.ccv, on_wheel=self.step, on_click=self.select_row)
        right.add(cf, weight=3)

        pf = ttk.LabelFrame(right, text="周波数特性（振幅）", padding=2)
        pcv = tk.Canvas(pf, background="white", highlightthickness=0)
        pcv.pack(fill="both", expand=True)
        self.plot = PlotView(pcv, None)
        right.add(pf, weight=4)

        nb = ttk.Notebook(right)
        self.nb = nb
        # --- adjust tab
        af = ttk.Frame(nb, padding=2)
        top = ttk.Frame(af)
        top.pack(fill="x")
        ttk.Label(top, text="◀ ▶ / ↑↓キー / 回路図ラベル上のホイールで E系列の前後の値へ。"
                            "入力欄に任意値（330p, 1.2u など）を入れて Enter。",
                  font=FONT_S).pack(side="left")
        ttk.Button(top, text="すべて設計値に戻す", command=self.reset_all).pack(side="right")
        holder = ttk.Frame(af)
        holder.pack(fill="both", expand=True, pady=(3, 0))
        self.acv = tk.Canvas(holder, highlightthickness=0, height=120)
        asb = ttk.Scrollbar(holder, orient="vertical", command=self.acv.yview)
        self.acv.configure(yscrollcommand=asb.set)
        self.acv.pack(side="left", fill="both", expand=True)
        asb.pack(side="left", fill="y")
        self.aframe = ttk.Frame(self.acv)
        self.acv.create_window(0, 0, window=self.aframe, anchor="nw")
        self.aframe.bind("<Configure>", lambda e: self.acv.configure(scrollregion=self.acv.bbox("all")))
        self.acv.bind("<MouseWheel>", lambda e: self.acv.yview_scroll(-int(e.delta / 120), "units"))
        nb.add(af, text="素子値の調整")
        self.adj_rows = {}
        # --- element table tab
        tf = ttk.Frame(nb, padding=2)
        cols = ("name", "pos", "ideal", "round", "err", "adj", "dev")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=6)
        for c, t, w in zip(cols, ("素子", "配置", "理想値", "E系列値", "丸め誤差", "調整値", "設計値からのずれ"),
                           (60, 70, 130, 120, 80, 120, 110)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        ts = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ts.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ts.pack(side="left", fill="y")
        nb.add(tf, text="素子一覧")
        right.add(nb, weight=2)

        self.update_states()
        root.after(100, self.run)

    # ------------------------------------------------------------------
    def update_states(self):
        t = self.v["type"].get()
        self.e_rip.configure(state="normal" if t != "バタワース" else "disabled")
        self.e_as.configure(state="normal" if t == "エリプティック" else "disabled")

    def _num(self, key, label):
        s = self.v[key].get().strip()
        try:
            return float(s)
        except ValueError:
            raise ValueError("%s の値が不正です: '%s'" % (label, s))

    def run(self):
        try:
            fl = self._num("fl", "fL") * UNITS[self.v["fl_u"].get()]
            fh = self._num("fh", "fH") * UNITS[self.v["fh_u"].get()]
            if fl <= 0 or fh <= 0:
                raise ValueError("周波数は正の値にしてください")
            if not fl < fh:
                raise ValueError("fL < fH となるように入力してください")
            ns = self.v["n"].get().strip()
            if not ns.isdigit() or not 1 <= int(ns) <= 20:
                raise ValueError("次数は 1～20 の整数です")
            n = int(ns)
            t = self.v["type"].get()
            rip = self._num("rip", "リップル")
            asd = self._num("as", "阻止域減衰量")
            if t != "バタワース" and rip <= 0:
                raise ValueError("リップルは正の値にしてください")
            if t == "エリプティック" and asd <= rip:
                raise ValueError("阻止域減衰量はリップルより大きくしてください")
            r0 = self._num("r0", "インピーダンス")
            if r0 <= 0:
                raise ValueError("インピーダンスは正の値にしてください")
            first = dict(FIRSTS)[self.v["first"].get()]
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            d = bc.design_bpf(fl, fh, n, t, r0, rip, asd, self.v["les"].get(), self.v["ces"].get(), first)
        except (ValueError, bc.SynthesisError) as ex:
            self.root.config(cursor="")
            messagebox.showerror("入力エラー", str(ex))
            return
        self.root.config(cursor="")
        self.design = d
        self.redraw_circuit()
        self.fill_table()
        self.build_adjust_panel()
        self.replot()
        self.fill_info()

    def redraw_circuit(self):
        if self.design:
            self.circuit.show_rounded = self.v["showr"].get()
            xv = self.ccv.xview()[0]
            self.circuit.render(self.design)
            self.ccv.xview_moveto(xv)  # keep the scroll position while adjusting

    def fill_table(self):
        self.tree.delete(*self.tree.get_children())
        d = self.design
        for el in d.elements:
            u = "H" if el["kind"] == "L" else "F"
            err = (el["rounded"] / el["ideal"] - 1) * 100
            adj = el["adjusted"] != el["rounded"]
            self.tree.insert("", "end", values=(
                el["name"], el["pos"], bc.fmt_eng(el["ideal"], u, 5), bc.fmt_eng(el["rounded"], u, 3),
                "%+.1f %%" % err, bc.fmt_eng(el["adjusted"], u, 4) if adj else "―",
                "%+.1f %%" % ((el["adjusted"] / el["rounded"] - 1) * 100) if adj else "―"))

    # ---- per-element adjustment ----------------------------------------
    def _el(self, name):
        for el in self.design.elements:
            if el["name"] == name:
                return el
        return None

    def build_adjust_panel(self):
        for w in self.aframe.winfo_children():
            w.destroy()
        self.adj_rows = {}
        hdr = ("素子", "配置", "設計値（E系列）", "", "現在値", "", "ずれ")
        for c, t in enumerate(hdr):
            ttk.Label(self.aframe, text=t, font=FONT_B).grid(row=0, column=c, padx=4, sticky="w")
        for i, el in enumerate(self.design.elements):
            u = "H" if el["kind"] == "L" else "F"
            r = i + 1
            name = el["name"]
            ttk.Label(self.aframe, text=name, font=FONT_B).grid(row=r, column=0, padx=4, sticky="w")
            ttk.Label(self.aframe, text=el["pos"]).grid(row=r, column=1, padx=4, sticky="w")
            ttk.Label(self.aframe, text="%s  (%s)" % (bc.fmt_eng(el["rounded"], u, 3), el["series"])).grid(
                row=r, column=2, padx=4, sticky="w")
            ttk.Button(self.aframe, text="◀", width=3,
                       command=lambda n=name: self.step(n, -1)).grid(row=r, column=3)
            var = tk.StringVar(value=bc.fmt_eng(el["adjusted"], u, 4))
            ent = ttk.Entry(self.aframe, textvariable=var, width=14)
            ent.grid(row=r, column=4, padx=2)
            ent.bind("<Return>", lambda e, n=name: (self.set_value(n), "break")[1])
            ent.bind("<KP_Enter>", lambda e, n=name: (self.set_value(n), "break")[1])
            ent.bind("<Up>", lambda e, n=name: (self.step(n, 1), "break")[1])
            ent.bind("<Down>", lambda e, n=name: (self.step(n, -1), "break")[1])
            ttk.Button(self.aframe, text="▶", width=3,
                       command=lambda n=name: self.step(n, 1)).grid(row=r, column=5)
            dev = ttk.Label(self.aframe, text="", width=10)
            dev.grid(row=r, column=6, padx=4, sticky="w")
            self.adj_rows[name] = {"var": var, "entry": ent, "dev": dev}
            for w in self.aframe.grid_slaves(row=r):
                w.bind("<MouseWheel>", lambda e: self.acv.yview_scroll(-int(e.delta / 120), "units"), add="+")
            self._update_row(name)
        self.acv.yview_moveto(0)

    def _update_row(self, name):
        el = self._el(name)
        row = self.adj_rows.get(name)
        if not el or not row:
            return
        u = "H" if el["kind"] == "L" else "F"
        row["var"].set(bc.fmt_eng(el["adjusted"], u, 4))
        if el["adjusted"] != el["rounded"]:
            row["dev"].configure(text="%+.1f %%" % ((el["adjusted"] / el["rounded"] - 1) * 100), foreground=COL_ADJ)
        else:
            row["dev"].configure(text="―", foreground="")

    def step(self, name, k):
        el = self._el(name) if self.design else None
        if not el:
            return
        el["adjusted"] = bc.step_series(el["adjusted"], el["series"], k)
        self.after_adjust(name)

    def set_value(self, name):
        el = self._el(name)
        if not el:
            return
        u = "H" if el["kind"] == "L" else "F"
        try:
            el["adjusted"] = bc.parse_eng(self.adj_rows[name]["var"].get(), u)
        except ValueError as ex:
            messagebox.showerror("素子値の入力", str(ex))
            self._update_row(name)
            return
        self.after_adjust(name)

    def reset_all(self):
        if not self.design:
            return
        bc.reset_adjust(self.design)
        self.after_adjust(None)

    def select_row(self, name):
        row = self.adj_rows.get(name)
        if not row:
            return
        self.nb.select(0)
        idx = [el["name"] for el in self.design.elements].index(name)
        n = max(len(self.design.elements), 1)
        self.acv.yview_moveto(max(0.0, (idx - 1) / float(n + 1)))
        row["entry"].focus_set()
        row["entry"].select_range(0, "end")

    def after_adjust(self, name):
        if name:
            self._update_row(name)
        else:
            for n in self.adj_rows:
                self._update_row(n)
        self.redraw_circuit()
        self.fill_table()
        self.replot(keep=True)
        self.fill_info()

    def freq_range(self):
        d = self.design
        bw = d.fh - d.fl
        fmin = max(d.fl - 1.5 * bw, d.fl / 4.0)
        fmax = min(d.fh + 1.5 * bw, d.fh * 4.0)
        mul = UNITS[self.v["fl_u"].get()]
        try:
            if self.v["pmin"].get().strip():
                fmin = float(self.v["pmin"].get()) * mul
            if self.v["pmax"].get().strip():
                fmax = float(self.v["pmax"].get()) * mul
        except ValueError:
            messagebox.showwarning("表示範囲", "表示範囲の値が不正なので自動範囲を使います")
        if not (0 < fmin < fmax):
            fmin, fmax = d.fl / 4.0, d.fh * 4.0
        return fmin, fmax

    def replot(self, keep=False):
        d = self.design
        if not d:
            return
        if keep and getattr(self, "_resp", None) and self._resp[3] is d:
            freqs, ideal, rounded = self._resp[:3]   # only the adjusted curve changes
        else:
            fmin, fmax = self.freq_range()
            N = 1601
            la, lb = math.log10(fmin), math.log10(fmax)
            freqs = [10 ** (la + (lb - la) * i / (N - 1)) for i in range(N)]
            ideal = bc.response_db(d, freqs, False)
            rounded = bc.response_db(d, freqs, True)
            self._resp = (freqs, ideal, rounded, d)
        adjusted = bc.response_db(d, freqs, "adjusted") if bc.is_adjusted(d) else None
        marks = [(d.fl, "fL", "#2a8a2a"), (d.fh, "fH", "#2a8a2a")]
        self.plot.stopspec = None
        if d.stop_edges:
            marks += [(d.stop_edges[0], "fs-", "#8a2a8a"), (d.stop_edges[1], "fs+", "#8a2a8a")]
            self.plot.stopspec = (d.stop_edges[0], d.stop_edges[1], d.as_db)
        title = "%s %d次  fL=%s  fH=%s  R0=%s  L:%s C:%s" % (
            d.ftype, d.order, bc.fmt_eng(d.fl, "Hz", 4), bc.fmt_eng(d.fh, "Hz", 4),
            bc.fmt_eng(d.r0, "Ω", 4), d.l_series, d.c_series)
        self.plot.set_data(freqs, ideal, rounded, marks, dict(YRANGES)[self.v["yr"].get()], title,
                           adjusted=adjusted, show_ideal=self.v["showi"].get())

    def info_text(self):
        d = self.design
        L = []
        L.append("特性: %s  次数: %d" % (d.ftype, d.order))
        if d.ftype != "バタワース":
            L.append("通過域リップル: %g dB" % d.ripple_db)
        if d.ftype == "エリプティック":
            L.append("阻止域減衰量: %g dB" % d.as_db)
        L.append("fL/fH の定義: " + ("-3 dB 点" if d.ftype == "バタワース" else "リップル通過域の端"))
        L.append("中心周波数 f0 = √(fL·fH) = %s" % bc.fmt_eng(d.f0, "Hz", 5))
        L.append("帯域幅 = %s（比帯域 %.3g %%）" % (bc.fmt_eng(d.fh - d.fl, "Hz", 5), (d.fh - d.fl) / d.f0 * 100))
        L.append("Rs = %s, RL = %s" % (bc.fmt_eng(d.rs, "Ω", 5), bc.fmt_eng(d.rl, "Ω", 5)))
        if abs(d.rl - d.rs) > 1e-6 * d.rs:
            L.append("※ 偶数次チェビシェフは終端抵抗が等しくなりません（RL は上記の値）。")
        if d.stop_edges:
            L.append("阻止域端（%g dB 到達）: %s / %s" % (d.as_db, bc.fmt_eng(d.stop_edges[0], "Hz", 5),
                                                     bc.fmt_eng(d.stop_edges[1], "Hz", 5)))
            L.append("LPF 正規化阻止域端 Ωs = %.6g" % d.info["ws"])
            L.append("合成検証誤差（目標特性との差）: %.2g dB" % d.info["synth_err_db"])
            if d.order % 2 == 0:
                L.append("※ 偶数次は等終端にするため type c 変形（伝送零点1組を∞へ移動）を使用。")
            if d.order >= 9:
                L.append("※ 高次エリプティックは素子感度が非常に高く、E系列に丸めると特性が大きく崩れます。")
        if "g" in d.info:
            L.append("g 値: " + ", ".join("%.5g" % g for g in d.info["g"]))
        if hasattr(self, "_resp"):
            vals = bc.response_db(d, [d.fl, d.f0, d.fh], False)
            valr = bc.response_db(d, [d.fl, d.f0, d.fh], True)
            L.append("")
            L.append("|S21| [dB]      fL       f0       fH")
            L.append("  理想値     %7.2f  %7.2f  %7.2f" % tuple(vals))
            L.append("  E系列値    %7.2f  %7.2f  %7.2f" % tuple(valr))
            if bc.is_adjusted(d):
                vala = bc.response_db(d, [d.fl, d.f0, d.fh], "adjusted")
                L.append("  調整後     %7.2f  %7.2f  %7.2f" % tuple(vala))
                L.append("調整中の素子: " + ", ".join(el["name"] for el in d.elements
                                                    if el["adjusted"] != el["rounded"]))
        return "\n".join(L)

    def fill_info(self):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", self.info_text())
        self.info.configure(state="disabled")

    def copy(self):
        if not self.design:
            return
        lines = [self.info_text(), "", "素子\t配置\t理想値\tE系列値\t調整値"]
        for el in self.design.elements:
            u = "H" if el["kind"] == "L" else "F"
            lines.append("%s\t%s\t%s\t%s\t%s" % (el["name"], el["pos"], bc.fmt_eng(el["ideal"], u, 5),
                                                 bc.fmt_eng(el["rounded"], u, 3), bc.fmt_eng(el["adjusted"], u, 4)))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        messagebox.showinfo("コピー", "設計結果をクリップボードにコピーしました")

    def save_csv(self):
        d = self.design
        if not d:
            messagebox.showinfo("CSV 保存", "先に設計してください。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="lc_bpf_%s_%d.csv" % ({"バタワース": "butterworth", "チェビシェフ": "chebyshev",
                                                "エリプティック": "elliptic"}[d.ftype], d.order))
        if not path:
            return
        # utf-8-sig so that Excel opens the Japanese headers correctly
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(["# %s %d次 fL=%g Hz fH=%g Hz Rs=%g ohm RL=%g ohm L:%s C:%s" % (
                d.ftype, d.order, d.fl, d.fh, d.rs, d.rl, d.l_series, d.c_series)])
            w.writerow(["素子", "配置", "種類", "理想値", "E系列値", "単位", "丸め誤差[%]",
                        "調整値", "設計値からのずれ[%]"])
            for el in d.elements:
                w.writerow([el["name"], el["pos"], el["kind"], "%.6g" % el["ideal"], "%.4g" % el["rounded"],
                            "H" if el["kind"] == "L" else "F",
                            "%.2f" % ((el["rounded"] / el["ideal"] - 1) * 100),
                            "%.6g" % el["adjusted"], "%.2f" % ((el["adjusted"] / el["rounded"] - 1) * 100)])
        messagebox.showinfo("CSV 保存", "保存しました:\n%s" % path)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    # --demo [butterworth|chebyshev|elliptic] [order]: start with that design
    argv = sys.argv[1:]
    if "--demo" in argv:
        rest = argv[argv.index("--demo") + 1:]
        names = {"butterworth": "バタワース", "chebyshev": "チェビシェフ", "elliptic": "エリプティック"}
        if rest and rest[0] in names:
            app.v["type"].set(names[rest[0]])
            rest = rest[1:]
        if rest and rest[0].isdigit():
            app.v["n"].set(rest[0])
    # optional presets: key=value (fl, fh, n, type, rip, as, r0, les, ces, first, yr)
    for arg in argv:
        if "=" in arg:
            k, val = arg.split("=", 1)
            if k == "first":
                val = FIRSTS[1][0] if val == "series" else FIRSTS[0][0]
            elif k == "yr":
                val = YRANGES[int(val)][0]
            if k in app.v:
                app.v[k].set(val)
    app.update_states()
    # --adjust C3=+1,L2=-1: step those elements after the first design
    if "--adjust" in argv and argv.index("--adjust") + 1 < len(argv):
        spec = argv[argv.index("--adjust") + 1]

        def apply_adjust():
            for item in spec.split(","):
                if "=" in item:
                    name, k = item.split("=", 1)
                    try:
                        app.step(name.strip(), int(k))
                    except ValueError:
                        pass
        root.after(400, apply_adjust)
    # --autocheck result.json: exercise the GUI handlers, write the result, quit
    if "--autocheck" in argv and argv.index("--autocheck") + 1 < len(argv):
        out = argv[argv.index("--autocheck") + 1]
        root.after(600, lambda: _autocheck(app, out))
    if len(sys.argv) > 2 and sys.argv[1] == "--screenshot":
        root.after(2500, lambda: _shot(root, sys.argv[2]))
    root.mainloop()


def _autocheck(app, out):
    """GUI self-check through the real event handlers (works in the exe too)."""
    import json
    res = {}
    root = app.root
    try:
        d = app.design
        res["designed"] = d is not None
        caps = [el for el in d.elements if el["kind"] == "C"]
        # 1) every C one step up with the ▶ button
        changed = True
        for el in caps:
            r = [el2["name"] for el2 in d.elements].index(el["name"]) + 1
            btn = [w for w in app.aframe.grid_slaves(row=r, column=5)][0]
            before = el["adjusted"]
            btn.invoke()
            expect = bc.step_series(before, el["series"], 1)
            changed &= el["adjusted"] == expect and el["adjusted"] > before
        res["step_up_all_C"] = changed
        f = [d.fl, d.f0, d.fh]
        res["response_changes"] = bc.response_db(d, f, "adjusted") != bc.response_db(d, f, True)
        res["plot_has_adjusted_curve"] = app.plot.adjusted is not None
        res["table_shows_adjusted"] = all(app.tree.item(i)["values"][5] != "―"
                                          for i in app.tree.get_children()
                                          if app.tree.item(i)["values"][0] in [c["name"] for c in caps])
        # 2) reset
        app.reset_all()
        res["reset"] = (not bc.is_adjusted(d)) and app.plot.adjusted is None
        # 3) Up key in the entry, ◀ button, typed value, wheel on the circuit label
        el = d.elements[0]
        ent = app.adj_rows[el["name"]]["entry"]
        ent.focus_force()
        root.update()
        before = el["adjusted"]
        ent.event_generate("<Up>", when="now")
        root.update()
        res["up_key"] = el["adjusted"] > before
        btn = app.aframe.grid_slaves(row=1, column=3)[0]
        before = el["adjusted"]
        btn.invoke()
        res["left_button"] = el["adjusted"] < before
        u = "H" if el["kind"] == "L" else "F"
        app.adj_rows[el["name"]]["var"].set("330p" if u == "F" else "1.2u")
        ent.event_generate("<Return>", when="now")
        root.update()
        res["typed_value"] = abs(el["adjusted"] - (330e-12 if u == "F" else 1.2e-6)) < 1e-18
        el2 = d.elements[1]
        before = el2["adjusted"]
        app.circuit.on_wheel(el2["name"], -1)
        res["wheel_on_circuit"] = el2["adjusted"] < before
        app.reset_all()
        res["ok"] = all(v for k, v in res.items())
    except Exception as ex:  # noqa: BLE001
        res["error"] = repr(ex)
        res["ok"] = False
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=1)
    root.destroy()


def _shot(root, path):
    """Test hook: write the window rectangle for an external screen grab,
    then quit after a few seconds."""
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    with open(path, "w") as f:
        f.write("%d %d %d %d" % (root.winfo_rootx(), root.winfo_rooty(),
                                 root.winfo_width(), root.winfo_height()))
    root.after(4000, root.destroy)


if __name__ == "__main__":
    main()
