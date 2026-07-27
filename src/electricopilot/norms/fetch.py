"""HTTP access to adilet.zan.kz for the norm library (docs/20 §4.1–4.2).

Single fetcher for the whole project: `scripts/extract_pue_rk.py` imports from here instead
of carrying its own copy (docs/20 §4.1 — "дублирования быть не должно").

Policy (docs/20 §1.4): only `/rus/docs/{ID}` (and its `/info`, `/history`, `/links` tabs) is
requested. adilet's `robots.txt` disallows `/rus/search/`, `/rus/list/docs/` and `/files/`, so
the search endpoint is NOT used at all — new document IDs are resolved through the link graph
of already-fetched documents. legality's `enumerateYear*` helpers are deliberately NOT ported.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import httpx

from electricopilot.exceptions import ElectriCopilotError

ADILET_BASE = "https://adilet.zan.kz"
USER_AGENT = "Mozilla/5.0"
DEFAULT_TIMEOUT = 60.0

#: Where `fetch_document` keeps raw HTML between runs (docs/20 §4.1). Git-ignored via ./runs/.
DEFAULT_CACHE_DIR = Path("runs/norms-cache")

# --- §4.2 integrity gate ---------------------------------------------------------------
# A "successful" HTTP 200 from adilet is not proof of a document: the bulk re-fetch in
# legality tripped an anti-bot wall and got back an identical 20623-byte
# "Подтвердите, что Вы не робот" CAPTCHA shell for 43% of documents, which a naive
# length>200 guard happily stored as a document. These three cheap invariants of a real
# `/rus/docs/{ID}` page are checked before anything is persisted, and a failed check must
# NEVER overwrite an already-saved good version.
MIN_HTML_BYTES = 20 * 1024
INTEGRITY_MARKER = "Назад к документу"
_Z_ANCHOR_RE = re.compile(r'(?:id|name)="(z\d+)"')


class NormFetchError(ElectriCopilotError):
    """A norm document could not be downloaded, or the response failed the integrity gate."""


def doc_url(doc_id: str, *, lang: str = "rus") -> str:
    """Canonical page URL of an adilet document."""
    return f"{ADILET_BASE}/{lang}/docs/{doc_id}"


def passes_integrity_gate(html: str) -> bool:
    """True if `html` looks like a real adilet document page (docs/20 §4.2)."""
    if len(html.encode("utf-8")) < MIN_HTML_BYTES:
        return False
    if INTEGRITY_MARKER not in html:
        return False
    return _Z_ANCHOR_RE.search(html) is not None


def fetch_url(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """GET `url` as text, with a curl fallback for adilet's incomplete TLS chain."""
    try:
        resp = httpx.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
        )
        resp.raise_for_status()
        return resp.text
    except httpx.ConnectError:
        # adilet.zan.kz serves an incomplete TLS chain (missing intermediate) that OpenSSL's
        # root-only bundle can't complete, so httpx fails where curl (system trust store,
        # builds the chain) succeeds. Fall back to curl — it STILL verifies the certificate
        # (no -k), which matters for a norm source (a MITM must not swap in wrong values).
        return _fetch_via_curl(url, timeout=timeout)


def _fetch_via_curl(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise NormFetchError(
            f"httpx could not verify the TLS chain of {url} and curl is not available. "
            "Download the page manually (browser/curl) and pass it via a local cache file."
        )
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, verified cert (no -k)
        [curl, "-fsSL", "-A", USER_AGENT, url],
        capture_output=True,
        timeout=max(timeout, DEFAULT_TIMEOUT) * 2,
        check=True,
    )
    return out.stdout.decode("utf-8")


def fetch_cached(url: str, cache: Path | None, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Read `cache` if it exists, otherwise download `url` and write it there.

    Plain read-through caching with no integrity gate: used by `scripts/extract_pue_rk.py`,
    which does its own table-level sanity checks on the parsed content. Norm-library ingest
    goes through `fetch_document` instead.
    """
    if cache is not None and cache.is_file():
        return cache.read_text(encoding="utf-8")
    html = fetch_url(url, timeout=timeout)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(html, encoding="utf-8")
    return html


def fetch_document(
    doc_id: str,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Fetch one `/rus/docs/{doc_id}` page through the §4.2 integrity gate.

    A response that fails the gate raises and leaves any cached good copy untouched — an
    anti-bot shell must never replace a real document on disk.
    """
    cache = None if cache_dir is None else cache_dir / f"{doc_id}.html"
    if cache is not None and cache.is_file() and not refresh:
        return cache.read_text(encoding="utf-8")

    html = fetch_url(doc_url(doc_id), timeout=timeout)
    if not passes_integrity_gate(html):
        raise NormFetchError(
            f"{doc_id}: ответ не похож на страницу документа adilet "
            f"({len(html.encode('utf-8'))} байт, маркер/z-якоря не найдены) — "
            "сохранённая версия не перезаписана."
        )
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(html, encoding="utf-8")
    return html
