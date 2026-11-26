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
print("FAIL" if fails else "ALL OK", fails)
sys.exit(1 if fails else 0)
