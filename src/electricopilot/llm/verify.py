"""R3 Verify: independent re-check. Deterministic invariant check ALWAYS runs;
the LLM review (Gemini Pro) is an optional second opinion (docs/05 §5.2)."""
from __future__ import annotations

import math

from ..models import SizingRequest, SizingResult, VerificationVerdict
from .client import OpenRouterClient, parse_json_lenient
from .intake import SYSTEM

_TOL = 0.02
_USER_HEAD = (
    "Независимо перепроверь расчёт. Даны ЗАПРОС и РЕЗУЛЬТАТ. Проверь условия координации "
    "(IB≤In≤IZ; I2≤1.45·IZ), IZ=It·∏k, падение напряжения и адиабатику. Верни ТОЛЬКО JSON "
    '{"agrees": true|false, "issues": [строки]}. Не вводи новых чисел.'
)


def _user_prompt(req_json: str, res_json: str) -> str:
    return f"{_USER_HEAD}\n\nЗАПРОС:\n{req_json}\n\nРЕЗУЛЬТАТ:\n{res_json}"


def verify_deterministic_check(request: SizingRequest, result: SizingResult) -> bool:
    """Recompute key invariants independently of the engine; return True if consistent."""
    load = request.load
    # 1. IB re-derivation
    if load.current_a is not None:
        ib = load.current_a
    elif load.phases == 1:
        ib = load.power_w / (load.voltage_v * load.power_factor)  # type: ignore[operator]
    else:
        ib = load.power_w / (math.sqrt(3) * load.voltage_v * load.power_factor)  # type: ignore[operator]
    if abs(ib - result.design_current_a) > max(0.05, _TOL * ib):
        return False
    cab, prot = result.selected_cable, result.selected_protection
    # 2. IZ = It · ∏k
    if abs(cab.It_a * cab.correction_total - cab.Iz_a) > max(0.1, _TOL * cab.Iz_a):
        return False
    # 3. coordination (only strictly required when PASS)
    if result.overall_status == "PASS":
        if not (ib <= prot.In_a + 1e-6 <= cab.Iz_a + 1e-6):
            return False
        if prot.I2_a > 1.45 * cab.Iz_a + 1e-6:
            return False
    return True


def verify_review(
    request: SizingRequest, result: SizingResult, client: OpenRouterClient, *, model: str
) -> VerificationVerdict:
    det_ok = verify_deterministic_check(request, result)
    raw = client.complete(
        model=model, system=SYSTEM,
        user=_user_prompt(request.model_dump_json(), result.model_dump_json()),
        json_schema={"type": "object"},
    )
    data = parse_json_lenient(raw)
    return VerificationVerdict(
        agrees=bool(data.get("agrees", False)),
        issues=[str(x) for x in data.get("issues", [])],
        model=model,
        deterministic_ok=det_ok,
    )
