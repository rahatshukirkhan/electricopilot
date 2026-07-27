"""Versioned, deterministic identity for board calculations (docs/18)."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .data.loader import DataPack, NUMERIC_PROVENANCE_SECTIONS
from .project_contract import ProjectInput, validate_project

MANIFEST_SCHEMA_VERSION: Literal[1] = 1
_SIZING_SECTIONS = tuple(
    section
    for section in NUMERIC_PROVENANCE_SECTIONS
    if not section.startswith("normcheck.") and section != "voltage_drop_limit"
)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible value with the stable v1 canonicalization."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BuildIdentity(_ManifestModel):
    status: Literal["provided", "unavailable"]
    value: str | None = None

    @model_validator(mode="after")
    def _consistent_status(self) -> BuildIdentity:
        if self.status == "provided" and not self.value:
            raise ValueError("provided build identity requires a non-empty value")
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable build identity must not contain a value")
        return self


class ApplicationIdentity(_ManifestModel):
    version: str
    build: BuildIdentity


class NormPackIdentity(_ManifestModel):
    name: str
    version: str
    status: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DataSectionIdentity(_ManifestModel):
    section: str
    verification_status: Literal["VERIFIED", "NEEDS_REVIEW"]
    issues: list[str]


def _manifest_core(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload["schema_version"],
        "project_schema_version": payload["project_schema_version"],
        "project_sha256": payload["project_sha256"],
        "norm_pack": payload["norm_pack"],
        "used_data_sections": payload["used_data_sections"],
        "application": payload["application"],
    }


class CalculationManifest(_ManifestModel):
    schema_version: Literal[1] = MANIFEST_SCHEMA_VERSION
    project_schema_version: int
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    norm_pack: NormPackIdentity
    used_data_sections: list[DataSectionIdentity]
    application: ApplicationIdentity
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calculation_id: str = Field(pattern=r"^calc-v1-[0-9a-f]{20}$")

    @model_validator(mode="after")
    def _verify_self_identity(self) -> CalculationManifest:
        digest = sha256_hex(canonical_json_bytes(_manifest_core(self.model_dump(mode="json"))))
        if self.manifest_sha256 != digest:
            raise ValueError("manifest_sha256 does not match manifest contents")
        expected_id = f"calc-v1-{digest[:20]}"
        if self.calculation_id != expected_id:
            raise ValueError("calculation_id does not match manifest_sha256")
        return self


class ManifestVerification(_ManifestModel):
    ok: bool
    expected_calculation_id: str
    actual_calculation_id: str
    mismatches: list[str]
    explanation: str | None = None
    """Human-facing reason when the divergence is explainable rather than a broken calculation
    (docs/20 §9): a pack version bump must not read to the user like the numbers moved."""


def _build_identity(explicit: str | None = None) -> BuildIdentity:
    value = explicit
    if value is None:
        value = (
            os.environ.get("ELECTRICOPILOT_BUILD_IDENTITY")
            or os.environ.get("VERCEL_GIT_COMMIT_SHA")
        )
    value = value.strip() if value else None
    return (
        BuildIdentity(status="provided", value=value)
        if value
        else BuildIdentity(status="unavailable", value=None)
    )


def effective_project_input(project: ProjectInput) -> dict[str, Any]:
    """Return only fields consumed by engine/report/R01-R11, excluding UI snapshots."""
    canonical = validate_project(project)
    circuits: list[dict[str, Any]] = []
    for circuit in canonical.circuits:
        declaration = circuit.meta.import_declaration
        circuits.append({
            "id": circuit.id,
            "ref": circuit.ref,
            "sort_index": circuit.sort_index,
            "request": circuit.request.model_dump(mode="json"),
            "meta": {
                "phase": circuit.meta.phase,
                "rcd": circuit.meta.rcd.model_dump(mode="json"),
                "diversity_category": circuit.meta.diversity_category,
                "cores": circuit.meta.cores,
                "pe_section_mm2": circuit.meta.pe_section_mm2,
                "import_declaration": (
                    {
                        "declared_section_mm2": declaration.declared_section_mm2,
                        "declared_in_a": declaration.declared_in_a,
                    }
                    if declaration is not None
                    else None
                ),
            },
        })
    return {
        "schema_version": canonical.schema_version,
        "supply": canonical.supply.model_dump(mode="json"),
        "diversity": canonical.diversity.model_dump(mode="json"),
        "circuits": circuits,
    }


def canonical_pack_payload(pack: DataPack) -> dict[str, Any]:
    """Return the complete validated semantic content used for the pack digest."""
    return pack.model_dump(mode="json")


def calculation_sections(project: ProjectInput, pack: DataPack) -> list[str]:
    canonical = validate_project(project)
    sections = set(_SIZING_SECTIONS) if canonical.circuits else set()
    if canonical.circuits and any(
        c.request.protection.max_voltage_drop_pct is None for c in canonical.circuits
    ):
        sections.add("voltage_drop_limit")
    sections.update(f"normcheck.{rule_id}" for rule_id in pack.normcheck)
    return sorted(sections)


def build_calculation_manifest(
    project: ProjectInput,
    pack: DataPack,
    *,
    build_identity: str | None = None,
) -> CalculationManifest:
    effective = effective_project_input(project)
    sections = calculation_sections(project, pack)
    assessment = pack.assess_provenance(sections)
    core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_schema_version": int(effective["schema_version"]),
        "project_sha256": sha256_hex(canonical_json_bytes(effective)),
        "norm_pack": {
            "name": pack.meta.name,
            "version": pack.meta.version,
            "status": pack.meta.status,
            "sha256": sha256_hex(canonical_json_bytes(canonical_pack_payload(pack))),
        },
        "used_data_sections": [
            {
                "section": item.section,
                "verification_status": "VERIFIED" if item.trusted else "NEEDS_REVIEW",
                "issues": list(item.issues),
            }
            for item in assessment.used_sections
        ],
        "application": {
            "version": __version__,
            "build": _build_identity(build_identity).model_dump(mode="json"),
        },
    }
    digest = sha256_hex(canonical_json_bytes(core))
    return CalculationManifest.model_validate({
        **core,
        "manifest_sha256": digest,
        "calculation_id": f"calc-v1-{digest[:20]}",
    })


def manifest_data_identity(manifest: CalculationManifest) -> str:
    verification_status = (
        "NEEDS_REVIEW"
        if any(item.verification_status == "NEEDS_REVIEW" for item in manifest.used_data_sections)
        else "VERIFIED"
    )
    return (
        f"Расчёт: {manifest.calculation_id}; норм-пакет: {manifest.norm_pack.name} "
        f"v{manifest.norm_pack.version}; происхождение: {manifest.norm_pack.status}; "
        f"SHA-256 пакета: {manifest.norm_pack.sha256[:12]}; "
        f"проверка манифеста: {verification_status}."
    )


def verify_calculation_manifest(
    expected: CalculationManifest | Mapping[str, Any],
    project: ProjectInput,
    pack: DataPack,
    *,
    build_identity: str | None = None,
) -> ManifestVerification:
    parsed = (
        expected
        if isinstance(expected, CalculationManifest)
        else CalculationManifest.model_validate(expected)
    )
    actual = build_calculation_manifest(project, pack, build_identity=build_identity)
    mismatches: list[str] = []
    if parsed.project_sha256 != actual.project_sha256:
        mismatches.append("project_sha256")
    if parsed.norm_pack.sha256 != actual.norm_pack.sha256:
        mismatches.append("norm_pack.sha256")
    if parsed.used_data_sections != actual.used_data_sections:
        mismatches.append("used_data_sections")
    if parsed.application != actual.application:
        mismatches.append("application")
    if parsed.manifest_sha256 != actual.manifest_sha256:
        mismatches.append("manifest_sha256")
    return ManifestVerification(
        ok=not mismatches,
        expected_calculation_id=parsed.calculation_id,
        actual_calculation_id=actual.calculation_id,
        mismatches=mismatches,
        explanation=_explain(parsed, actual, mismatches, pack),
    )


#: A divergence confined to these two is fully accounted for by the pack itself changing:
#: the project input, the used data sections and the build all matched.
_PACK_ONLY_MISMATCHES = {"norm_pack.sha256", "manifest_sha256"}


def _explain(
    parsed: CalculationManifest,
    actual: CalculationManifest,
    mismatches: list[str],
    pack: DataPack,
) -> str | None:
    """Name the cause of an explainable divergence (docs/20 §9).

    The PER-22 contract is NOT weakened: the hash still covers the whole pack, citations
    included, so adding `doc_id`/`anchor` legitimately changes `calculation_id` for every
    previously saved project. That is expected verification behaviour, not a bug — but the
    user must be told WHY, instead of seeing what looks like a calculation that no longer
    reproduces. The claim about what changed is quoted from the pack's own `source_note`;
    this function does not assert on its own authority that the numbers held (docs/20 §12.5
    proves that structurally in CI).
    """
    if not mismatches or set(mismatches) - _PACK_ONLY_MISMATCHES:
        return None
    if parsed.norm_pack.name != actual.norm_pack.name:
        return (
            f"Расчёт выполнен на другом паке норм: {parsed.norm_pack.name} → "
            f"{actual.norm_pack.name}. Числа могли измениться — требуется пересчёт."
        )
    if parsed.norm_pack.version == actual.norm_pack.version:
        return (
            f"Пак {actual.norm_pack.name} той же версии {actual.norm_pack.version}, но с иным "
            "содержимым — содержимое пака изменено без повышения версии, нужна проверка."
        )
    return (
        f"Обновлён пак норм: {actual.norm_pack.name} {parsed.norm_pack.version} → "
        f"{actual.norm_pack.version}. Входные данные проекта, состав использованных разделов "
        f"и сборка совпали, поэтому расхождение вызвано версией пака, а не пересчётом. "
        f"Изменение по описанию пака: {pack.meta.source_note}"
    )
