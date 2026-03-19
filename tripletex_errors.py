"""Parse Tripletex API error payloads (ApiError schema) and map to exceptions."""

from __future__ import annotations

import json
from typing import Any

import requests


class TripletexAPIError(Exception):
    """Raised when Tripletex returns a non-success HTTP status with a parseable body."""

    def __init__(
        self,
        http_status: int,
        message: str,
        *,
        code: int | None = None,
        request_id: str | None = None,
        developer_message: str | None = None,
        validation_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.api_message = message
        self.code = code
        self.request_id = request_id
        self.developer_message = developer_message
        self.validation_messages = validation_messages or []

    def public_detail(self) -> str:
        """Safe string for HTTP responses / logs (no secrets)."""
        parts: list[str] = [self.api_message]
        if self.validation_messages:
            for vm in self.validation_messages[:5]:
                msg = vm.get("message") or vm.get("field")
                if msg:
                    parts.append(str(msg))
        if self.request_id:
            parts.append(f"requestId={self.request_id}")
        return "; ".join(parts)


def _truncate(text: str, max_len: int = 500) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def response_body_preview(resp: requests.Response, max_len: int = 500) -> str:
    """Short plain-text preview for logging."""
    try:
        return _truncate(resp.text, max_len=max_len)
    except Exception:
        return "<unreadable body>"


def parse_error_payload(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    return {
        "status": body.get("status"),
        "code": body.get("code"),
        "message": body.get("message") or "",
        "developerMessage": body.get("developerMessage"),
        "requestId": body.get("requestId"),
        "validationMessages": body.get("validationMessages") or [],
    }


def raise_for_tripletex_error(resp: requests.Response) -> None:
    """If response is not OK, parse JSON ApiError when possible and raise TripletexAPIError."""
    if resp.ok:
        return

    http_status = resp.status_code
    message = f"HTTP {http_status}"
    code: int | None = None
    request_id: str | None = None
    developer_message: str | None = None
    validation_messages: list[dict[str, Any]] | None = None

    try:
        data = resp.json()
        if isinstance(data, dict):
            parsed = parse_error_payload(data)
            if parsed.get("message"):
                message = str(parsed["message"])
            code = parsed.get("code") if isinstance(parsed.get("code"), int) else code
            request_id = str(parsed["requestId"]) if parsed.get("requestId") else None
            dev = parsed.get("developerMessage")
            developer_message = str(dev) if dev else None
            vm = parsed.get("validationMessages")
            if isinstance(vm, list):
                validation_messages = [v for v in vm if isinstance(v, dict)]
    except (json.JSONDecodeError, ValueError):
        message = _truncate(resp.text or message, max_len=300)

    raise TripletexAPIError(
        http_status,
        message,
        code=code,
        request_id=request_id,
        developer_message=developer_message,
        validation_messages=validation_messages,
    ) from None
