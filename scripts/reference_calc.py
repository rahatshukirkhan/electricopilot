#!/usr/bin/env python3
"""Independent reference calculator — a SECOND implementation of the sizing math,
used to cross-check the engine's golden cases against the synthetic data pack.

This intentionally does NOT import electricopilot.engine (that would be circular).
It reads the same iec_stub.json and recomputes IB / In / IZ / ΔU / adiabatic from
scratch. Golden tests assert the doc-06 constants; this script is a human-facing
recompute you can eyeball.

Usage:  uv run python scripts/reference_calc.py
"""
from __future__ import annotations

import json
import math
from importlib import resources


def key(x: float) -> str:
    return format(float(x), "g")


def load_pack() -> dict:
    raw = resources.files("electricopilot").joinpath("data/iec_stub.json").read_text("utf-8")
    return json.loads(raw)


def size_case(pack: dict, name: str, *, P=None, amps=None, U, phases, pf, method,
              material, insulation, ambient, grouping, length, device, iscc, t, purpose="power"):
    ratings = pack["standard_ratings_a"]
    sections = pack["standard_sections_mm2"]
    ib = amps if amps is not None else (P / (U * pf) if phases == 1 else P / (math.sqrt(3) * U * pf))
    In = next(r for r in ratings if r >= ib)
    i2r = pack["device_classes"][device]["i2_over_in"]
    req_iz = max(In, i2r * In / 1.45)
    n = 2 if phases == 1 else 3
    tbl = pack["ampacity"][method][material][insulation][str(n)]
    ka = pack["ambient_correction"][insulation][key(ambient)]
    kg = pack["grouping_correction"][key(grouping)]
    corr = ka * kg
    k = pack["k_material"][f"{material}/{insulation}"]
    rho = pack["resistivity_ohm_mm2_per_m"][material]
    lam = pack["reactance_ohm_per_m"]
    vd_limit = pack["vd_limits_pct"][purpose]
    s_min = iscc * math.sqrt(t) / k if iscc else None
    sinp = math.sqrt(max(0.0, 1 - pf * pf))

    def vd(S):
        term = rho * pf / S + lam * sinp
        du = (2 if phases == 1 else math.sqrt(3)) * ib * length * term
        return du / U * 100

    chosen = None
    for S in sections:
        iz = tbl[key(S)] * corr
        if iz >= req_iz - 1e-9 and vd(S) <= vd_limit + 1e-9 and (s_min is None or S >= s_min - 1e-9):
            chosen = (S, iz)
            break
    S, iz = chosen if chosen else (sections[-1], 0)
    print(f"{name:28} IB={ib:6.2f} In={In:>4g} S={S:>4g} IZ={iz:6.2f} "
          f"VD={vd(S):4.2f}% Sadia={(s_min or 0):5.2f}")


def main() -> None:
    pack = load_pack()
    print("pack:", pack["meta"]["name"], pack["meta"]["version"], f"({pack['meta']['status']})\n")
    size_case(pack, "Case1 1ph overload-bound", P=4600, U=230, phases=1, pf=1.0, method="C",
              material="Cu", insulation="PVC", ambient=35, grouping=1, length=25, device="MCB",
              iscc=800, t=0.1)
    size_case(pack, "Case2 3ph gG-fuse-bound", P=15000, U=400, phases=3, pf=0.85, method="C",
              material="Cu", insulation="XLPE", ambient=40, grouping=3, length=50, device="gG_fuse",
              iscc=2500, t=0.1)
    size_case(pack, "Case3 1ph VD-bound", P=4600, U=230, phases=1, pf=1.0, method="C",
              material="Cu", insulation="PVC", ambient=30, grouping=1, length=60, device="MCB",
              iscc=800, t=0.1)
    size_case(pack, "Case4 3ph SC-bound", amps=100, U=400, phases=3, pf=0.9, method="C",
              material="Cu", insulation="XLPE", ambient=30, grouping=1, length=30, device="MCB",
              iscc=20000, t=0.2)


if __name__ == "__main__":
    main()
