#!/usr/bin/env python3
"""Regenerate the truncated adilet HTML fixtures in `tests/fixtures/norms/` (docs/20 §12.1).

Fixtures must be ≤ 200 KB each, so each one is the head of a real page: everything up to and
including the document header (chrome, `<h1>`, freshness badge, registration line, tab row) plus
as much of `<article>` as fits, cut at a tag boundary and explicitly closed. Closing the cut tags
matters: it removes parser error-recovery from the §5 determinism invariant.

The synthetic status-conflict fixture (docs/20 §12.3) is NOT produced here — it is hand-written
and committed, because no real adilet page exhibits that combination.

Usage:
    uv run python scripts/make_norm_fixtures.py            # fetch live pages and rewrite
    uv run python scripts/make_norm_fixtures.py --cache-dir runs/norms-cache
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from electricopilot.norms.fetch import fetch_document

DOC_IDS = ("V1500010851", "V1900019361", "V2300032783")
MAX_BYTES = 195_000  # stay under the docs/20 §12.1 ceiling of 200 KB
CLOSERS = "\n</article></div></div></div></body></html>\n"
# Cut only right after one of these, so no element is left half-open before CLOSERS.
BOUNDARIES = ("</p>", "</h3>", "</h4>", "</table>", "</div>")


def truncate(html: str) -> str:
    article = html.find("<article")
    if article < 0:
        raise ValueError("no <article> in page")
    budget = MAX_BYTES - len(CLOSERS.encode("utf-8"))
    # walk back from the byte budget to the last safe tag boundary
    cut = len(html)
    while len(html[:cut].encode("utf-8")) > budget:
        cut = int(cut * budget / len(html[:cut].encode("utf-8")))
    best = max(html.rfind(b, article, cut) + len(b) for b in BOUNDARIES)
    if best <= article:
        raise ValueError("no tag boundary found inside the byte budget")
    return html[:best] + CLOSERS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=Path("runs/norms-cache"))
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests/fixtures/norms",
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for doc_id in DOC_IDS:
        html = fetch_document(doc_id, cache_dir=args.cache_dir)
        fixture = truncate(html)
        path = args.out / f"{doc_id}.html"
        path.write_text(fixture, encoding="utf-8")
        print(f"wrote {path} ({len(fixture.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
