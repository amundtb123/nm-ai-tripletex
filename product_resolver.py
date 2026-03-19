"""Resolve Tripletex products by name or product number (simple heuristics)."""

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


def search_products(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str | None = None,
    product_number: str | None = None,
    count: int = 100,
) -> list[dict[str, Any]]:
    """
    GET /product with ``name`` (contains) and/or ``productNumber`` filter.

    Tripletex exposes ``productNumber`` as an array in OpenAPI; ``requests`` encodes
    a list as repeated query keys where supported.
    """
    params: dict[str, Any] = {"from": 0, "count": min(count, 1000)}
    has_filter = False
    if name and name.strip():
        params["name"] = name.strip()
        has_filter = True
    if product_number and product_number.strip():
        params["productNumber"] = [product_number.strip()]
        has_filter = True
    if not has_filter:
        return []

    data = tripletex_json(client, log, "GET", "/product", params=params)
    values = _unwrap_value(data)
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def search_products_fallback(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str = "",
    product_number: str = "",
    count: int = 100,
) -> list[dict[str, Any]]:
    """
    Same filter/fallback sequence as ``workflow_search_product``:
    both → number only → name only.
    """
    pname = name.strip()
    pnum = product_number.strip()
    if not pname and not pnum:
        return []
    matches: list[dict[str, Any]] = []
    if pnum and pname:
        matches = search_products(
            client,
            log,
            name=pname,
            product_number=pnum,
            count=count,
        )
    if not matches and pnum:
        matches = search_products(client, log, product_number=pnum, count=count)
    if not matches and pname:
        matches = search_products(client, log, name=pname, count=count)
    return matches


def resolve_product_by_name_or_number(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str = "",
    product_number: str = "",
) -> dict[str, Any] | None:
    """Return best product row or ``None`` if nothing matches."""
    matches = search_products_fallback(
        client, log, name=name, product_number=product_number
    )
    if not matches:
        return None
    return pick_best_product_match(
        matches,
        query_name=name,
        query_number=product_number,
        log=log,
    )


def pick_best_product_match(
    candidates: list[dict[str, Any]],
    *,
    query_name: str = "",
    query_number: str = "",
    log: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """Prefer exact name or exact number match; then substring; else first row."""
    if not candidates:
        return None

    qn = (query_number or "").strip()
    if qn:
        for row in candidates:
            num = str(row.get("number") or "").strip()
            if num == qn:
                return row

    qname = (query_name or "").strip().casefold()
    if qname:
        for row in candidates:
            nm = str(row.get("name") or "").strip().casefold()
            if nm == qname:
                return row
        for row in candidates:
            nm = str(row.get("name") or "").strip().casefold()
            if qname in nm or nm in qname:
                return row

    if log is not None and len(candidates) > 1:
        log.info(
            json.dumps(
                {
                    "event": "product_resolver_ambiguous",
                    "picked": "first_result",
                    "candidate_count": len(candidates),
                },
                ensure_ascii=False,
            )
        )
    return candidates[0]
