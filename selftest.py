"""計算部の自己テスト: python selftest.py

3 特性 × 1～20 次（fL=1 MHz, fH=2 MHz, 50 Ω, リップル 0.5 dB, 阻止域 40 dB）を設計し、
梯子回路の計算特性が仕様を満たすか確認する。
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lcbpf_core as c  # noqa: E402

fails = 0
for kind in ("butterworth", "chebyshev", "elliptic"):
    for n in range(1, 21):
        try:
            d = c.design_bpf(1e6, 2e6, n, kind, 50, 0.5, 40)
            edge = c.response(d, [d.fl, d.fh])
            fr = c.log_freqs(d.fl, d.fh, 401)
            pb = min(c.response(d, fr))
            want = -3.0103 if kind == "butterworth" else -0.5
            ok = all(abs(e - want) < 0.01 for e in edge) and pb > want - 0.01
            if kind == "elliptic" and d.fs_lo:
                sb = c.response(d, c.log_freqs(d.f0 / 50, d.fs_lo, 400) + c.log_freqs(d.fs_hi, d.f0 * 50, 400))
                ok = ok and max(sb) < -40 + 0.01
            print(f"{kind:12s} n={n:2d} {'OK ' if ok else 'NG '} edge={edge[0]:.3f}/{edge[1]:.3f} dB  RL={d.rl:.2f}")
            fails += not ok
        except Exception as ex:  # noqa: BLE001
            print(f"{kind:12s} n={n:2d} ERR {ex}")
            fails += 1

# 素子値の調整（E 系列ステップ・任意値入力・リセット）
def chk(name, cond):
    global fails
    print(f"adjust: {name:40s} {'OK' if cond else 'NG'}")
    fails += not cond


chk("step_e E12 330p +1 -> 390p", c.step_e(330e-12, "E12", 1) == 390e-12)
chk("step_e E12 330p -1 -> 270p", c.step_e(330e-12, "E12", -1) == 270e-12)
chk("step_e E12 decade 8.2n +1 -> 10n", c.step_e(8.2e-9, "E12", 1) == 10e-9)
chk("step_e E12 decade 1n -1 -> 820p", c.step_e(1e-9, "E12", -1) == 820e-12)
chk("step_e off-series 123.4p +1 -> 150p", c.step_e(123.4e-12, "E12", 1) == 150e-12)
chk("step_e off-series 123.4p -1 -> 120p", c.step_e(123.4e-12, "E12", -1) == 120e-12)
chk("step_e E96 1.02u +1 -> 1.05u", c.step_e(1.02e-6, "E96", 1) == 1.05e-6)
chk("step_e +2 == +1 twice", c.step_e(330e-12, "E24", 2) == c.step_e(c.step_e(330e-12, "E24", 1), "E24", 1))
chk("parse_eng 330p / 330 pF / 4.7e-9",
    c.parse_eng("330p", "F") == 330e-12 and c.parse_eng("330 pF", "F") == 330e-12 and c.parse_eng("4.7e-9", "F") == 4.7e-9)
chk("parse_eng 1.2u / 1.2(micro)H / 10m", abs(c.parse_eng("1.2u", "H") - 1.2e-6) < 1e-18
    and abs(c.parse_eng("1.2µH", "H") - 1.2e-6) < 1e-18 and abs(c.parse_eng("10m", "H") - 0.01) < 1e-15)
for bad in ("", "abc", "-1n", "0"):
    try:
        c.parse_eng(bad, "F")
        chk(f"parse_eng rejects {bad!r}", False)
    except ValueError:
        chk(f"parse_eng rejects {bad!r}", True)
d = c.design_bpf(1e6, 2e6, 5, "butterworth", 50, 0.5, 40, "E12", "E12")
fr = c.log_freqs(d.f0 / 5, d.f0 * 5, 401)
r_e = c.response(d, fr, use_e=True)
chk("adj == design before edit", c.response(d, fr, use_adj=True) == r_e and not c.is_adjusted(d))
d.arms[0].Ca[0] = c.step_e(d.arms[0].Ce[0], "E12", 1)
r_a = c.response(d, fr, use_adj=True)
chk("C1 +1 step changes response (>0.1 dB)", c.is_adjusted(d) and max(abs(x - y) for x, y in zip(r_a, r_e)) > 0.1)
chk("design (E) response unchanged by edit", c.response(d, fr, use_e=True) == r_e)
c.reset_adjust(d)
chk("reset restores design", not c.is_adjusted(d) and c.response(d, fr, use_adj=True) == r_e)

print("FAIL" if fails else "ALL OK", fails)
sys.exit(1 if fails else 0)
