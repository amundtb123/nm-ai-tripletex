"""Tripletex HTTP helpers: structured logging (never logs session token) and JSON handling."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from request_context import solve_request_id
from tripletex_client import TripletexClient
from tripletex_errors import TripletexAPIError, raise_for_tripletex_error, response_body_preview

Method = Literal["GET", "POST", "PUT"]


def tripletex_json(
    client: TripletexClient,
    log: logging.Logger,
    method: Method,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    preview_len: int = 480,
) -> Any:
    """
    Perform one Tripletex call, log method/path/status/summary, return parsed JSON.

    Logs: method, path (relative), status_code, which query keys were used (not values),
    request body keys for mutations, response_preview.
    """
    if method == "GET":
        resp = client.get(path, params=params)
    elif method == "POST":
        resp = client.post(path, json=json_body, params=params)
    elif method == "PUT":
        resp = client.put(path, json=json_body, params=params)
    else:
        raise ValueError(f"Unsupported method {method}")

    preview = response_body_preview(resp, max_len=preview_len)
    rid = solve_request_id.get()
    payload: dict[str, Any] = {
        "level": "INFO",
        "event": "tripletex_http",
        "method": method,
        "path": path,
        "status_code": resp.status_code,
        "query_param_keys": sorted(params) if params else [],
        "request_body_keys": sorted(json_body) if json_body else [],
        "response_preview": preview,
    }
    if rid:
        payload["request_id"] = rid
    log.info(json.dumps(payload, ensure_ascii=False, default=str))

    raise_for_tripletex_error(resp)
    if not resp.content:
        return None
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise TripletexAPIError(resp.status_code, "Response was not valid JSON") from exc
