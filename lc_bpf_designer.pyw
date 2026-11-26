"""LC BPF 設計ツール（GUI）。Python 3 標準ライブラリ（tkinter）のみで動作。

起動: run.bat をダブルクリック（または本ファイルをダブルクリック）
"""
from __future__ import annotations

import csv
import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lcbpf_core as core  # noqa: E402

UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
KINDS = {"バタワース": "butterworth", "チェビシェフ": "chebyshev", "エリプティック": "elliptic"}
COL_IDEAL = "#1f5fbf"
COL_E = "#e0662b"
COL_ADJ = "#1a9a4a"
COL_DEV = "#c62828"
FONT = ("Yu Gothic UI", 9)
FONT_S = ("Yu Gothic UI", 8)


def parse_float(s: str, name: str) -> float:
    try:
        return float(s.strip().replace(",", ""))
    except ValueError:
        raise ValueError(f"{name} の値が数値ではありません: {s!r}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LC BPF 設計ツール")
        self.geometry("1360x860")
        self.minsize(1000, 640)
        self.design = None
        self.adj_rows = {}
        self.v_show_ideal = tk.BooleanVar(value=True)
        self._build_inputs()
        self._build_outputs()

    # ------------------------------------------------------------------ 入力
    def _build_inputs(self):
        f = ttk.Frame(self, padding=8)
        f.pack(side=tk.LEFT, fill=tk.Y)
        r = 0

        def row(label, widget, unit=None):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            widget.grid(row=r, column=1, sticky="we", pady=3)
            if unit is not None:
                unit.grid(row=r, column=2, sticky="w", padx=(4, 0))
            r += 1

        self.v_fl = tk.StringVar(value="1")
        self.v_fh = tk.StringVar(value="2")
        self.v_unit = tk.StringVar(value="MHz")
        row("カットオフ周波数 fL", ttk.Entry(f, textvariable=self.v_fl, width=12),
            ttk.Combobox(f, textvariable=self.v_unit, values=list(UNITS), width=5, state="readonly"))
        row("カットオフ周波数 fH", ttk.Entry(f, textvariable=self.v_fh, width=12),
            ttk.Label(f, textvariable=self.v_unit))
        self.v_n = tk.StringVar(value="5")
        row("次数（1～20）", ttk.Spinbox(f, from_=1, to=20, textvariable=self.v_n, width=10))
        self.v_kind = tk.StringVar(value="バタワース")
        cb = ttk.Combobox(f, textvariable=self.v_kind, values=list(KINDS), state="readonly", width=12)
        cb.bind("<<ComboboxSelected>>", lambda e: self._update_param_state())
        row("フィルタ特性", cb)
        self.v_el = tk.StringVar(value="E12")
        self.v_ec = tk.StringVar(value="E12")
        row("L の E 系列", ttk.Combobox(f, textvariable=self.v_el, values=list(core.E_SERIES), state="readonly", width=8))
        row("C の E 系列", ttk.Combobox(f, textvariable=self.v_ec, values=list(core.E_SERIES), state="readonly", width=8))
        self.v_r0 = tk.StringVar(value="50")
        row("入出力インピーダンス", ttk.Entry(f, textvariable=self.v_r0, width=12), ttk.Label(f, text="Ω"))
        self.v_rip = tk.StringVar(value="0.5")
        self.e_rip = ttk.Entry(f, textvariable=self.v_rip, width=12)
        row("リップル", self.e_rip, ttk.Label(f, text="dB"))
        self.v_as = tk.StringVar(value="40")
        self.e_as = ttk.Entry(f, textvariable=self.v_as, width=12)
        row("阻止域減衰量", self.e_as, ttk.Label(f, text="dB"))
        ttk.Label(f, text="（リップルはチェビシェフ／エリプティック、\n 阻止域減衰量はエリプティックで使用）",
                  font=FONT_S, foreground="#666").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Button(f, text="設計する", command=self.run_design).grid(row=r, column=0, columnspan=3, sticky="we", pady=(10, 4))
        r += 1
        ttk.Button(f, text="素子値を CSV 保存", command=self.save_csv).grid(row=r, column=0, columnspan=3, sticky="we")
        r += 1
        self.info = tk.Text(f, width=40, height=22, font=FONT, wrap="word", relief="flat", background="#f4f4f4")
        self.info.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        f.rowconfigure(r, weight=1)
        self._update_param_state()

    def _update_param_state(self):
        kind = KINDS[self.v_kind.get()]
        self.e_rip.configure(state="normal" if kind in ("chebyshev", "elliptic") else "disabled")
        self.e_as.configure(state="normal" if kind == "elliptic" else "disabled")

    # ------------------------------------------------------------------ 出力
    def _build_outputs(self):
        nb = ttk.Notebook(self)
        nb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8), pady=8)
        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="回路図・周波数特性")
        hp = ttk.PanedWindow(tab1, orient=tk.HORIZONTAL)
        hp.pack(fill=tk.BOTH, expand=True)
        pw = ttk.PanedWindow(hp, orient=tk.VERTICAL)
        hp.add(pw, weight=1)
        hp.add(self._build_adjust_panel(hp), weight=0)
        cf = ttk.Frame(pw)
        self.cv = tk.Canvas(cf, background="white", height=320)
        hs = ttk.Scrollbar(cf, orient=tk.HORIZONTAL, command=self.cv.xview)
        self.cv.configure(xscrollcommand=hs.set)
        self.cv.pack(fill=tk.BOTH, expand=True)
        self._cv_keys = {}
        self.cv.bind("<MouseWheel>", self._cv_wheel)
        hs.pack(fill=tk.X)
        pw.add(cf, weight=1)
        pf = ttk.Frame(pw)
        self.pl = tk.Canvas(pf, background="white", height=380)
        self.pl.pack(fill=tk.BOTH, expand=True)
        self.pl.bind("<Configure>", lambda e: self.draw_plot())
        self.pl.bind("<Motion>", self._plot_hover)
        pw.add(pf, weight=1)

        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="素子一覧")
        cols = ("arm", "pos", "elem", "ideal", "eser", "err", "adj", "dev")
        self.tv = ttk.Treeview(tab2, columns=cols, show="headings")
        for c, t, w in zip(cols, ("段", "配置", "素子", "理想値", "E 系列値（設計値）", "理想値からの誤差", "調整値", "設計値からのずれ"),
                           (40, 230, 60, 110, 130, 110, 110, 110)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ 素子値の調整
    def _build_adjust_panel(self, parent):
        outer = ttk.Frame(parent, padding=(6, 4))
        ttk.Label(outer, text="素子値の調整", font=("Yu Gothic UI", 10, "bold")).pack(anchor="w")
        ttk.Label(outer, font=FONT_S, foreground="#555", justify="left",
                  text="◀ ▶: E 系列の前後の標準数へ（L/C それぞれ選択中の系列）\n"
                       "入力欄: 任意値を入力して Enter（例 330p, 1.2u, 4.7e-9）\n"
                       "　　　 ↑↓ キーでもステップ変更\n"
                       "回路図: 素子名クリックで選択、ホイールでステップ").pack(anchor="w", pady=(2, 4))
        bf = ttk.Frame(outer)
        bf.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(bf, text="すべて設計値に戻す", command=self.reset_all).pack(side=tk.LEFT)
        ttk.Checkbutton(bf, text="理想値の曲線", variable=self.v_show_ideal,
                        command=self.draw_plot).pack(side=tk.LEFT, padx=(8, 0))
        self.v_adj_status = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.v_adj_status, font=FONT_S, foreground=COL_DEV).pack(anchor="w")
        wrap = ttk.Frame(outer)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.adj_cv = tk.Canvas(wrap, width=340, highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.adj_cv.yview)
        self.adj_cv.configure(yscrollcommand=vs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.adj_cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.adj_fr = ttk.Frame(self.adj_cv)
        self.adj_cv.create_window(0, 0, window=self.adj_fr, anchor="nw")
        self.adj_fr.bind("<Configure>", lambda e: self.adj_cv.configure(scrollregion=self.adj_cv.bbox("all")))
        return outer

    def _elem_get(self, key):
        """(調整値, 設計値=E 系列値)"""
        ai, t, k = key
        a = self.design.arms[ai]
        return (a.La if t == "L" else a.Ca)[k], (a.Le if t == "L" else a.Ce)[k]

    def _elem_set(self, key, v):
        ai, t, k = key
        a = self.design.arms[ai]
        (a.La if t == "L" else a.Ca)[k] = v

    def _build_adjust_rows(self):
        for w in self.adj_fr.winfo_children():
            w.destroy()
        self.adj_rows = {}
        for j, h in enumerate(("素子", "設計値", "", "現在値", "", "ずれ")):
            ttk.Label(self.adj_fr, text=h, font=FONT_S, foreground="#555").grid(row=0, column=j, padx=2)
        for r, (i, pos, name, unit, v, ve, key) in enumerate(self._elem_rows(), 1):
            ttk.Label(self.adj_fr, text=name, width=5).grid(row=r, column=0, sticky="w", padx=2)
            ttk.Label(self.adj_fr, text=core.fmt_eng(ve, unit), width=10, foreground=COL_E).grid(row=r, column=1, sticky="w")
            bdn = ttk.Button(self.adj_fr, text="◀", width=2, command=lambda k=key: self.step_elem(k, -1))
            bdn.grid(row=r, column=2)
            sv = tk.StringVar()
            en = tk.Entry(self.adj_fr, textvariable=sv, width=11)
            en.grid(row=r, column=3, padx=1)
            en.bind("<Return>", lambda e, k=key: self.enter_elem(k))
            en.bind("<KP_Enter>", lambda e, k=key: self.enter_elem(k))
            en.bind("<FocusOut>", lambda e, k=key: self._refresh_row(k))
            en.bind("<Up>", lambda e, k=key: (self.step_elem(k, +1), "break")[1])
            en.bind("<Down>", lambda e, k=key: (self.step_elem(k, -1), "break")[1])
            bup = ttk.Button(self.adj_fr, text="▶", width=2, command=lambda k=key: self.step_elem(k, +1))
            bup.grid(row=r, column=4)
            dv = tk.StringVar()
            dl = tk.Label(self.adj_fr, textvariable=dv, width=8, anchor="e", font=FONT)
            dl.grid(row=r, column=5, padx=2)
            self.adj_rows[key] = dict(name=name, unit=unit, sv=sv, entry=en, dv=dv, dl=dl, up=bup, down=bdn)
            self._refresh_row(key)

    def _refresh_row(self, key):
        row = self.adj_rows.get(key)
        if not row or not self.design:
            return
        va, ve = self._elem_get(key)
        row["sv"].set(core.fmt_eng(va, row["unit"]))
        row["dv"].set("±0 %" if va == ve else f"{(va / ve - 1) * 100:+.1f} %")
        row["dl"].configure(foreground="#777" if va == ve else COL_DEV)
        row["entry"].configure(foreground="black" if va == ve else COL_DEV)

    def step_elem(self, key, steps):
        if not self.design:
            return
        series = self.v_el.get() if key[1] == "L" else self.v_ec.get()
        va, _ = self._elem_get(key)
        self._elem_set(key, core.step_e(va, series, steps))
        self._after_adjust(key)

    def enter_elem(self, key):
        row = self.adj_rows[key]
        try:
            v = core.parse_eng(row["sv"].get(), row["unit"])
        except ValueError as ex:
            messagebox.showerror("入力エラー", f"{row['name']}: {ex}")
            self._refresh_row(key)
            return
        self._elem_set(key, v)
        self._after_adjust(key)

    def reset_all(self):
        if not self.design:
            return
        core.reset_adjust(self.design)
        self._after_adjust(None)

    def select_elem(self, key):
        row = self.adj_rows.get(key)
        if row:
            row["entry"].focus_set()
            row["entry"].select_range(0, tk.END)
            self.update_idletasks()
            h = max(1, self.adj_fr.winfo_height())
            self.adj_cv.yview_moveto(max(0.0, (row["entry"].winfo_y() - 40) / h))

    def n_adjusted(self):
        return sum(1 for k in self.adj_rows if self._elem_get(k)[0] != self._elem_get(k)[1])

    def _after_adjust(self, key):
        self.r_adj = core.response(self.design, self.freqs, use_adj=True)
        for k in (list(self.adj_rows) if key is None else [key]):
            self._refresh_row(k)
        n_adj = self.n_adjusted()
        self.v_adj_status.set(f"調整中の素子: {n_adj} 個" if n_adj else "")
        self.fill_info()
        self.fill_table()
        self.draw_circuit()
        self.draw_plot()

    # ------------------------------------------------------------------ 設計
    def run_design(self):
        try:
            u = UNITS[self.v_unit.get()]
            fl = parse_float(self.v_fl.get(), "fL") * u
            fh = parse_float(self.v_fh.get(), "fH") * u
            if fl <= 0 or fh <= 0:
                raise ValueError("周波数は正の値を入力してください。")
            if not fl < fh:
                raise ValueError("fL < fH となるように入力してください。")
            try:
                n = int(self.v_n.get())
            except ValueError:
                raise ValueError("次数は 1～20 の整数で入力してください。")
            if not 1 <= n <= 20:
                raise ValueError("次数は 1～20 の整数で入力してください。")
            r0 = parse_float(self.v_r0.get(), "インピーダンス")
            rip = parse_float(self.v_rip.get(), "リップル")
            asd = parse_float(self.v_as.get(), "阻止域減衰量")
            kind = KINDS[self.v_kind.get()]
            self.config(cursor="watch")
            self.update_idletasks()
            self.design = core.design_bpf(fl, fh, n, kind, r0, rip, asd, self.v_el.get(), self.v_ec.get())
        except ValueError as ex:
            messagebox.showerror("入力エラー", str(ex))
            return
        finally:
            self.config(cursor="")
        self.params = dict(kind=self.v_kind.get(), n=n, rip=rip, asd=asd, kindkey=kind)
        self._compute_curves()
        self._build_adjust_rows()
        self.v_adj_status.set("")
        self.fill_info()
        self.fill_table()
        self.draw_circuit()
        self.draw_plot()

    def _compute_curves(self):
        d = self.design
        span = max(3.0, (d.fh / d.fl) ** 1.5)
        self.fmin, self.fmax = d.f0 / span, d.f0 * span
        self.freqs = core.log_freqs(self.fmin, self.fmax, 1201)
        self.r_ideal = core.response(d, self.freqs, use_e=False)
        self.r_e = core.response(d, self.freqs, use_e=True)
        self.r_adj = core.response(d, self.freqs, use_adj=True)

    def _elem_rows(self):
        """(段, 配置, 素子名, 単位, 理想値, E 系列値, key)。key = (腕 index, 'L'/'C', 腕内 index)"""
        rows = []
        for ai, a in enumerate(self.design.arms):
            i = ai + 1
            if a.kind in ("series_LC", "shunt_LC"):
                pos = "直列腕・直列共振" if a.kind == "series_LC" else "並列腕・並列共振"
                rows += [(i, pos, f"L{i}", "H", a.L[0], a.Le[0], (ai, "L", 0)),
                         (i, pos, f"C{i}", "F", a.C[0], a.Ce[0], (ai, "C", 0))]
            else:
                rows += [(i, "直列腕・(直列LC)∥L∥C の直列枝", f"L{i}a", "H", a.L[0], a.Le[0], (ai, "L", 0)),
                         (i, "直列腕・(直列LC)∥L∥C の直列枝", f"C{i}a", "F", a.C[0], a.Ce[0], (ai, "C", 0)),
                         (i, "直列腕・(直列LC)∥L∥C の並列L", f"L{i}b", "H", a.L[1], a.Le[1], (ai, "L", 1)),
                         (i, "直列腕・(直列LC)∥L∥C の並列C", f"C{i}b", "F", a.C[1], a.Ce[1], (ai, "C", 1))]
        return rows

    def fill_table(self):
        self.tv.delete(*self.tv.get_children())
        for i, pos, name, unit, v, ve, key in self._elem_rows():
            va = self._elem_get(key)[0]
            self.tv.insert("", tk.END, values=(i, pos, name, core.fmt_eng(v, unit), core.fmt_eng(ve, unit),
                                               f"{(ve / v - 1) * 100:+.1f} %", core.fmt_eng(va, unit),
                                               "±0 %" if va == ve else f"{(va / ve - 1) * 100:+.1f} %"))

    def fill_info(self):
        d, p = self.design, self.params
        t = self.info
        t.configure(state="normal")
        t.delete("1.0", tk.END)
        lines = [
            f"特性: {p['kind']}  {p['n']} 次（素子 {sum(len(a.L) + len(a.C) for a in d.arms)} 個）",
            f"fL = {core.fmt_eng(d.fl, 'Hz')}, fH = {core.fmt_eng(d.fh, 'Hz')}",
            f"中心周波数 f0 = √(fL·fH) = {core.fmt_eng(d.f0, 'Hz')}",
            f"帯域幅 BW = {core.fmt_eng(d.fh - d.fl, 'Hz')}（比帯域 {(d.fh - d.fl) / d.f0 * 100:.1f} %）",
            f"信号源 R = {d.r0:g} Ω、負荷 R = {d.rl:.4g} Ω",
        ]
        if p["kindkey"] == "butterworth":
            lines.append("fL/fH は −3 dB 点です。")
        else:
            lines.append(f"fL/fH はリップル {p['rip']:g} dB の通過域端です。")
        if p["kindkey"] == "elliptic" and d.fs_lo:
            lines.append(f"阻止域減衰量 {p['asd']:g} dB の阻止域端: {core.fmt_eng(d.fs_lo, 'Hz')} 以下 / "
                         f"{core.fmt_eng(d.fs_hi, 'Hz')} 以上")
        if d.note:
            lines.append("注: " + d.note)
        lines.append("")
        lines.append("低域プロトタイプ（正規化値）:")
        gi = 1
        for e in d.proto.elements:
            if e[0] == "LCtank":
                lines.append(f"  g{gi}: 直列腕タンク L={e[1]:.5f}, C={e[2]:.5f}")
            else:
                lines.append(f"  g{gi} = {e[1]:.5f}（{'シャント C' if e[0] == 'C' else '直列 L'}）")
            gi += 1
        lines.append(f"  負荷 = {d.proto.rl:.5f}")
        # E 系列の影響
        pb = [(f, a, b) for f, a, b in zip(self.freqs, self.r_ideal, self.r_e) if d.fl <= f <= d.fh]
        if pb:
            lines.append("")
            lines.append("通過域内の挿入損失（最大）: 理想 %.2f dB / E系列 %.2f dB"
                         % (-min(x[1] for x in pb), -min(x[2] for x in pb)))
            if core.is_adjusted(d):
                pa = [b for f, b in zip(self.freqs, self.r_adj) if d.fl <= f <= d.fh]
                dmax = max(abs(a - b) for a, b in zip(self.r_e, self.r_adj))
                lines.append("　調整後: %.2f dB（設計値の特性との差 最大 %.2f dB）" % (-min(pa), dmax))
        t.insert("1.0", "\n".join(lines))
        t.configure(state="disabled")

    def save_csv(self):
        if not self.design:
            messagebox.showinfo("CSV 保存", "先に設計してください。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="lc_bpf_elements.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(["段", "配置", "素子", "単位", "理想値", "E系列値", "調整値", "設計値からのずれ[%]"])
            for i, pos, name, unit, v, ve, key in self._elem_rows():
                va = self._elem_get(key)[0]
                w.writerow([i, pos, name, unit, f"{v:.6e}", f"{ve:.6e}", f"{va:.6e}", f"{(va / ve - 1) * 100:+.2f}"])

    # ------------------------------------------------------------------ 回路図
    def _ind_h(self, x0, x1, y):
        """水平インダクタ（x0→x1）"""
        c = self.cv
        n = 4
        w = (x1 - x0) / n
        for i in range(n):
            c.create_arc(x0 + i * w, y - w / 2, x0 + (i + 1) * w, y + w / 2, start=0, extent=180, style="arc", width=1.5)

    def _ind_v(self, x, y0, y1):
        c = self.cv
        n = 4
        h = (y1 - y0) / n
        for i in range(n):
            c.create_arc(x - h / 2, y0 + i * h, x + h / 2, y0 + (i + 1) * h, start=90, extent=-180, style="arc", width=1.5)

    def _cap_h(self, xm, y):
        c = self.cv
        c.create_line(xm - 4, y - 10, xm - 4, y + 10, width=2)
        c.create_line(xm + 4, y - 10, xm + 4, y + 10, width=2)

    def _cap_v(self, x, ym):
        c = self.cv
        c.create_line(x - 10, ym - 4, x + 10, ym - 4, width=2)
        c.create_line(x - 10, ym + 4, x + 10, ym + 4, width=2)

    def _res_v(self, x, y0, y1):
        c = self.cv
        pts = [x, y0]
        n = 6
        h = (y1 - y0) / n
        for i in range(n):
            pts += [x + (7 if i % 2 == 0 else -7), y0 + (i + 0.5) * h]
        pts += [x, y1]
        c.create_line(*pts, width=1.5)

    def _lbl(self, x, y, name, v, ve, unit, anchor="n", key=None):
        c = self.cv
        tag = f"el_{key[0]}_{key[1]}_{key[2]}" if key else ""
        tags = ("elem", tag) if key else ()
        c.create_text(x, y, text=f"{name}  {core.fmt_eng(v, unit)}", font=FONT_S, fill=COL_IDEAL, anchor=anchor, tags=tags)
        c.create_text(x, y + 12, text=f"({core.fmt_eng(ve, unit)})", font=FONT_S, fill=COL_E, anchor=anchor, tags=tags)
        if key:
            va = self._elem_get(key)[0]
            if va != ve:
                c.create_text(x, y + 24, text=f"→{core.fmt_eng(va, unit)} {(va / ve - 1) * 100:+.1f}%",
                              font=FONT_S, fill=COL_DEV, anchor=anchor, tags=tags)
            c.tag_bind(tag, "<Button-1>", lambda e, k=key: self.select_elem(k))
            self._cv_keys[tag] = key
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))

    def draw_circuit(self):
        c = self.cv
        c.delete("all")
        self._cv_keys = {}
        d = self.design
        if not d:
            return
        yt, yb = 150, 270
        x = 20
        # 信号源
        c.create_oval(x, (yt + yb) / 2 - 15, x + 30, (yt + yb) / 2 + 15, width=1.5)
        c.create_text(x + 15, (yt + yb) / 2, text="~")
        c.create_line(x + 15, yt, x + 15, (yt + yb) / 2 - 15, width=1.5)
        c.create_line(x + 15, yb, x + 15, (yt + yb) / 2 + 15, width=1.5)
        c.create_line(x + 15, yt, x + 30, yt, width=1.5)
        x += 30
        # 信号源抵抗（水平）
        pts = [x, yt]
        for i in range(6):
            pts += [x + 5 + i * 6, yt + (7 if i % 2 == 0 else -7)]
        pts += [x + 40, yt]
        c.create_line(*pts, width=1.5)
        c.create_text(x + 20, yt - 22, text=f"Rs {d.r0:g} Ω", font=FONT_S)
        x += 40
        xs = 35
        for i, a in enumerate(d.arms, 1):
            if a.kind == "series_LC":
                w = 150
                c.create_line(x, yt, x + 20, yt, width=1.5)
                self._ind_h(x + 20, x + 70, yt)
                c.create_line(x + 70, yt, x + 96, yt, width=1.5)
                self._cap_h(x + 100, yt)
                c.create_line(x + 104, yt, x + w, yt, width=1.5)
                self._lbl(x + 45, yt - 56, f"L{i}", a.L[0], a.Le[0], "H", key=(i - 1, "L", 0))
                self._lbl(x + 105, yt + 16, f"C{i}", a.C[0], a.Ce[0], "F", key=(i - 1, "C", 0))
            elif a.kind == "shunt_LC":
                w = 150
                c.create_line(x, yt, x + w, yt, width=1.5)
                xl, xc = x + 45, x + 105
                c.create_oval(xl - 2, yt - 2, xl + 2, yt + 2, fill="black")
                c.create_oval(xc - 2, yt - 2, xc + 2, yt + 2, fill="black")
                c.create_line(xl, yt, xl, yt + 30, width=1.5)
                self._ind_v(xl, yt + 30, yt + 90)
                c.create_line(xl, yt + 90, xl, yb, width=1.5)
                c.create_line(xc, yt, xc, yt + 56, width=1.5)
                self._cap_v(xc, yt + 60)
                c.create_line(xc, yt + 64, xc, yb, width=1.5)
                c.create_oval(xl - 2, yb - 2, xl + 2, yb + 2, fill="black")
                c.create_oval(xc - 2, yb - 2, xc + 2, yb + 2, fill="black")
                self._lbl(xl - 6, yb + 8, f"L{i}", a.L[0], a.Le[0], "H", anchor="ne", key=(i - 1, "L", 0))
                self._lbl(xc + 6, yb + 8, f"C{i}", a.C[0], a.Ce[0], "F", anchor="nw", key=(i - 1, "C", 0))
            else:
                w = 200
                xa, xb = x + 20, x + w - 20
                c.create_line(x, yt, xa, yt, width=1.5)
                c.create_line(xb, yt, x + w, yt, width=1.5)
                c.create_line(xa, yt - 80, xa, yt, width=1.5)
                c.create_line(xb, yt - 80, xb, yt, width=1.5)
                # 枝1（最上段）: 直列 L-C
                y1 = yt - 80
                c.create_line(xa, y1, xa + 20, y1, width=1.5)
                self._ind_h(xa + 20, xa + 70, y1)
                c.create_line(xa + 70, y1, xa + 106, y1, width=1.5)
                self._cap_h(xa + 110, y1)
                c.create_line(xa + 114, y1, xb, y1, width=1.5)
                # 枝2: L
                y2 = yt - 40
                c.create_line(xa, y2, xa + 55, y2, width=1.5)
                self._ind_h(xa + 55, xa + 105, y2)
                c.create_line(xa + 105, y2, xb, y2, width=1.5)
                # 枝3（レール上）: C
                c.create_line(xa, yt, xa + 76, yt, width=1.5)
                self._cap_h(xa + 80, yt)
                c.create_line(xa + 84, yt, xb, yt, width=1.5)
                self._lbl(xa + 45, y1 - 46, f"L{i}a", a.L[0], a.Le[0], "H", key=(i - 1, "L", 0))
                self._lbl(xa + 125, y1 - 46, f"C{i}a", a.C[0], a.Ce[0], "F", key=(i - 1, "C", 0))
                self._lbl(xa + 80, yt + 16, f"L{i}b", a.L[1], a.Le[1], "H", key=(i - 1, "L", 1))
                self._lbl(xa + 80, yt + 56, f"C{i}b", a.C[1], a.Ce[1], "F", key=(i - 1, "C", 1))
            x += w
        # 負荷
        c.create_line(x, yt, x + 30, yt, width=1.5)
        c.create_line(x + 30, yt, x + 30, yt + 40, width=1.5)
        self._res_v(x + 30, yt + 40, yt + 90)
        c.create_line(x + 30, yt + 90, x + 30, yb, width=1.5)
        c.create_text(x + 40, yt + 65, text=f"RL {d.rl:.4g} Ω", font=FONT_S, anchor="w")
        x += 30
        c.create_line(xs, yb, x, yb, width=1.5)
        c.create_text(20, 12, anchor="nw", font=FONT,
                      text="青: 理想値　橙(括弧内): E 系列値＝設計値（L: %s / C: %s）　赤(→): 調整値と設計値からのずれ"
                      % (self.v_el.get(), self.v_ec.get()))
        c.configure(scrollregion=(0, 0, x + 120, yb + 50))

    # ------------------------------------------------------------------ 周波数特性
    def draw_plot(self):
        c = self.pl
        c.delete("all")
        if not self.design:
            c.create_text(20, 20, anchor="nw", text="左の入力欄を設定して「設計する」を押してください。", font=FONT)
            return
        W, H = c.winfo_width(), c.winfo_height()
        ml, mr, mt, mb = 60, 20, 44, 40
        self._pa = (ml, mt, W - mr, H - mb)
        lo = min(min(self.r_ideal), min(self.r_e), min(self.r_adj))
        ymin = max(-120.0, math.floor(lo / 10) * 10)
        ymin = min(ymin, -60.0)
        ymax = 5.0
        lx0, lx1 = math.log10(self.fmin), math.log10(self.fmax)
        self._ax = (lx0, lx1, ymin, ymax)

        def X(f):
            return ml + (math.log10(f) - lx0) / (lx1 - lx0) * (W - ml - mr)

        def Y(db):
            db = max(ymin, min(ymax, db))
            return mt + (ymax - db) / (ymax - ymin) * (H - mt - mb)

        # グリッド
        for dec in range(math.floor(lx0), math.ceil(lx1) + 1):
            for m in range(1, 10):
                f = m * 10 ** dec
                if self.fmin <= f <= self.fmax:
                    c.create_line(X(f), mt, X(f), H - mb, fill="#ddd" if m != 1 else "#bbb")
                    if m in (1, 2, 5):
                        c.create_text(X(f), H - mb + 4, text=core.fmt_eng(f, "Hz").replace(" ", ""), anchor="n", font=FONT_S)
        step = 10 if ymax - ymin <= 80 else 20
        db = 0
        while db >= ymin:
            c.create_line(ml, Y(db), W - mr, Y(db), fill="#bbb" if db == 0 else "#ddd")
            c.create_text(ml - 4, Y(db), text=f"{db}", anchor="e", font=FONT_S)
            db -= step
        c.create_rectangle(ml, mt, W - mr, H - mb, outline="#888")
        c.create_text(14, (mt + H - mb) / 2, text="|S21| [dB]", angle=90, font=FONT_S)
        c.create_text((ml + W - mr) / 2, H - 14, text="周波数（対数軸）", font=FONT_S)
        d = self.design
        for f, lab in ((d.fl, "fL"), (d.fh, "fH")):
            c.create_line(X(f), mt, X(f), H - mb, fill="#5a5", dash=(4, 3))
            c.create_text(X(f) + 3, mt + 3, text=lab, anchor="nw", fill="#383", font=FONT_S)
        if self.params["kindkey"] == "elliptic" and d.fs_lo:
            a = -self.params["asd"]
            c.create_line(ml, Y(a), X(d.fs_lo), Y(a), fill="#a5a", dash=(2, 3))
            c.create_line(X(d.fs_hi), Y(a), W - mr, Y(a), fill="#a5a", dash=(2, 3))
        # 曲線
        adjusted = core.is_adjusted(d)
        curves = []
        if self.v_show_ideal.get():
            curves.append((self.r_ideal, COL_IDEAL, None, 1.5))
        if adjusted:
            curves.append((self.r_adj, COL_ADJ, None, 2.5))
        curves.append((self.r_e, COL_E, (6, 3), 2))
        for data, col, dash, width in curves:
            pts = []
            for f, v in zip(self.freqs, data):
                pts += [X(f), Y(v)]
            c.create_line(*pts, fill=col, width=width, dash=dash)
        # 凡例
        # 凡例（プロット枠の外、タイトルの下の1行）
        lx, ly = ml, mt - 12
        items = []
        if self.v_show_ideal.get():
            items.append((COL_IDEAL, None, 2, "理想値"))
        items.append((COL_E, (6, 3), 2, f"設計値＝E 系列（L:{self.v_el.get()} C:{self.v_ec.get()}）"))
        items.append((COL_ADJ, None, 2.5, "調整後" + ("" if adjusted else "（未調整：設計値と同じ）")))
        for col, dash, width, label in items:
            c.create_line(lx, ly, lx + 28, ly, fill=col, width=width, dash=dash)
            t = c.create_text(lx + 33, ly, text=label, anchor="w", font=FONT_S)
            lx = c.bbox(t)[2] + 18
        c.create_text(ml, 4, anchor="nw", font=FONT, text="振幅特性 |S21|（Rs・RL 終端の伝達利得）")
        self._hover_id = None

    def _cv_wheel(self, ev):
        """回路図の素子ラベル上でホイール → その素子を 1 ステップ変更"""
        for item in self.cv.find_withtag("current"):
            for t in self.cv.gettags(item):
                if t in self._cv_keys:
                    self.step_elem(self._cv_keys[t], 1 if ev.delta > 0 else -1)
                    return "break"

    def _key_by_name(self, name):
        for row in self._elem_rows():
            if row[2] == name:
                return row[6]
        raise KeyError(name)

    def apply_adjust_spec(self, spec):
        for item in spec.split(","):
            name, st = item.split("=")
            self.step_elem(self._key_by_name(name.strip()), int(st))

    def autocheck(self, out):
        import json
        rep = {"ok": False}
        try:
            d = self.design
            rows = self._elem_rows()
            caps = [r for r in rows if r[3] == "F"]
            base = list(self.r_adj)
            rep["design"] = dict(fl=d.fl, fh=d.fh, n=len(d.arms), L_series=self.v_el.get(), C_series=self.v_ec.get(), r0=d.r0)
            rep["base_equals_design"] = base == self.r_e
            checks = []
            for r in caps:
                key, name = r[6], r[2]
                ve = r[5]
                self.adj_rows[key]["up"].invoke()  # ▶ ボタン = 1 ステップ増
                self.update()
                va = self._elem_get(key)[0]
                diff = max(abs(a - b) for a, b in zip(self.r_adj, self.r_e))
                shown = self.adj_rows[key]["dv"].get()
                self.reset_all()
                self.update()
                back = self._elem_get(key)[0] == ve and self.r_adj == self.r_e and not core.is_adjusted(d)
                checks.append(dict(name=name, design=ve, stepped=va, shown_dev=shown,
                                   max_dB_change=round(diff, 4), reset_ok=back))
            # ▶ ボタン相当と任意値入力
            k0 = caps[0][6]
            self.adj_rows[k0]["down"].invoke()
            down = self._elem_get(k0)[0]
            self.reset_all()
            en = self.adj_rows[k0]["entry"]
            self.focus_force()
            en.focus_force()
            self.update()
            en.event_generate("<Up>")
            self.update()
            rep["C1_up_key"] = self._elem_get(k0)[0]
            self.adj_rows[k0]["sv"].set("123.4p")
            en.event_generate("<Return>")
            self.update()
            typed = self._elem_get(k0)[0]
            self.adj_rows[k0]["up"].invoke()
            typed_up = self._elem_get(k0)[0]
            lk = [r for r in rows if r[3] == "H"][0][6]
            self.adj_rows[lk]["up"].invoke()
            rep["L1_step_up"] = [self._elem_get(lk)[1], self._elem_get(lk)[0]]
            rep["circuit_shows_adjusted"] = any("→" in self.cv.itemcget(i, "text") for i in self.cv.find_withtag("elem"))
            rep["table_dev_col"] = [self.tv.item(i, "values")[7] for i in self.tv.get_children()]
            n_adj = self.n_adjusted()
            self.reset_all()
            rep["C1_step_down"] = down
            rep["C1_typed_123.4p"] = typed
            rep["C1_typed_then_step_up"] = typed_up
            rep["n_adjusted_before_reset"] = n_adj
            rep["after_reset_adjusted"] = core.is_adjusted(d)
            rep["caps"] = checks
            rep["ok"] = (rep["base_equals_design"] and all(c["max_dB_change"] > 0.01 and c["reset_ok"] for c in checks)
                         and abs(typed - 123.4e-12) < 1e-18 and not core.is_adjusted(d))
        except Exception as ex:  # noqa: BLE001
            rep["error"] = repr(ex)
        with open(out, "w", encoding="utf-8") as fp:
            json.dump(rep, fp, ensure_ascii=False, indent=1)
        self.destroy()

    def _plot_hover(self, ev):
        if not self.design or not hasattr(self, "_pa"):
            return
        x0, y0, x1, y1 = self._pa
        c = self.pl
        c.delete("hover")
        if not (x0 <= ev.x <= x1 and y0 <= ev.y <= y1):
            return
        lx0, lx1, _, _ = self._ax
        f = 10 ** (lx0 + (ev.x - x0) / (x1 - x0) * (lx1 - lx0))
        a = core.response(self.design, [f])[0]
        b = core.response(self.design, [f], use_e=True)[0]
        txt = f"{core.fmt_eng(f, 'Hz')}\n理想 {a:.2f} dB / 設計値 {b:.2f} dB"
        if core.is_adjusted(self.design):
            g = core.response(self.design, [f], use_adj=True)[0]
            txt += f"\n調整後 {g:.2f} dB（{g - b:+.2f} dB）"
        c.create_line(ev.x, y0, ev.x, y1, fill="#999", tags="hover")
        c.create_text(ev.x + 6, y1 - 6, anchor="sw", font=FONT_S, tags="hover", text=txt)


def main():
    app = App()
    if "--demo" in sys.argv:  # 動作確認用: --demo [butterworth|chebyshev|elliptic] [次数]
        rest = sys.argv[sys.argv.index("--demo") + 1:]
        inv = {v: k for k, v in KINDS.items()}
        if rest and rest[0] in inv:
            app.v_kind.set(inv[rest[0]])
            app._update_param_state()
        if len(rest) > 1:
            app.v_n.set(rest[1])
        app.after(300, app.run_design)
    if "--adjust" in sys.argv:  # 例: --adjust C1=+1,L3=-2（--demo と併用、スクリーンショット用）
        spec = sys.argv[sys.argv.index("--adjust") + 1]
        app.after(900, lambda: app.apply_adjust_spec(spec))
    if "--autocheck" in sys.argv:  # 動作確認: 調整→リセットを GUI のハンドラ経由で行い結果を書き出して終了
        out = sys.argv[sys.argv.index("--autocheck") + 1]
        app.after(1500, lambda: app.autocheck(out))
    app.mainloop()


if __name__ == "__main__":
    main()
