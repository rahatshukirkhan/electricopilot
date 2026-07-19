"""Create the phase-4 project/share tables without altering sizing_sessions."""
from __future__ import annotations

from electricopilot.config import get_config
from electricopilot.store import PostgresStore


def main() -> None:
    dsn = get_config().database_url
    if not dsn:
        raise SystemExit("DATABASE_URL is required; no database changes were made")
    PostgresStore(dsn).init_schema()
    print("Project/share schema is ready.")


if __name__ == "__main__":
    main()
