"""Sanity checks for Tripletex credentials (no secrets logged)."""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlparse


def tripletex_base_url_placeholder_like(base_url: str) -> bool:
    """Heuristic: URL still looks like a template or non-production placeholder."""
    raw = base_url.strip()
    if not raw:
        return True
    low = raw.lower()
    markers = (
        "paste_",
        "your_",
        "replace_me",
        "changeme",
        "here>",
        "<http",
        "todo",
        "fixme",
        "xxx",
    )
    if any(m in low for m in markers):
        return True
    if "<" in raw or ">" in raw:
        return True
    try:
        p = urlparse(raw)
        host = (p.hostname or "").lower()
        if host.endswith(".example.com") or host in ("example.com", "invalid"):
            return True
    except Exception:
        return True
    return False


def tripletex_session_token_placeholder_like(session_token: str) -> bool:
    """Heuristic: token still looks like a template (conservative — avoid false positives)."""
    raw = session_token.strip()
    if not raw:
        return True
    low = raw.lower()
    if low in (
        "<session-token>",
        "session-token",
        "changeme",
        "your-token-here",
    ):
        return True
    if low.startswith("paste_") or low.startswith("your_"):
        return True
    if "paste_sandbox" in low:
        return True
    if raw.startswith("<") and raw.endswith(">"):
        return True
    # Example files use this literal
    if low == "paste_sandbox_session_token":
        return True
    return False


def tripletex_credentials_valid_for_api(
    base_url: str,
    session_token: str,
) -> Tuple[bool, Optional[str]]:
    """
    Return (ok, norwegian_error_detail).

    Call only before workflows that hit Tripletex; do not log token value.
    """
    url = base_url.strip()
    token = session_token.strip()

    if tripletex_session_token_placeholder_like(token):
        return (
            False,
            "session_token ser ut som en plassholder eller mangler. Bytt til ekte "
            "session token fra Tripletex sandbox-siden (eksempler i examples/ bruker PASTE_*).",
        )

    try:
        p = urlparse(url)
    except Exception:
        return False, "base_url er ikke en gyldig URL."

    if p.scheme.lower() != "https":
        return (
            False,
            "base_url må bruke https (Tripletex API). Sjekk URL fra sandbox-siden.",
        )

    if not p.netloc:
        return False, "base_url mangler vertsnavn (nettsted). Sjekk sandbox API-URL."

    if tripletex_base_url_placeholder_like(url):
        return (
            False,
            "base_url ser ut som en plassholder eller ugyldig eksempel-vert. "
            "Bruk nøyaktig API-URL fra Tripletex sandbox-siden.",
        )

    path = (p.path or "").rstrip("/")
    if path != "/v2":
        return (
            False,
            "base_url må peke på Tripletex API v2, vanligvis med sti /v2 "
            "(f.eks. https://…/v2). Sjekk hva sandbox-siden viser.",
        )

    host = (p.hostname or "").lower()
    if host.endswith(".example.com") or host in ("example.com", "invalid"):
        return False, "base_url peker på et eksempeldomene — bruk sandboxens API-vertsnavn."

    return True, None
