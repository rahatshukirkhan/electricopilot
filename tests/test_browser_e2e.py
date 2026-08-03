"""Real-browser smoke coverage for the local Studio SPA; external network is blocked."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright

pytestmark = pytest.mark.browser_e2e

if os.environ.get("ELECTRICOPILOT_BROWSER_E2E") != "1":
    pytest.skip(
        "set ELECTRICOPILOT_BROWSER_E2E=1 to run the Chromium Studio smoke suite",
        allow_module_level=True,
    )

ROOT = Path(__file__).parents[1]
ARTIFACTS = ROOT / ".e2e-artifacts"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StudioServer:
    """A disposable loopback-only Studio process with optional test-local MemoryStore."""

    def __init__(self, memory_store: bool) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("DATABASE_URL", None)
        env["ELECTRICOPILOT_E2E_PORT"] = str(self.port)
        env["ELECTRICOPILOT_E2E_STORE"] = "memory" if memory_store else "disabled"
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "e2e_server.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                output = self.process.stdout.read() if self.process.stdout else ""
                raise RuntimeError(f"Studio E2E server exited early:\n{output}")
            try:
                with urlopen(f"{self.url}/api/health", timeout=0.25) as response:
                    if response.status == 200:
                        return
            except URLError:
                pass
            time.sleep(0.05)
        self.close()
        raise RuntimeError("Studio E2E server did not become ready on loopback")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.fixture
def studio_server() -> Iterator[Callable[[bool], StudioServer]]:
    servers: list[StudioServer] = []

    def start(memory_store: bool) -> StudioServer:
        server = StudioServer(memory_store)
        servers.append(server)
        return server

    yield start
    for server in reversed(servers):
        server.close()


@pytest.fixture
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as instance:
        yield instance


@pytest.fixture
def chromium(playwright: Playwright) -> Iterator[Browser]:
    browser = playwright.chromium.launch()
    yield browser
    browser.close()


def _allow_loopback_only(route: Any) -> None:
    hostname = urlparse(route.request.url).hostname
    if hostname in {"127.0.0.1", "localhost"}:
        route.continue_()
    else:
        route.abort()


@pytest.fixture
def page(chromium: Browser, request: pytest.FixtureRequest) -> Iterator[Page]:
    context = chromium.new_context()
    context.route("**/*", _allow_loopback_only)
    page = context.new_page()
    console_messages: list[str] = []
    page.on("console", lambda message: console_messages.append(f"{message.type}: {message.text}"))
    yield page
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        ARTIFACTS.mkdir(exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
        page.screenshot(path=str(ARTIFACTS / f"{safe_name}.png"), full_page=True)
        (ARTIFACTS / f"{safe_name}.console.json").write_text(
            json.dumps(console_messages[-50:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    context.close()


def _wait_for_project(page: Page) -> str:
    expect(page.locator("#screen-project")).to_be_visible()
    expect(page.locator("#scheduleBody tr").first).to_have_count(1)
    return str(page.evaluate("location.hash.split('/')[2]"))


def _load_sample(page: Page, server: StudioServer) -> str:
    page.goto(server.url)
    expect(page.locator("#modeTag")).to_have_text("ИИ: резервный режим")
    # "Загрузить пример" lives inside the dashboard's "Ещё" menu (design-v2-spec §2.5) — open it first.
    page.get_by_role("button", name="Ещё", exact=True).click()
    with page.expect_response(lambda response: "/api/project-report" in response.url) as report:
        page.get_by_role("button", name="Загрузить пример").click()
    assert report.value.ok
    return _wait_for_project(page)


def _write_csv(path: Path) -> None:
    path.write_text(
        "Ref;Описание;P, кВт;U, В;Фазы;cos φ;Длина, м;Аппарат;In, А;Кривая;УЗО, мА;"
        "Сечение, мм²;Материал;Изоляция;Метод\n"
        "C1;Розетки;3,68;230;1;0,95;18;MCB;20;C;30;2,5;Cu;PVC;C\n",
        encoding="utf-8",
    )


def test_import_review_requires_confirmation_and_creates_schema_v2_project(
    page: Page,
    studio_server: Callable[[bool], StudioServer],
    tmp_path: Path,
) -> None:
    server = studio_server(False)
    fixture = tmp_path / "schedule.csv"
    _write_csv(fixture)

    page.goto(server.url)
    page.locator("#scheduleImportFile").set_input_files(str(fixture))
    expect(page.locator("#importReview")).to_be_visible()
    expect(page.locator("#importMappingBody select").first).to_be_visible()
    expect(page.locator("#importConfirm")).not_to_be_checked()
    expect(page.locator("#btnImportCreate")).to_be_disabled()

    page.locator("#importConfirm").check()
    with page.expect_response(lambda response: "/api/project-validate" in response.url) as validation:
        page.get_by_role("button", name="Создать проект").click()
    assert validation.value.ok
    project_id = _wait_for_project(page)
    project = page.evaluate("id => JSON.parse(localStorage.getItem('ec_v2_project_' + id))", project_id)
    assert project["schema_version"] == 2
    assert len(project["circuits"]) == 1
    expect(page.locator("#scheduleBody")).to_contain_text("Розетки")


def test_local_fallback_keeps_fresh_report_and_explains_unavailable_share(
    page: Page,
    studio_server: Callable[[bool], StudioServer],
) -> None:
    server = studio_server(False)
    project_id = _load_sample(page, server)
    page.evaluate(
        """id => {
            const key = 'ec_v2_project_' + id;
            const project = JSON.parse(localStorage.getItem(key));
            project.circuits[0].result = {status: 'PASS', IB: 99999, Iz: 99999, vd: 0};
            localStorage.setItem(key, JSON.stringify(project));
        }""",
        project_id,
    )
    page.reload()
    expect(page.locator("#scheduleBody")).not_to_contain_text("99999")
    expect(page.locator("#sldPreview svg")).to_have_count(1)
    page.get_by_role("button", name="Нормоконтроль").click()
    expect(page.locator("#normFindings")).not_to_contain_text("не запускался")
    page.get_by_role("button", name="Share-ссылка").click()
    expect(page.locator("#toast")).to_contain_text("требует DATABASE_URL")
    assert page.evaluate(
        "id => localStorage.getItem('ec_v2_project_' + id) !== null",
        project_id,
    )


def test_memory_store_sync_conflict_and_read_only_share(
    chromium: Browser,
    studio_server: Callable[[bool], StudioServer],
) -> None:
    server = studio_server(True)
    context_a = chromium.new_context()
    context_b = chromium.new_context()
    for context in (context_a, context_b):
        context.route("**/*", _allow_loopback_only)
    page_a = context_a.new_page()
    page_b = context_b.new_page()
    try:
        project_id = _load_sample(page_a, server)
        with page_a.expect_response(lambda response: "/api/projects/" in response.url and response.request.method == "PUT") as put:
            page_a.locator("#b_name").fill("Щит E2E")
        assert put.value.ok

        workspace = page_a.evaluate("JSON.parse(localStorage.getItem('ec_v2_workspace')).id")
        context_b.add_init_script(
            "localStorage.setItem('ec_v2_workspace', JSON.stringify({"
            f"schema_version: 2, id: {json.dumps(workspace)}, created_at: '2026-01-01T00:00:00Z'"
            "}));",
        )
        page_b.goto(server.url)
        expect(page_b.locator("#projectGrid")).to_contain_text("Щит E2E")
        page_b.evaluate(
            """id => {
                const key = 'ec_v2_project_' + id;
                const project = JSON.parse(localStorage.getItem(key));
                project.updated_at = '2099-01-01T00:00:00Z';
                localStorage.setItem(key, JSON.stringify(project));
            }""",
            project_id,
        )
        with page_b.expect_response(
            lambda response: "/api/projects/" in response.url and response.request.method == "PUT",
        ) as future_put:
            page_b.reload()
        assert future_put.value.ok

        with page_a.expect_response(lambda response: response.status == 409 and "/api/projects/" in response.url):
            page_a.locator("#b_location").fill("Конфликт E2E")
        expect(page_a.locator("#toast")).to_contain_text("обновлён с другого устройства")

        captured_link: list[str] = []
        page_a.on("dialog", lambda dialog: (captured_link.append(dialog.default_value), dialog.accept()))
        page_a.get_by_role("button", name="Share-ссылка").click()
        expect(page_a.locator("#toast")).to_contain_text("Share-ссылка")
        if captured_link:
            token = captured_link[0].split("#/s/", 1)[1]
            page_a.goto(f"{server.url}#/s/{token}")
        else:
            page_a.get_by_role("button", name="Открыть").click()
        expect(page_a.locator("#screen-shared")).to_be_visible()
        expect(page_a.locator("#sharedIdentity")).to_contain_text("UNSIGNED_ADVISORY")
        expect(page_a.locator("#screen-shared button")).to_have_count(0)
    finally:
        context_a.close()
        context_b.close()
