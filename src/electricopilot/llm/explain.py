"""R2 Explain: SizingResult → human narrative. Live (Gemini) or deterministic template."""
from __future__ import annotations

from ..models import LlmNarrative, SizingResult
from .client import OpenRouterClient
from .intake import SYSTEM

_USER = (
    "Ниже — РЕЗУЛЬТАТ детерминированного расчёта (числа уже посчитаны). Объясни инженеру "
    "выбор сечения и аппарата, ссылаясь на пункты норм из трассы. КАТЕГОРИЧЕСКИ не вводи "
    "новых чисел — используй только те, что есть в результате. 4–8 предложений.\n\n{payload}"
)


def explain_render(result: SizingResult, client: OpenRouterClient, *, model: str) -> LlmNarrative:
    payload = result.model_dump_json(indent=2)
    text = client.complete(model=model, system=SYSTEM, user=_USER.format(payload=payload))
    return LlmNarrative(text=text.strip(), model=model)


def explain_render_template(result: SizingResult) -> LlmNarrative:
    """Deterministic fallback narrative built from the result (provenance-trivially clean)."""
    r = result
    cab, prot = r.selected_cable, r.selected_protection
    parts = [
        f"Расчётный ток IB={r.design_current_a:g} A. Выбран аппарат {prot.device_class} "
        f"In={prot.In_a:g} A (I2/In={prot.I2_over_In:g}, I2={prot.I2_a:g} A).",
        f"При методе прокладки {r.request.installation.method}, {cab.material}/{cab.insulation}, "
        f"суммарной поправке ∏k={cab.correction_total:g}, выбрано сечение "
        f"{cab.cross_section_mm2:g} мм² (It={cab.It_a:g} A, IZ={cab.Iz_a:g} A).",
        f"Падение напряжения ΔU={r.voltage_drop_pct:g}% при пределе "
        f"{r.voltage_drop_limit_pct:g}%.",
    ]
    if r.adiabatic_min_mm2 is not None:
        parts.append(f"Термическая стойкость к КЗ: минимальное сечение "
                     f"S_min={r.adiabatic_min_mm2:g} мм².")
    parts.append(f"Связывающий критерий — {cab.governing_constraint}; итоговый статус "
                 f"{r.overall_status}. Результат носит рекомендательный характер и требует "
                 f"подписи инженера.")
    return LlmNarrative(text=" ".join(parts), model="", provenance_ok=True)
