"""Unwrap Tripletex list/search responses where ``value`` is a list or ``{values, fullResultSize}``."""

from __future__ import annotations

from typing import Any


def _unwrap_value(data: Any) -> Any:
    if isinstance(data, dict) and "value" in data:
        return data["value"]
    return data


def tripletex_list_rows_from_response(
    data: Any,
) -> tuple[list[dict[str, Any]], str, int | None]:
    """
    Return (rows, extract_tag, fullResultSize or None).

    Tripletex often returns ``{"value": {"fullResultSize": n, "values": [...]}}``;
    treating ``value`` as a list was wrong and produced **0** rows while previews showed data.
    """
    inner = _unwrap_value(data)
    if isinstance(inner, list):
        rows = [v for v in inner if isinstance(v, dict)]
        return rows, "unwrapped_list", None
    if isinstance(inner, dict):
        frs_raw = inner.get("fullResultSize")
        frs: int | None
        if isinstance(frs_raw, int):
            frs = frs_raw
        elif isinstance(frs_raw, str) and frs_raw.isdigit():
            frs = int(frs_raw)
        else:
            frs = None
        vals = inner.get("values")
        if isinstance(vals, list):
            rows = [v for v in vals if isinstance(v, dict)]
            return rows, "unwrapped_object_values", frs
    return [], "no_extractable_rows", None
