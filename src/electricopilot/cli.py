"""Typer CLI for ElectriCopilot (docs/03 §3.8)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .calculation_manifest import CalculationManifest, verify_calculation_manifest
from .config import get_config
from .data.loader import DataPack, list_packs, load_data_pack
from .llm.client import OpenRouterClient
from .models import (
    InstallationConditions,
    LlmNarrative,
    LoadSpec,
    ProtectionSpec,
    SignOff,
    SizingRequest,
    SizingResult,
    VerificationVerdict,
)
from .persistence import persist
from .pipeline import run
from .project_contract import validate_project
from .report import render_json, render_markdown

app = typer.Typer(add_completion=False, help="ElectriCopilot — аудируемый подбор кабеля и защиты (IEC 60364).")


def _load_pack(data_pack: Optional[str]) -> DataPack:
    return load_data_pack(data_pack) if data_pack else load_data_pack()


def _apply_sign(result: SizingResult, sign: Optional[str]) -> SizingResult:
    if not sign:
        return result
    name = sign.split("<")[0].strip()
    lic = sign[sign.find("<") + 1:sign.find(">")].strip() if "<" in sign and ">" in sign else None
    so = SignOff(status="SIGNED", engineer_name=name or None, license_id=lic,
                 signed_at=datetime.now(timezone.utc).isoformat())
    return result.model_copy(update={"signoff": so})


def _emit(result: SizingResult, narrative: Optional[LlmNarrative],
          verdict: Optional[VerificationVerdict], fmt: str, out: Optional[str]) -> None:
    text = render_json(result) if fmt == "json" else render_markdown(result, narrative, verdict)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"Отчёт записан: {out}")
    else:
        typer.echo(text)


def _finish(request: SizingRequest, *, client: Optional[OpenRouterClient], data_pack: DataPack,
            explain: bool, verify: bool, sign: Optional[str], fmt: str, out: Optional[str],
            prefer_jsonl: bool = False) -> int:
    cfg = get_config()
    output = run(request, client=client, data_pack=data_pack, explain=explain, verify=verify,
                 strict_provenance=cfg.strict_provenance,
                 model_fast=cfg.model_fast, model_strong=cfg.model_strong)
    result = _apply_sign(output.result, sign)
    loc = persist(result, prefer_jsonl=prefer_jsonl)
    _emit(result, output.narrative, output.verdict, fmt, out)
    typer.echo(f"\n[сохранено: {loc}]", err=True)
    return 0 if result.overall_status in ("PASS", "NEEDS_REVIEW") else 1


@app.command()
def size(
    request: Optional[str] = typer.Option(None, "--request", help="JSON-файл SizingRequest"),
    power: Optional[float] = typer.Option(None, help="Мощность, Вт"),
    current: Optional[float] = typer.Option(None, help="Ток, А"),
    voltage: float = typer.Option(230.0, help="Напряжение, В"),
    phases: int = typer.Option(1, help="Фаз: 1 или 3"),
    pf: float = typer.Option(0.9, help="cosφ"),
    purpose: str = typer.Option("general", help="lighting|power|socket|motor|general"),
    method: str = typer.Option("C", help="метод прокладки A1..G"),
    material: str = typer.Option("Cu", help="Cu|Al"),
    insulation: str = typer.Option("PVC", help="PVC|XLPE"),
    ambient: float = typer.Option(30.0, help="t окружения, °C"),
    grouping: int = typer.Option(1, help="число сгруппированных цепей"),
    length: float = typer.Option(20.0, help="длина трассы, м"),
    device: str = typer.Option("MCB", help="MCB|MCCB|gG_fuse"),
    iscc: Optional[float] = typer.Option(None, help="ток КЗ, А"),
    tdisc: float = typer.Option(0.1, help="время отключения, с"),
    vdlimit: Optional[float] = typer.Option(None, help="предел ΔU, %"),
    data_pack: Optional[str] = typer.Option(
        None, "--data-pack", help="норм-пакет: имя из data/packs/ (напр. pue-rk) или путь к JSON"
    ),
    explain: bool = typer.Option(False, "--explain", help="добавить объяснение"),
    verify: bool = typer.Option(False, "--verify", help="добавить независимую проверку"),
    fmt: str = typer.Option("md", "--format", help="md|json"),
    out: Optional[str] = typer.Option(None, "--out", help="файл вывода"),
    sign: Optional[str] = typer.Option(None, "--sign", help='"Имя <лицензия>"'),
) -> None:
    """Подобрать сечение кабеля и аппарат защиты."""
    if request:
        req = SizingRequest.model_validate_json(Path(request).read_text(encoding="utf-8"))
    else:
        if power is None and current is None:
            typer.echo("Нужно --power или --current (или --request).", err=True)
            raise typer.Exit(2)
        req = SizingRequest(
            load=LoadSpec(power_w=power, current_a=current, voltage_v=voltage,
                          phases=phases, power_factor=pf, purpose=purpose),
            installation=InstallationConditions(method=method, material=material,
                          insulation=insulation, ambient_temp_c=ambient,
                          grouping_circuits=grouping, length_m=length),
            protection=ProtectionSpec(device_class=device, prospective_fault_current_a=iscc,
                          disconnection_time_s=tdisc, max_voltage_drop_pct=vdlimit))
    client = OpenRouterClient()
    code = _finish(req, client=client, data_pack=_load_pack(data_pack),
                   explain=explain, verify=verify, sign=sign, fmt=fmt, out=out)
    raise typer.Exit(code)


@app.command()
def explain(request: str = typer.Option(..., "--request", help="JSON-файл SizingRequest"),
            data_pack: Optional[str] = typer.Option(None, "--data-pack"),
            fmt: str = typer.Option("md", "--format")) -> None:
    """Расчёт + объяснение."""
    req = SizingRequest.model_validate_json(Path(request).read_text(encoding="utf-8"))
    raise typer.Exit(_finish(req, client=OpenRouterClient(), data_pack=_load_pack(data_pack),
                             explain=True, verify=False, sign=None, fmt=fmt, out=None))


@app.command()
def verify(request: str = typer.Option(..., "--request", help="JSON-файл SizingRequest"),
           data_pack: Optional[str] = typer.Option(None, "--data-pack"),
           fmt: str = typer.Option("md", "--format")) -> None:
    """Расчёт + независимая проверка."""
    req = SizingRequest.model_validate_json(Path(request).read_text(encoding="utf-8"))
    raise typer.Exit(_finish(req, client=OpenRouterClient(), data_pack=_load_pack(data_pack),
                             explain=False, verify=True, sign=None, fmt=fmt, out=None))


@app.command()
def demo(live: bool = typer.Option(False, "--live", help="использовать LLM (нужен ключ)"),
         data_pack: Optional[str] = typer.Option(None, "--data-pack")) -> None:
    """Сквозной пример (по умолчанию офлайн-детерминированный)."""
    req = SizingRequest.model_validate_json(
        Path(__file__).parent.joinpath("data/examples/motor_feeder.json").read_text(encoding="utf-8")
    )
    client = OpenRouterClient() if live else None
    raise typer.Exit(_finish(req, client=client, data_pack=_load_pack(data_pack),
                             explain=True, verify=True, sign=None, fmt="md", out=None,
                             prefer_jsonl=not live))


@app.command()
def packs() -> None:
    """Список доступных норм-пакетов (data/packs/)."""
    for meta in list_packs():
        assessment = load_data_pack(meta.name).publication_assessment()
        typer.echo(
            f"{meta.name:16} {meta.version:8} {meta.status:16} "
            f"{assessment.verification_status:14} {meta.source_note}"
        )


@app.command("verify-manifest")
def verify_manifest_command(
    manifest: str = typer.Option(..., "--manifest", help="manifest.json"),
    project: str = typer.Option(..., "--project", help="project.json"),
    data_pack: Optional[str] = typer.Option(None, "--data-pack", help="имя или JSON норм-пака"),
    build_identity: Optional[str] = typer.Option(
        None, "--build-identity", help="ожидаемая identity сборки; иначе окружение",
    ),
) -> None:
    """Offline-проверка CalculationManifest по проекту и норм-паку."""
    manifest_value = CalculationManifest.model_validate_json(
        Path(manifest).read_text(encoding="utf-8")
    )
    project_value = validate_project(json.loads(Path(project).read_text(encoding="utf-8")))
    result = verify_calculation_manifest(
        manifest_value,
        project_value,
        _load_pack(data_pack or project_value.norm_pack),
        build_identity=build_identity,
    )
    typer.echo(result.model_dump_json(indent=2))
    if not result.ok:
        raise typer.Exit(1)


@app.command()
def models() -> None:
    """Показать доступные slug'и Gemini в OpenRouter (нужен ключ)."""
    import httpx

    cfg = get_config()
    if not cfg.llm_available:
        typer.echo("OPENROUTER_API_KEY не задан.", err=True)
        raise typer.Exit(2)
    resp = httpx.get(f"{cfg.openrouter_base_url}/models",
                     headers={"Authorization": f"Bearer {cfg.openrouter_api_key}"}, timeout=30)
    resp.raise_for_status()
    for slug in sorted(m["id"] for m in resp.json().get("data", [])):
        if "gemini" in slug.lower():
            typer.echo(slug)


if __name__ == "__main__":
    app()
