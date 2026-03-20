"""create_customer: exact-name reuse skips POST /customer."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from customer_resolver import search_customer_by_name
from planner import Plan
from workflows import workflow_create_customer


def _plan(customer_name: str) -> Plan:
    return Plan(
        raw_prompt=f"opprett kunde {customer_name}",
        detected_intent="create",
        workflow="create_customer",
        customer_name=customer_name,
    )


class TestCreateCustomerReuse(unittest.TestCase):
    @patch("customer_resolver.tripletex_json")
    def test_search_customer_parses_tripletex_values_wrapper(
        self,
        mock_json: MagicMock,
    ) -> None:
        mock_json.return_value = {
            "value": {
                "fullResultSize": 5,
                "values": [{"id": 1, "name": "Nordisk Demo AS"}],
            }
        }
        rows = search_customer_by_name(MagicMock(), MagicMock(), "Nordisk Demo AS")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("id"), 1)

    @patch("workflows.tripletex_json")
    @patch("workflows.find_exact_customer_matches_for_create")
    def test_reuses_when_one_exact_match_no_post(
        self,
        mock_find: MagicMock,
        mock_json: MagicMock,
    ) -> None:
        mock_find.return_value = [{"id": 42, "name": "Nordisk Demo AS"}]
        client = MagicMock()
        out = workflow_create_customer(_plan("Nordisk Demo AS"), client)
        self.assertEqual(out["customer_id"], "42")
        self.assertEqual(out["customer_reused"], "true")
        mock_json.assert_not_called()

    @patch("workflows.tripletex_json")
    @patch("workflows.find_exact_customer_matches_for_create")
    def test_posts_when_no_exact_match(
        self,
        mock_find: MagicMock,
        mock_json: MagicMock,
    ) -> None:
        mock_find.return_value = []
        mock_json.return_value = {"value": {"id": 100}}
        client = MagicMock()
        out = workflow_create_customer(_plan("Helt Ny AS"), client)
        self.assertEqual(out["customer_id"], "100")
        self.assertEqual(out["customer_reused"], "false")
        mock_json.assert_called_once()
        call = mock_json.call_args
        self.assertEqual(call[0][2], "POST")
        self.assertEqual(call[0][3], "/customer")


if __name__ == "__main__":
    unittest.main()
