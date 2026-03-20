"""Resolve Tripletex customers by name (single best match)."""

from __future__ import annotations

import json
import logging
from typing import Any

from tripletex_client import TripletexClient
from tripletex_list import tripletex_list_rows_from_response
from tripletex_request import tripletex_json


def _customer_rows_from_list_response(
    data: Any,
) -> tuple[list[dict[str, Any]], str, int | None]:
    """
    Tripletex list/search responses use ``{"value": {"fullResultSize": n, "values": [...]}}``.
    Older code assumed ``value`` was a bare list; that yielded zero rows despite HTTP 200 + data.
    """
    rows, tag, frs = tripletex_list_rows_from_response(data)
    if tag == "no_extractable_rows":
        tag = "no_extractable_customer_rows"
    return rows, tag, frs


def search_customer_by_name_with_meta(
    client: TripletexClient,
    log: logging.Logger,
    name: str,
    *,
    count: int = 100,
) -> tuple[list[dict[str, Any]], str, int | None]:
    """GET /customer by name; return rows, diagnostic tag, and ``fullResultSize`` when present."""
    stripped = name.strip()
    if not stripped:
        return [], "empty_query", None
    data = tripletex_json(
        client,
        log,
        "GET",
        "/customer",
        params={"customerName": stripped, "from": 0, "count": count},
    )
    return _customer_rows_from_list_response(data)


def search_customer_by_name(
    client: TripletexClient,
    log: logging.Logger,
    name: str,
    *,
    count: int = 100,
) -> list[dict[str, Any]]:
    """Return raw customer dicts from Tripletex (may be empty)."""
    rows, _, _ = search_customer_by_name_with_meta(client, log, name, count=count)
    return rows


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
                "query_name": query_name[:120],
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


def filter_exact_planned_name_matches(
    candidates: list[dict[str, Any]],
    planned_name: str,
) -> list[dict[str, Any]]:
    """
    Rows whose ``name`` or ``displayName`` equals ``planned_name`` after normalize.

    One row appears at most once (by ``id``). Used before ``POST /customer`` to skip
    creating a duplicate when an exact logical match already exists in search results.
    """
    q = _normalize_customer_label(planned_name)
    if not q:
        return []
    out: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid is not None and rid in seen_ids:
            continue
        hit = False
        for key in ("name", "displayName"):
            raw = str(row.get(key) or "").strip()
            if raw and _normalize_customer_label(raw) == q:
                hit = True
                break
        if hit:
            out.append(row)
            if rid is not None:
                seen_ids.add(rid)
    return out


def find_exact_customer_matches_for_create(
    client: TripletexClient,
    log: logging.Logger,
    planned_name: str,
) -> list[dict[str, Any]]:
    """``GET /customer`` with planned name, then filter to exact normalized name/display matches."""
    stripped = planned_name.strip()
    if not stripped:
        return []
    candidates, list_payload_extract, _full_result_size = search_customer_by_name_with_meta(
        client, log, stripped
    )
    q = _normalize_customer_label(stripped)
    api_candidate_count = len(candidates)

    ids_name: set[Any] = set()
    ids_dn: set[Any] = set()
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        row_key: Any = rid if rid is not None else id(row)
        nm = str(row.get("name") or "").strip()
        dn = str(row.get("displayName") or "").strip()
        if nm and _normalize_customer_label(nm) == q:
            ids_name.add(row_key)
        if dn and _normalize_customer_label(dn) == q:
            ids_dn.add(row_key)

    any_name = len(ids_name) > 0
    any_dn = len(ids_dn) > 0

    exact_rows = filter_exact_planned_name_matches(candidates, stripped)
    combined_count = len(exact_rows)

    log.info(
        json.dumps(
            {
                "event": "create_customer_precheck_search_result",
                "api_candidate_count": api_candidate_count,
                "list_payload_extract": list_payload_extract,
                "normalized_planned_label_length": len(q),
                "any_exact_name_field_match": any_name,
                "any_exact_displayname_field_match": any_dn,
                "name_field_exact_match_distinct_row_count": len(ids_name),
                "displayname_field_exact_match_distinct_row_count": len(ids_dn),
                "combined_exact_match_row_count": combined_count,
            },
            ensure_ascii=False,
            default=str,
        )
    )

    if any_name:
        log.info(
            json.dumps(
                {
                    "event": "create_customer_exact_match_found",
                    "distinct_row_count": len(ids_name),
                },
                ensure_ascii=False,
                default=str,
            )
        )
    if any_dn:
        log.info(
            json.dumps(
                {
                    "event": "create_customer_displayname_match_found",
                    "distinct_row_count": len(ids_dn),
                },
                ensure_ascii=False,
                default=str,
            )
        )

    if combined_count == 0:
        reason = (
            "no_api_rows"
            if api_candidate_count == 0
            else "api_rows_but_no_combined_exact_normalized_match"
        )
        log.info(
            json.dumps(
                {
                    "event": "create_customer_reuse_rejected",
                    "reason": reason,
                    "api_candidate_count": api_candidate_count,
                    "combined_exact_match_row_count": 0,
                },
                ensure_ascii=False,
                default=str,
            )
        )

    return exact_rows


def _normalize_customer_label(s: str) -> str:
    """Lowercase + collapse internal whitespace for stable comparison."""
    return " ".join(s.strip().split()).casefold()


