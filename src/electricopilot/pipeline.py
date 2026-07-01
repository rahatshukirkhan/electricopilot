"""End-to-end orchestration: size → (explain) → (verify) → provenance guardrail.

Pure of I/O and CLI; callable with client=None for a fully offline, deterministic run
(this is what the E2E test exercises — no network, no keys, per D8).
"""
from __future__ import annotations

from dataclasses import dataclass

from .data.loader import DataPack
from .engine import size
from .exceptions import LlmError
from .guardrails import apply_provenance_downgrade, check_numeric_provenance
from .llm.client import OpenRouterClient
from .llm.explain import explain_render, explain_render_template
from .llm.verify import verify_deterministic_check, verify_review
from .models import LlmNarrative, SizingRequest, SizingResult, VerificationVerdict


@dataclass
class PipelineOutput:
    result: SizingResult
    narrative: LlmNarrative | None
    verdict: VerificationVerdict | None


def run(
    request: SizingRequest,
    *,
    client: OpenRouterClient | None = None,
    data_pack: DataPack | None = None,
    explain: bool = False,
    verify: bool = False,
    strict_provenance: bool = True,
    model_fast: str = "",
    model_strong: str = "",
) -> PipelineOutput:
    result = size(request, data_pack=data_pack)
    live = client is not None and client.available

    narrative: LlmNarrative | None = None
    if explain:
        if live:
            try:
                narrative = explain_render(result, client, model=model_fast)  # type: ignore[arg-type]
                ok, unv = check_numeric_provenance(
                    narrative.text, result, strict=strict_provenance
                )
                narrative = narrative.model_copy(
                    update={"provenance_ok": ok, "unverified_numbers": unv}
                )
            except LlmError:
                narrative = explain_render_template(result)
        else:
            narrative = explain_render_template(result)

    verdict: VerificationVerdict | None = None
    if verify:
        det_ok = verify_deterministic_check(request, result)
        if live:
            try:
                verdict = verify_review(request, result, client, model=model_strong)  # type: ignore[arg-type]
            except LlmError:
                verdict = VerificationVerdict(
                    agrees=det_ok, issues=["живая проверка LLM недоступна"],
                    model="", deterministic_ok=det_ok,
                )
        else:
            verdict = VerificationVerdict(agrees=det_ok, model="", deterministic_ok=det_ok)

    if narrative is not None and not narrative.provenance_ok:
        result = apply_provenance_downgrade(result, narrative)

    return PipelineOutput(result=result, narrative=narrative, verdict=verdict)
