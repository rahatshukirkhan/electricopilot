"""Private loopback server used only by the Playwright browser smoke suite."""
from __future__ import annotations

import os

import uvicorn

from electricopilot import studio_api
from electricopilot.store import MemoryStore


if os.environ.get("ELECTRICOPILOT_E2E_STORE") == "memory":
    # This module is never imported by the production application. Keeping the override in a
    # separate process lets the browser exercise sync/share without a database or test-only API.
    studio_api._PROJECT_STORE_OVERRIDE = MemoryStore()


if __name__ == "__main__":
    uvicorn.run(
        studio_api.app,
        host="127.0.0.1",
        port=int(os.environ["ELECTRICOPILOT_E2E_PORT"]),
        log_level="warning",
    )
