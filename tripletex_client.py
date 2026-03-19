"""Minimal Tripletex HTTP client using session token as Basic Auth password."""

from __future__ import annotations

from typing import Any

import requests


class TripletexClient:
    """
    Thin wrapper around Tripletex REST API.

    Auth: HTTP Basic with username \"0\" and password = session_token.
    TODO: Retry/backoff for transient errors; logging of request IDs if API provides them.
    """

    def __init__(self, base_url: str, session_token: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._auth = ("0", session_token)
        self._session = requests.Session()
        self._session.auth = self._auth

    def get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self._base_url}{path if path.startswith('/') else '/' + path}"
        return self._session.get(url, params=params, timeout=self._timeout)

    def post(
        self,
        path: str,
        json: dict[str, Any] | list[Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = f"{self._base_url}{path if path.startswith('/') else '/' + path}"
        return self._session.post(url, json=json, params=params, timeout=self._timeout)

    def put(
        self,
        path: str,
        json: dict[str, Any] | list[Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = f"{self._base_url}{path if path.startswith('/') else '/' + path}"
        return self._session.put(url, json=json, params=params, timeout=self._timeout)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self._base_url}{path if path.startswith('/') else '/' + path}"
        return self._session.delete(url, params=params, timeout=self._timeout)
