"""Resolve Tripletex customers by name (single best match)."""

from __future__ import annotations

import json
import logging
from typing import Any

from tripletex_client import TripletexClient
from tripletex_request import tripletex_json


def _unwrap_value(data: Any) -> Any:
    if isinstance(data, dict) and "value" in data:
        return data["value"]
    return data


def search_customer_by_name(
    client: TripletexClient,
    log: logging.Logger,
    name: str,
    *,
    count: int = 100,
) -> list[dict[str, Any]]:
    """Return raw customer dicts from Tripletex (may be empty)."""
    stripped = name.strip()
    if not stripped:
        return []
    data = tripletex_json(
        client,
        log,
        "GET",
        "/customer",
        params={"customerName": stripped, "from": 0, "count": count},
    )
    values = _unwrap_value(data)
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def pick_best_customer_match(
    candidates: list[dict[str, Any]],
    query_name: str,
    *,
    log: logging.Logger,
) -> dict[str, Any] | None:
    """
    Choose a single customer when Tripletex returns multiple rows.

    Priority: exact case-insensitive name/displayName → name contains query → first row.
    """
    if not candidates:
        return None
    q = query_name.strip().casefold()
    if not q:
        return candidates[0]

    for row in candidates:
        for key in ("name", "displayName"):
            label = str(row.get(key) or "").strip()
            if label.casefold() == q:
                return row

    for row in candidates:
        nm = str(row.get("name") or "").strip().casefold()
        if q in nm or nm in q:
            return row

    log.info(
        json.dumps(
            {
                "event": "customer_resolver_ambiguous",
                "picked": "first_result",
                "candidate_count": len(candidates),
            },
            ensure_ascii=False,
        )
    )
    return candidates[0]


def resolve_customer_by_name(
    client: TripletexClient,
    log: logging.Logger,
    name: str,
) -> dict[str, Any] | None:
    """Search by name and return one best match, or None if no hits."""
    cands = search_customer_by_name(client, log, name)
    if not cands:
        return None
    return pick_best_customer_match(cands, name, log=log)
