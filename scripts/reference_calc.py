#!/usr/bin/env python3
"""Independent reference calculator — a SECOND implementation of the sizing math,
used to cross-check the engine's golden cases against a data pack.

This intentionally does NOT import electricopilot.engine (that would be circular).
It reads a data/packs/<name>.json pack and recomputes IB / In / IZ / ΔU / adiabatic
from scratch. Golden tests assert the doc-06 / doc-12 constants; this script is a
human-facing recompute you can eyeball, and per docs/12 §1.2 the source of truth for
a NEW pack's golden expectations (never copy them from the engine's own output).

Usage:  uv run python scripts/reference_calc.py [pack-name]   # default: iec-stub
"""
from __future__ import annotations

import json
import math
import sys
from importlib import resources


def key(x: float) -> str:
    return format(float(x), "g")


def load_pack(name: str = "iec-stub") -> dict:
    raw = resources.files("electricopilot").joinpath(f"data/packs/{name}.json").read_text("utf-8")
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
        it = tbl.get(key(S))
        if it is None:
            continue  # off-table for this pack (e.g. pue-rk doesn't cover every section/method)
        iz = it * corr
        if iz >= req_iz - 1e-9 and vd(S) <= vd_limit + 1e-9 and (s_min is None or S >= s_min - 1e-9):
            chosen = (S, iz)
            break
    S, iz = chosen if chosen else (sections[-1], 0)
    print(f"{name:28} IB={ib:6.2f} In={In:>4g} S={S:>4g} IZ={iz:6.2f} "
          f"VD={vd(S):4.2f}% Sadia={(s_min or 0):5.2f}")


# One demo case set per bundled pack — each case's method/material/insulation/ambient/
# grouping must exist in that pack's own tables (packs are not required to cover the
# same domain, docs/12 §1.1 off-table policy).
_CASES: dict[str, list[dict]] = {
    "iec-stub": [
        dict(name="Case1 1ph overload-bound", P=4600, U=230, phases=1, pf=1.0, method="C",
             material="Cu", insulation="PVC", ambient=35, grouping=1, length=25, device="MCB",
             iscc=800, t=0.1),
        dict(name="Case2 3ph gG-fuse-bound", P=15000, U=400, phases=3, pf=0.85, method="C",
             material="Cu", insulation="XLPE", ambient=40, grouping=3, length=50, device="gG_fuse",
             iscc=2500, t=0.1),
        dict(name="Case3 1ph VD-bound", P=4600, U=230, phases=1, pf=1.0, method="C",
             material="Cu", insulation="PVC", ambient=30, grouping=1, length=60, device="MCB",
             iscc=800, t=0.1),
        dict(name="Case4 3ph SC-bound", amps=100, U=400, phases=3, pf=0.9, method="C",
             material="Cu", insulation="XLPE", ambient=30, grouping=1, length=30, device="MCB",
             iscc=20000, t=0.2),
    ],
    # pue-rk v1 scope: method C / Cu only (see tests/test_golden_cases.py PUE_RK_CASES note —
    # B1/Al hit a real off-table gap in the frozen engine's sweep, not fixed here).
    "pue-rk": [
        dict(name="Case1 overload", P=4000, U=230, phases=1, pf=1.0, method="C",
             material="Cu", insulation="PVC", ambient=35, grouping=1, length=20, device="MCB",
             iscc=None, t=0.1),
        dict(name="Case2 gGfuse-bound", P=15000, U=400, phases=3, pf=0.85, method="C",
             material="Cu", insulation="PVC", ambient=40, grouping=5, length=50, device="gG_fuse",
             iscc=2500, t=0.1),
        dict(name="Case3 VD-bound", P=3500, U=230, phases=1, pf=1.0, method="C",
             material="Cu", insulation="PVC", ambient=25, grouping=1, length=80, device="MCB",
             iscc=800, t=0.1),
        dict(name="Case4 SC-bound", amps=90, U=400, phases=3, pf=0.9, method="C",
             material="Cu", insulation="PVC", ambient=25, grouping=1, length=20, device="MCB",
             iscc=18000, t=0.2),
    ],
}


def main() -> None:
    pack_name = sys.argv[1] if len(sys.argv) > 1 else "iec-stub"
    pack = load_pack(pack_name)
    print("pack:", pack["meta"]["name"], pack["meta"]["version"], f"({pack['meta']['status']})\n")
    cases = _CASES.get(pack_name)
    if not cases:
        print(f"(no reference cases wired up yet for pack '{pack_name}' — add to _CASES)")
        return
    for case in cases:
        size_case(pack, case.pop("name"), **case)


if __name__ == "__main__":
    main()
