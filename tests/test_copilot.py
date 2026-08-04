"""Safe board Copilot loop, deterministic proposal, and Studio contract."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

import pytest

from electricopilot import studio_api
from electricopilot.copilot import run_copilot
from electricopilot.copilot.models import CopilotRequest
from electricopilot.copilot.tools import build_proposal, check_reply_provenance
from electricopilot.config import Config
from electricopilot.data.loader import load_data_pack
from electricopilot.engine import size
from electricopilot.llm.client import OpenRouterClient
from electricopilot.project_contract import Project


def _request(*, length_m: float = 18) -> dict[str, Any]:
    return {
        "load": {
            "description": "Конвейер",
            "power_w": 3000,
            "voltage_v": 230,
            "phases": 1,
            "power_factor": 0.9,
            "purpose": "motor",
        },
        "installation": {
            "method": "C",
            "material": "Cu",
            "insulation": "PVC",
            "ambient_temp_c": 30,
            "grouping_circuits": 1,
            "length_m": length_m,
        },
        "protection": {
            "device_class": "MCB",
            "prospective_fault_current_a": 1500,
            "disconnection_time_s": 0.01,
            "max_voltage_drop_pct": None,
            "trip_curve_type": "C",
        },
    }


def _project(*, with_circuit: bool = False) -> Project:
    circuits: list[dict[str, Any]] = []
    if with_circuit:
        circuits.append({
            "id": "c-1",
            "ref": "M1",
            "sort_index": 0,
            "request": _request(),
            "meta": {"phase": "L1", "rcd": {"present": False}},
            "result": {"status": "PASS", "section": 999},
            "signoff": {"status": "UNSIGNED_ADVISORY"},
        })
    return Project.model_validate({
        "schema_version": 2,
        "id": "copilot-project",
        "name": "Copilot test",
        "board_ref": "DB-C",
        "created_at": "2026-07-19T10:00:00Z",
        "updated_at": "2026-07-19T10:00:00Z",
        "norm_pack": "iec-stub",
        "supply": {
            "voltage_v": 400,
            "phases": 3,
            "ways_total": 12,
            "earthing": "TN-C-S",
            "method": "C",
            "material": "Cu",
            "insulation": "PVC",
            "ambient_temp_c": 30,
        },
        "circuits": circuits,
    })


def _tool_turn(name: str, arguments: dict[str, Any], *, content: str = "") -> dict[str, Any]:
    return {
        "content": content,
        "tool_calls": [{"id": "call-1", "name": name, "arguments": arguments}],
    }


class ScriptedClient:
    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.calls = 0
        self.intake_calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    def complete_tools(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.seen_messages.append(deepcopy(kwargs["messages"]))
        return self.turns.pop(0)

    def complete(self, **_: Any) -> str:
        self.intake_calls += 1
        return json.dumps(_request(), ensure_ascii=False)


def _run(project: Project, client: ScriptedClient, **kwargs: Any) -> Any:
    return run_copilot(
        project,
        "сделай изменение",
        [],
        client=client,
        model="fake/strong",
        parse_model="fake/fast",
        **kwargs,
    )


def test_add_proposal_is_deterministic_and_never_mutates_input() -> None:
    project = _project()
    original = project.model_dump_json()
    turn = _tool_turn(
        "propose_changes",
        {"ops": [{"op": "add", "ref": "M2", "request": _request(), "meta": {"phase": "L2"}}]},
        content="Предложение подготовлено; требуется подпись инженера.",
    )

    result = _run(project, ScriptedClient([turn]))

    assert result.ok and result.proposal is not None
    assert project.model_dump_json() == original
    operation = result.proposal.ops[0]
    assert operation.circuit_id == "copilot-1"
    expected = size(operation.request, data_pack=load_data_pack("iec-stub"))
    assert result.proposal.diff.circuits[0].after is not None
    assert result.proposal.diff.circuits[0].after.section_mm2 == expected.selected_cable.cross_section_mm2
    assert result.proposal.diff.signoff_notice.startswith("UNSIGNED_ADVISORY")

    proposal2, _candidate, _before, _after = build_proposal(
        project,
        [operation.model_dump(mode="json")],
        load_data_pack("iec-stub"),
    )
    assert proposal2.model_dump_json() == result.proposal.model_dump_json()


def test_edit_recomputes_and_ignores_untrusted_snapshot() -> None:
    project = _project(with_circuit=True)
    changed = _request(length_m=80)
    result = _run(project, ScriptedClient([_tool_turn(
        "propose_changes",
        {"ops": [{"op": "edit", "circuit_id": "c-1", "request": changed}]},
    )]))

    assert result.proposal is not None
    diff = result.proposal.diff.circuits[0]
    assert diff.before is not None and diff.after is not None
    assert diff.before.section_mm2 != 999
    assert diff.after.voltage_drop_pct >= diff.before.voltage_drop_pct


def test_parse_compute_and_propose_tools_share_typed_request() -> None:
    client = ScriptedClient([
        _tool_turn("parse_circuit", {"text": "конвейер"}),
        _tool_turn("compute", {"request": _request()}),
        _tool_turn("propose_changes", {"ops": [{"op": "add", "request": _request()}]}),
    ])

    result = _run(_project(), client)

    assert result.ok and result.proposal is not None
    assert client.calls == 3 and client.intake_calls == 1


def test_board_state_exposes_inputs_but_not_snapshots_or_import_evidence() -> None:
    client = ScriptedClient([
        _tool_turn("get_board_state", {}),
        {"content": "Состояние прочитано.", "tool_calls": []},
    ])

    result = _run(_project(with_circuit=True), client)

    assert result.ok
    tool_message = next(
        item for item in client.seen_messages[1] if item["role"] == "tool"
    )
    state = json.loads(tool_message["content"])
    circuit = state["project"]["circuits"][0]
    assert circuit["request"]["installation"]["length_m"] == 18
    assert "result" not in circuit and "signoff" not in circuit
    assert "import_declaration" not in circuit["meta"]


def test_validation_error_feeds_back_to_the_model_instead_of_aborting() -> None:
    client = ScriptedClient([
        _tool_turn("propose_changes", {"ops": [{"op": "delete", "circuit_id": "missing"}]}),
        _tool_turn(
            "propose_changes",
            {"ops": [{"op": "add", "ref": "M2", "request": _request()}]},
            content="Исправил: добавляю новую цепь.",
        ),
    ])

    result = _run(_project(), client)

    assert result.ok and result.proposal is not None
    assert client.calls == 2
    error_result = next(
        item for item in client.seen_messages[1]
        if item["role"] == "tool" and item["name"] == "propose_changes"
    )
    feedback = json.loads(error_result["content"])
    assert feedback["ok"] is False and feedback["error"] == "invalid_proposal"


def test_unrecovered_validation_error_ends_as_iteration_limit() -> None:
    bad_turn = _tool_turn(
        "propose_changes", {"ops": [{"op": "delete", "circuit_id": "missing"}]},
    )
    result = _run(_project(), ScriptedClient([deepcopy(bad_turn) for _ in range(6)]))

    assert not result.ok and result.error == "iteration_limit"
    assert result.proposal is None


def test_normcheck_explanation_iteration_limit_timeout_and_model_error() -> None:
    explained = _run(_project(with_circuit=True), ScriptedClient([
        _tool_turn("run_normcheck", {}),
        {"content": "R01 объяснён без новых инженерных значений.", "tool_calls": []},
    ]))
    assert explained.ok and explained.provenance_ok and explained.proposal is None

    repeated = [_tool_turn("get_board_state", {}) for _ in range(6)]
    limited = _run(_project(), ScriptedClient(repeated))
    assert limited.error == "iteration_limit" and limited.incomplete

    ticks = iter([0.0, 0.0, 0.0, 46.0])
    timed_out = _run(
        _project(),
        ScriptedClient([_tool_turn("get_board_state", {})]),
        clock=lambda: next(ticks),
    )
    assert timed_out.error == "timeout" and timed_out.incomplete

    broken = _run(_project(), ScriptedClient([]))
    assert broken.error == "model_error" and broken.proposal is None


_PLAN_IMAGE = "data:image/jpeg;base64,QUJDRA=="
_PLAN_PDF = "data:application/pdf;base64,QUJDRA=="


def test_attached_plans_become_multimodal_user_parts() -> None:
    client = ScriptedClient([{"content": "Распознал кухню и санузел.", "tool_calls": []}])

    result = run_copilot(
        _project(),
        "составь цепи по плану",
        [],
        client=client,
        model="fake/strong",
        parse_model="fake/fast",
        attachments=[_PLAN_IMAGE, _PLAN_PDF],
    )

    assert result.ok
    user = next(item for item in client.seen_messages[0] if item["role"] == "user")
    assert user["content"] == [
        {"type": "text", "text": "составь цепи по плану"},
        {"type": "image_url", "image_url": {"url": _PLAN_IMAGE}},
        {"type": "file", "file": {"filename": "plan-2.pdf", "file_data": _PLAN_PDF}},
    ]


def test_no_attachments_keeps_plain_string_user_message() -> None:
    client = ScriptedClient([{"content": "ок", "tool_calls": []}])

    _run(_project(), client)

    user = next(item for item in client.seen_messages[0] if item["role"] == "user")
    assert user["content"] == "сделай изменение"


def test_copilot_request_rejects_non_plan_or_oversized_attachments() -> None:
    base = {"project": _project().model_dump(mode="json"), "message": "план"}

    accepted = CopilotRequest.model_validate({**base, "attachments": [_PLAN_IMAGE, _PLAN_PDF]})
    assert accepted.attachments == [_PLAN_IMAGE, _PLAN_PDF]
    with pytest.raises(ValueError, match="PNG, JPEG, WebP или PDF"):
        CopilotRequest.model_validate({**base, "attachments": ["data:text/html;base64,QUJDRA=="]})
    with pytest.raises(ValueError, match="PNG, JPEG, WebP или PDF"):
        CopilotRequest.model_validate({**base, "attachments": ["https://example.com/plan.jpg"]})
    with pytest.raises(ValueError, match="слишком большое"):
        CopilotRequest.model_validate(
            {**base, "attachments": ["data:image/jpeg;base64," + "A" * 1_500_001]},
        )
    with pytest.raises(ValueError):
        CopilotRequest.model_validate({**base, "attachments": [_PLAN_IMAGE] * 3})


def test_reply_provenance_accepts_si_prefix_of_evidence_leaves() -> None:
    evidence = [{"request": {"load": {"power_w": 3600}}, "meta": {"rcd": {"ma": 30}}}]

    ok, unverified = check_reply_provenance("Духовка 3,6 кВт под УЗО 30 мА (0,03 А).", evidence)
    assert ok and unverified == []

    ok, unverified = check_reply_provenance("Поставьте 5 кВт.", evidence)
    assert not ok and unverified == ["5"]


def test_unverified_reply_number_is_flagged_without_changing_project() -> None:
    project = _project()
    before = deepcopy(project.model_dump(mode="json"))
    result = _run(project, ScriptedClient([{
        "content": "Поставьте аппарат 999 A.",
        "tool_calls": [],
    }]))

    assert result.ok and not result.provenance_ok
    assert result.unverified_numbers == ["999"]
    assert project.model_dump(mode="json") == before

    scientific = _run(project, ScriptedClient([{
        "content": "Поставьте аппарат 1e6 A.",
        "tool_calls": [],
    }]))
    assert scientific.unverified_numbers == ["1e6"]


def test_api_no_key_and_frontend_keep_copilot_explicit_and_read_only(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        studio_api,
        "get_config",
        lambda: SimpleNamespace(llm_available=False),
    )
    response = TestClient(studio_api.app).post(
        "/api/copilot",
        json={"project": _project().model_dump(mode="json"), "message": "помоги", "history": []},
    )

    assert response.status_code == 200
    assert response.json()["error"] == "no_key"
    assert "нормоконтроль" in response.json()["reply"]

    root = Path(__file__).parents[1]
    app_js = (root / "web/app.js").read_text(encoding="utf-8")
    html = (root / "web/index.html").read_text(encoding="utf-8")
    assert "BOARD_PROPOSAL" in app_js and "COPILOT_UNDO" in app_js
    assert "UNSIGNED_ADVISORY" in app_js
    assert 'id="boardProposalApply"' in html
    shared = html.split('id="screen-shared"', 1)[1].split('id="screen-project"', 1)[0]
    assert "boardCopilot" not in shared and "boardProposalApply" not in shared


def test_api_passes_attachments_to_the_copilot_loop(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_run_copilot(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        from electricopilot.copilot.models import CopilotResponse
        return CopilotResponse(ok=True, reply="ок", model="fake/strong")

    monkeypatch.setattr(
        studio_api,
        "get_config",
        lambda: SimpleNamespace(
            llm_available=True,
            llm_admission_mode="local",
            model_strong="fake/strong",
            model_fast="fake/fast",
        ),
    )
    monkeypatch.setattr(studio_api, "run_copilot", fake_run_copilot)

    response = TestClient(studio_api.app).post(
        "/api/copilot",
        json={
            "project": _project().model_dump(mode="json"),
            "message": "план кухни",
            "history": [],
            "attachments": [_PLAN_IMAGE, _PLAN_PDF],
        },
    )

    assert response.status_code == 200
    assert captured["attachments"] == [_PLAN_IMAGE, _PLAN_PDF]


def test_api_maps_malformed_tool_call_to_typed_422(monkeypatch: Any) -> None:
    # Validation errors now feed back to the model (see the feedback test above); the
    # 422 path remains for protocol violations — a tool call without an id is one.
    scripted = ScriptedClient([{
        "content": "",
        "tool_calls": [{"id": "", "name": "get_board_state", "arguments": {}}],
    }])
    monkeypatch.setattr(
        studio_api,
        "get_config",
        lambda: SimpleNamespace(
            llm_available=True,
            model_strong="fake/strong",
            model_fast="fake/fast",
        ),
    )
    monkeypatch.setattr(studio_api, "_client", lambda: scripted)

    response = TestClient(studio_api.app).post(
        "/api/copilot",
        json={"project": _project().model_dump(mode="json"), "message": "удали", "history": []},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_tool"
    assert response.json()["detail"]["response"]["proposal"] is None


def test_openrouter_client_normalizes_function_call_without_network(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {
                "content": "готово",
                "tool_calls": [{
                    "id": "call-7",
                    "function": {"name": "get_board_state", "arguments": "{}"},
                }],
            }}]}

    def fake_post(*_: Any, **kwargs: Any) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("electricopilot.llm.client.httpx.post", fake_post)
    client = OpenRouterClient(Config(
        openrouter_api_key="test-key",
        openrouter_base_url="https://example.invalid/api/v1",
        model_strong="fake/strong",
        model_fast="fake/fast",
        app_title="test",
        http_referer="https://example.invalid",
        database_url="",
        strict_provenance=True,
        llm_timeout=60,
    ))

    turn = client.complete_tools(
        model="fake/strong",
        messages=[{"role": "user", "content": "состояние"}],
        tools=[],
        timeout=10,
    )

    assert turn["tool_calls"] == [{
        "id": "call-7", "name": "get_board_state", "arguments": {},
    }]
    assert captured["timeout"] == pytest.approx(7.0)
    assert captured["json"]["tool_choice"] == "auto"
    assert captured["json"]["max_tokens"] == 8192
    assert captured["json"]["reasoning"] == {"effort": "low"}


def test_openrouter_client_retries_a_thinking_only_empty_turn(monkeypatch: Any) -> None:
    """A reasoning model can spend the whole budget thinking (finish_reason=length) and
    return neither content nor tool calls; the client must retry once, then surface a
    diagnosable error with the finish_reason instead of a bare 'empty' message."""
    responses: list[dict[str, Any]] = [
        {"choices": [{"finish_reason": "length", "message": {"content": None}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "готово"}}]},
    ]
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_post(*_: Any, **__: Any) -> FakeResponse:
        payload = responses[calls["n"]]
        calls["n"] += 1
        return FakeResponse(payload)

    monkeypatch.setattr("electricopilot.llm.client.httpx.post", fake_post)
    config = Config(
        openrouter_api_key="test-key",
        openrouter_base_url="https://example.invalid/api/v1",
        model_strong="fake/strong",
        model_fast="fake/fast",
        app_title="test",
        http_referer="https://example.invalid",
        database_url="",
        strict_provenance=True,
        llm_timeout=60,
    )

    turn = OpenRouterClient(config).complete_tools(
        model="fake/strong", messages=[], tools=[], timeout=10,
    )
    assert turn["content"] == "готово" and calls["n"] == 2

    calls["n"] = 0
    responses[1] = responses[0]
    with pytest.raises(Exception, match="finish_reason=length"):
        OpenRouterClient(config).complete_tools(
            model="fake/strong", messages=[], tools=[], timeout=10,
        )