def _row_primary_label(row: dict[str, Any]) -> str:
    for key in ("name", "displayName"):
        raw = str(row.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _invoice_customer_score(row: dict[str, Any], q_norm: str) -> tuple[int, int]:
    """
    Score how well a Tripletex customer row matches the invoice query name.

    Returns (score, tie_break_len) with higher score better; tie_break_len prefers
    longer matched labels when scores tie. score <= 0 means no usable match.
    """
    best_sc = 0
    best_len = 0
    for key in ("name", "displayName"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        nm = _normalize_customer_label(raw)
        if not nm:
            continue
        if nm == q_norm:
            sc, ln = 100, len(nm)
        elif nm.startswith(q_norm):
            sc, ln = 95, len(nm)
        elif q_norm in nm:
            sc, ln = 85, len(nm)
        elif nm in q_norm and len(nm) >= 3:
            sc, ln = 75, len(nm)
        else:
            continue
        if sc > best_sc or (sc == best_sc and ln > best_len):
            best_sc, best_len = sc, ln
    return best_sc, best_len


def _invoice_score_tier_label(score: int) -> str:
    if score >= 100:
        return "exact"
    if score >= 95:
        return "prefix"
    if score >= 85:
        return "substring"
    if score >= 75:
        return "query_contains_name"
    return "unknown"


def _invoice_fallback_name_tokens(name: str) -> list[str]:
    """Longer tokens first; used when primary ``customerName`` search returns []."""
    tokens: list[str] = []
    for part in name.strip().split():
        t = part.strip(".,;:\"'()[]")
        if len(t) >= 2:
            tokens.append(t)
    tokens.sort(key=len, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        k = t.casefold()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def pick_best_customer_match_for_invoice(
    candidates: list[dict[str, Any]],
    query_name: str,
    *,
    log: logging.Logger,
    search_path: str,
) -> tuple[dict[str, Any] | None, str]:
    """
    Pick one customer for invoice flows; ``\"ok\"`` or ``\"ambiguous\"``.

    Prefers exact normalized name, then prefix, substring, weak contains.
    """
    q = _normalize_customer_label(query_name)
    if not q or not candidates:
        log.info(
            json.dumps(
                {
                    "event": "customer_resolver_invoice_ambiguous",
                    "reason": "empty_query_or_candidates",
                    "search_path": search_path,
                },
                ensure_ascii=False,
            )
        )
        return None, "ambiguous"

    scored: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for row in candidates:
        sc, tb = _invoice_customer_score(row, q)
        if sc > 0:
            scored.append(((sc, tb), row))

    if not scored:
        log.info(
            json.dumps(
                {
                    "event": "customer_resolver_invoice_ambiguous",
                    "reason": "no_scoring_match",
                    "search_path": search_path,
                    "candidate_count": len(candidates),
                },
                ensure_ascii=False,
            )
        )
        return None, "ambiguous"

    max_sc = max(s[0][0] for s in scored)
    tier = [s for s in scored if s[0][0] == max_sc]
    max_tb = max(s[0][1] for s in tier)
    top = [s for s in tier if s[0][1] == max_tb]
    tier_label = _invoice_score_tier_label(max_sc)

    if len(top) == 1:
        log.info(
            json.dumps(
                {
                    "event": "customer_resolver_invoice_pick",
                    "match_kind": tier_label,
                    "search_path": search_path,
                    "resolution": "unique_best",
                    "api_candidate_count": len(candidates),
                    "picked_customer_id": top[0][1].get("id"),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return top[0][1], "ok"

    primary_norms = {_normalize_customer_label(_row_primary_label(s[1])) for s in top}
    if len(primary_norms) == 1:
        log.info(
            json.dumps(
                {
                    "event": "customer_resolver_invoice_pick",
                    "match_kind": tier_label,
                    "search_path": search_path,
                    "resolution": "duplicate_row_same_display_name",
                    "api_candidate_count": len(candidates),
                    "picked_customer_id": top[0][1].get("id"),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return top[0][1], "ok"

    log.info(
        json.dumps(
            {
                "event": "customer_resolver_invoice_ambiguous",
                "search_path": search_path,
                "tier": tier_label,
                "api_candidate_count": len(candidates),
                "tied_distinct_names": len(primary_norms),
            },
            ensure_ascii=False,
        )
    )
    return None, "ambiguous"


def resolve_customer_for_invoice(
    client: TripletexClient,
    log: logging.Logger,
    name: str,
) -> tuple[dict[str, Any] | None, str]:
    """
    Resolve customer for ``create_invoice_for_customer`` (broader search + stricter pick).

    Returns (row, status) where status is ``\"ok\"``, ``\"not_found\"``, or ``\"ambiguous\"``.
    """
    stripped = name.strip()
    if not stripped:
        return None, "not_found"

    cands = search_customer_by_name(client, log, stripped)
    path = "primary_customerName"

    if not cands:
        for tok in _invoice_fallback_name_tokens(stripped):
            fb = search_customer_by_name(client, log, tok)
            if fb:
                cands = fb
                path = f"fallback_customerName:{tok!r}"
                log.info(
                    json.dumps(
                        {
                            "event": "customer_resolver_invoice_fallback_search",
                            "token": tok,
                            "candidate_count": len(fb),
                        },
                        ensure_ascii=False,
                    )
                )
                break

    if not cands:
        return None, "not_found"

    picked, pstatus = pick_best_customer_match_for_invoice(
        cands, stripped, log=log, search_path=path
    )
    if pstatus == "ambiguous":
        return None, "ambiguous"
    return picked, "ok"
