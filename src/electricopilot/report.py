"""Render a SizingResult into an auditable Markdown / JSON report (docs/03 §3.7)."""
from __future__ import annotations

from .models import Citation, LlmNarrative, SizingResult, VerificationVerdict

_STATUS_ICON = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "NEEDS_REVIEW": "⚠️ NEEDS_REVIEW"}


def _cite(c: Citation) -> str:
    parts = [c.standard]
    if c.clause:
        parts.append(f"§{c.clause}")
    if c.table:
        parts.append(f"Табл. {c.table}")
    return " ".join(parts)


def render_json(result: SizingResult) -> str:
    return result.model_dump_json(indent=2)


def render_markdown(
    result: SizingResult,
    narrative: LlmNarrative | None = None,
    verdict: VerificationVerdict | None = None,
) -> str:
    r = result
    cab, prot = r.selected_cable, r.selected_protection
    data_needs_review = r.data_provenance.verification_status == "NEEDS_REVIEW"
    L: list[str] = []
    L.append("# ElectriCopilot — отчёт подбора кабеля и аппарата защиты")
    L.append("")
    L.append(f"> {r.disclaimer}")
    L.append("")
    L.append(f"**Статус:** {_STATUS_ICON.get(r.overall_status, r.overall_status)}  ·  "
             f"**связывающий критерий:** `{cab.governing_constraint}`")
    L.append("")

    # -- request --
    ld, inst = r.request.load, r.request.installation
    src = f"P={ld.power_w:g} Вт" if ld.power_w else f"I={ld.current_a:g} A"
    L.append("## Запрос")
    L.append(f"- Нагрузка: {src}, U={ld.voltage_v:g} В, {ld.phases}ф, cosφ={ld.power_factor:g}, "
             f"назначение={ld.purpose}")
    L.append(f"- Прокладка: метод {inst.method}, {inst.material}/{inst.insulation}, "
             f"жил={inst.loaded_conductors or ('2' if ld.phases == 1 else '3')}, "
             f"t_окр={inst.ambient_temp_c:g}°C, групп={inst.grouping_circuits}, L={inst.length_m:g} м")
    fault = (f"{r.request.protection.prospective_fault_current_a:g} A"
             if r.request.protection.prospective_fault_current_a else "—")
    L.append(f"- Защита: {prot.device_class}, I_scc={fault}, "
             f"t_откл={r.request.protection.disconnection_time_s:g} с")
    L.append("")

    # -- result --
    L.append("## Результат")
    L.append(f"- **Кабель:** {cab.cross_section_mm2:g} мм² {cab.material}/{cab.insulation} · "
             f"It={cab.It_a:g} A · ∏k={cab.correction_total:g} · **IZ={cab.Iz_a:g} A**")
    L.append(f"- **Аппарат:** {prot.device_class} {prot.In_a:g} A · "
             f"I2/In={prot.I2_over_In:g} · I2={prot.I2_a:g} A")
    L.append(f"- **IB:** {r.design_current_a:g} A · **ΔU:** {r.voltage_drop_pct:g}% "
             f"(предел {r.voltage_drop_limit_pct:g}%)")
    if r.adiabatic_min_mm2 is not None:
        L.append(f"- **Адиабатика:** S_min={r.adiabatic_min_mm2:g} мм²")
    L.append("")

    # -- checks --
    L.append("## Проверки координации и пределов")
    if data_needs_review:
        L.append(
            "> _Вердикты требуют проверки данных: арифметический PASS не является полным "
            "нормативным соответствием._"
        )
    L.append("")
    L.append("| Проверка | Условие | Результат | Детали | Ссылка |")
    L.append("|---|---|---|---|---|")
    for c in r.checks:
        res = "✅" if c.passed else "❌"
        cites = "; ".join(_cite(x) for x in c.citations)
        L.append(f"| {c.name} | {c.condition} | {res} | {c.detail} | {cites} |")
    L.append("")

    # -- audit trace --
    L.append("## Трасса рассуждения (аудит)")
    for st in r.audit_trace:
        L.append(f"### {st.id} — {st.title}")
        if st.formula:
            L.append(f"- Формула: `{st.formula}`")
        if st.computation:
            L.append(f"- Вычисление: {st.computation}")
        if st.citations:
            L.append(f"- Ссылки: {'; '.join(_cite(c) for c in st.citations)}")
        L.append("")

    # -- provenance note --
    if r.data_provenance_note:
        L.append("## Провенанс данных")
        L.append(f"> {r.data_provenance_note}")
        L.append("")

    if r.warnings:
        L.append("## Предупреждения")
        for w in r.warnings:
            L.append(f"- {w}")
        L.append("")

    # -- LLM narrative --
    if narrative is not None and narrative.text:
        prov = "провенанс OK" if narrative.provenance_ok else \
            f"НЕ прошёл провенанс: {narrative.unverified_numbers}"
        tag = f" _(модель {narrative.model}; {prov})_" if narrative.model else f" _({prov})_"
        L.append(f"## Объяснение{tag}")
        L.append(narrative.text)
        L.append("")

    # -- independent verification --
    if verdict is not None:
        L.append("## Независимая проверка")
        agree = "согласна" if verdict.agrees else "НЕ согласна"
        detm = "OK" if verdict.deterministic_ok else "расхождение"
        model = f" (модель {verdict.model})" if verdict.model else ""
        L.append(f"- Детерминированная проверка инвариантов: {detm}")
        L.append(f"- LLM-ревизия{model}: {agree}")
        for iss in verdict.issues:
            L.append(f"  - {iss}")
        L.append("")

    # -- sign-off --
    L.append("## Подпись инженера")
    so = r.signoff
    if so.status == "SIGNED":
        L.append(f"- **ПОДПИСАНО:** {so.engineer_name or '—'} "
                 f"(лиц. {so.license_id or '—'}) · {so.signed_at or ''}")
    else:
        L.append("- **НЕ ПОДПИСАНО** (`UNSIGNED_ADVISORY`) — требуется проверка и подпись инженера.")
    L.append(f"- {so.statement}")
    L.append("")
    return "\n".join(L)
