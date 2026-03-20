"""Resolve Tripletex products by name or product number (simple heuristics)."""

from __future__ import annotations

import json
import logging
from typing import Any

from tripletex_client import TripletexClient
from tripletex_list import tripletex_list_rows_from_response
from tripletex_request import tripletex_json


def search_products(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str | None = None,
    product_number: str | None = None,
    count: int = 100,
) -> tuple[list[dict[str, Any]], str, int | None]:
    """
    GET /product with ``name`` (contains) and/or ``productNumber`` filter.

    Tripletex exposes ``productNumber`` as an array in OpenAPI; ``requests`` encodes
    a list as repeated query keys where supported.

    Returns ``(rows, list_payload_extract, fullResultSize or None)``.
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
        return [], "no_filter", None

    data = tripletex_json(client, log, "GET", "/product", params=params)
    rows, tag, full_size = tripletex_list_rows_from_response(data)
    if full_size is not None and full_size != len(rows):
        log.info(
            json.dumps(
                {
                    "event": "tripletex_list_count_hint",
                    "resource": "product",
                    "list_payload_extract": tag,
                    "api_full_result_size": full_size,
                    "rows_in_page": len(rows),
                },
                ensure_ascii=False,
            )
        )
    return rows, tag, full_size


def search_products_fallback(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str = "",
    product_number: str = "",
    count: int = 100,
) -> tuple[list[dict[str, Any]], str, int | None]:
    """
    Same filter/fallback sequence as ``workflow_search_product``:
    both → number only → name only.

    Returns rows and metadata from the **last** GET attempt (same as final ``matches`` source).
    """
    pname = name.strip()
    pnum = product_number.strip()
    if not pname and not pnum:
        return [], "empty", None
    matches: list[dict[str, Any]] = []
    tag_out = "empty"
    frs_out: int | None = None
    if pnum and pname:
        matches, tag_out, frs_out = search_products(
            client,
            log,
            name=pname,
            product_number=pnum,
            count=count,
        )
    if not matches and pnum:
        matches, tag_out, frs_out = search_products(client, log, product_number=pnum, count=count)
    if not matches and pname:
        matches, tag_out, frs_out = search_products(client, log, name=pname, count=count)
    return matches, tag_out, frs_out


def resolve_product_by_name_or_number(
    client: TripletexClient,
    log: logging.Logger,
    *,
    name: str = "",
    product_number: str = "",
) -> dict[str, Any] | None:
    """Return best product row or ``None`` if nothing matches."""
    matches, _, _ = search_products_fallback(
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
                    "query_name": (query_name or "")[:120],
                    "query_number": (query_number or "")[:40],
                },
                ensure_ascii=False,
            )
        )
    return candidates[0]
