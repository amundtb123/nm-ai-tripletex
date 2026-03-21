"""POST /solve request validation: tolerant parsing + 422 logging (before handler runs)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app


class TestSolveValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_files_null_accepted_and_reaches_handler(self) -> None:
        """``files: null`` must not 422 (coerced to [])."""
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "0"}, clear=False):
            r = self.client.post(
                "/solve",
                json={
                    "prompt": "zzzzzzzzzznmnoopunlikely999999999999999999999",
                    "files": None,
                    "tripletex_credentials": {
                        "base_url": "https://api.tripletex.io/v2",
                        "session_token": "test-token",
                    },
                },
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"status": "completed"})

    def test_extra_json_keys_ignored(self) -> None:
        """Unknown top-level keys must not 422 (extra=ignore)."""
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "0"}, clear=False):
            r = self.client.post(
                "/solve",
                json={
                    "prompt": "zzzzzzzzzznmnoopunlikely999999999999999999999",
                    "files": [],
                    "tripletex_credentials": {
                        "base_url": "https://api.tripletex.io/v2",
                        "session_token": "test-token",
                    },
                    "nm_metadata": {"round": 1},
                },
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"status": "completed"})

    def test_prompt_number_coerced_to_string(self) -> None:
        """Some clients send prompt as JSON number."""
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "0"}, clear=False):
            r = self.client.post(
                "/solve",
                json={
                    "prompt": 12345,
                    "files": [],
                    "tripletex_credentials": {
                        "base_url": "https://api.tripletex.io/v2",
                        "session_token": "test-token",
                    },
                },
            )
        self.assertEqual(r.status_code, 200, r.text)

    def test_missing_prompt_422_with_validation_log(self) -> None:
        with self.assertLogs("main", level="WARNING") as log_ctx:
            r = self.client.post(
                "/solve",
                json={
                    "files": [],
                    "tripletex_credentials": {
                        "base_url": "https://api.tripletex.io/v2",
                        "session_token": "x",
                    },
                },
            )
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertIn("detail", body)
        combined = "\n".join(log_ctx.output)
        self.assertIn("request_validation_error", combined)
        self.assertIn("/solve", combined)
        payload = None
        for ln in log_ctx.output:
            if "request_validation_error" not in ln:
                continue
            start = ln.find("{")
            if start >= 0:
                try:
                    payload = json.loads(ln[start:])
                except json.JSONDecodeError:
                    continue
                break
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get("event"), "request_validation_error")
        self.assertEqual(payload.get("path"), "/solve")
        self.assertTrue(payload.get("validation_errors"))
        self.assertIn("raw_body", payload)
        self.assertIn("request_headers", payload)


if __name__ == "__main__":
    unittest.main()
