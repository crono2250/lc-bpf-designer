"""Self-check for bpf_core (run: py -3.12 test_core.py).

For every type x order 1..20 x topology, the computed ladder must meet the spec:
  - edge loss at fL/fH = -3.01 dB (Butterworth) or the ripple (others)
  - passband never worse than that, never above 0 dB
  - elliptic: attenuation >= As outside the stopband edges, equal terminations
"""
import math
import sys

import bpf_core as bc


def logspace(a, b, n):
    la, lb = math.log10(a), math.log10(b)
    return [10 ** (la + (lb - la) * i / (n - 1)) for i in range(n)]


def check(d, ftype, ap, As):
    want = -3.0103 if ftype == "バタワース" else -ap
    edge = bc.response_db(d, [d.fl, d.fh])
    pb = bc.response_db(d, logspace(d.fl, d.fh, 401))
    msgs = []
    if any(abs(e - want) > 0.01 for e in edge):
        msgs.append("edge %.3f/%.3f" % tuple(edge))
    if min(pb) < want - 0.01 or max(pb) > 1e-6:
        msgs.append("passband %.3f..%.3f" % (min(pb), max(pb)))
    if ftype == "エリプティック":
        lo, hi = d.stop_edges
        fr = []
        if lo > d.f0 / 1000:
            fr += logspace(d.f0 / 1000, lo, 400)
        if hi < d.f0 * 1000:
            fr += logspace(hi, d.f0 * 1000, 400)
        sb = bc.response_db(d, fr) if fr else [-1e9]
        if max(sb) > -As + 0.01:
            msgs.append("stopband %.2f" % max(sb))
        if abs(d.rl - d.rs) > 1e-6 * d.rs:
            msgs.append("RL %.4f" % d.rl)
        if d.info["synth_err_db"] > 1e-6:
            msgs.append("synth_err %.2g" % d.info["synth_err_db"])
    return msgs


def main():
    fails = 0

    d = bc.design_bpf(1e6, 2e6, 5, "バタワース", 50.0, l_series="E12", c_series="E12")
    print("Example: fL=1MHz fH=2MHz n=5 Butterworth E12 50ohm")
    for el in d.elements:
        u = "H" if el["kind"] == "L" else "F"
        print("  %-4s %-4s ideal=%-12s E12=%s" % (el["name"], el["pos"],
              bc.fmt_eng(el["ideal"], u), bc.fmt_eng(el["rounded"], u, 3)))
    print("  |S21| fL/f0/fH = %.3f / %.3f / %.3f dB" % tuple(bc.response_db(d, [d.fl, d.f0, d.fh])))

    for ftype in bc.TYPES:
        for n in range(1, 21):
            for first in ("shunt", "series"):
                try:
                    d = bc.design_bpf(1e6, 2e6, n, ftype, 50.0, 0.5, 40.0, first=first)
                    msgs = check(d, ftype, 0.5, 40.0)
                except Exception as ex:  # noqa: BLE001
                    msgs = ["exception %r" % ex]
                if msgs:
                    fails += 1
                    print("FAIL %s n=%d %s: %s" % (ftype, n, first, "; ".join(msgs)))
    print("3 types x 1..20 x 2 topologies checked (0.5 dB / 40 dB)")

    # elliptic stress: other ripple / attenuation combinations, narrow band
    cases = [(0.1, 60), (1.0, 80), (0.01, 30), (3.0, 20), (0.5, 100)]
    for ap, As in cases:
        for n in range(1, 21):
            try:
                d = bc.design_bpf(10e6, 10.5e6, n, "エリプティック", 75.0, ap, As)
                msgs = check(d, "エリプティック", ap, As)
            except bc.SynthesisError as ex:
                # accepted only when the transition band has collapsed
                ws = bc.elliptic_function(n, ap, As)["ws"]
                if ws - 1 > 1e-7:
                    msgs = ["synthesis failed with ws-1=%.1e" % (ws - 1)]
                else:
                    msgs = []
                    print("  (expected) %g/%g dB n=%d: transition band ws-1=%.1e" % (ap, As, n, ws - 1))
            except Exception as ex:  # noqa: BLE001
                msgs = ["exception %r" % ex]
            if msgs:
                fails += 1
                print("FAIL elliptic %g/%g dB n=%d: %s" % (ap, As, n, "; ".join(msgs)))
    print("elliptic stress cases checked")

    # E-series rounding
    for ser, v, exp in (("E12", 6.438e-6, 6.8e-6), ("E6", 1.2e-9, 1.0e-9), ("E24", 9.3e-12, 9.1e-12), ("E24", 9.6e-12, 10e-12),
                        ("E96", 1.0e-6, 1.0e-6), ("E12", 9.5e-6, 10e-6)):
        got = bc.round_to_series(v, ser)
        if abs(got / exp - 1) > 1e-9:
            fails += 1
            print("FAIL round %s %g -> %g (expected %g)" % (ser, v, got, exp))

    # per-element adjustment helpers
    cases = [(bc.step_series(6.8e-6, "E12", 1), 8.2e-6), (bc.step_series(6.8e-6, "E12", -2), 4.7e-6),
             (bc.step_series(8.2e-9, "E12", 1), 10e-9), (bc.step_series(1.0e-9, "E6", -1), 680e-12),
             (bc.step_series(7.0e-9, "E12", 1), 8.2e-9), (bc.step_series(7.0e-9, "E12", -1), 6.8e-9),
             (bc.step_series(1.0e-6, "E96", 1), 1.02e-6),
             (bc.parse_eng("330p", "F"), 330e-12), (bc.parse_eng("330 pF", "F"), 330e-12),
             (bc.parse_eng("1.2u", "H"), 1.2e-6), (bc.parse_eng("1.2µH", "H"), 1.2e-6),
             (bc.parse_eng("4.7e-9", "F"), 4.7e-9), (bc.parse_eng("2.2nF", "F"), 2.2e-9)]
    for i, (got, exp) in enumerate(cases):
        if abs(got / exp - 1) > 1e-9:
            fails += 1
            print("FAIL adjust helper case %d: %g (expected %g)" % (i, got, exp))
    for bad in ("", "abc", "-1p", "0"):
        try:
            bc.parse_eng(bad, "F")
            fails += 1
            print("FAIL parse_eng accepted %r" % bad)
        except ValueError:
            pass
    d = bc.design_bpf(1e6, 2e6, 5, "バタワース", 50.0)
    base = bc.response_db(d, [d.fl, d.f0, d.fh], True)
    el = d.elements[4]
    el["adjusted"] = bc.step_series(el["adjusted"], el["series"], 1)
    if not bc.is_adjusted(d) or bc.response_db(d, [d.fl, d.f0, d.fh], "adjusted") == base:
        fails += 1
        print("FAIL adjusted response does not change")
    if bc.response_db(d, [d.fl, d.f0, d.fh], True) != base:
        fails += 1
        print("FAIL design (E-series) response changed by adjustment")
    bc.reset_adjust(d)
    if bc.is_adjusted(d) or bc.response_db(d, [d.fl, d.f0, d.fh], "adjusted") != base:
        fails += 1
        print("FAIL reset_adjust")
    print("adjustment helpers checked")

    print("ALL OK" if not fails else "FAILS: %d" % fails)
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
