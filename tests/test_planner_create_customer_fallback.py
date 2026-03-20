"""create_customer: word-based fallback when exact substring triggers miss (NM-style prompts)."""

from __future__ import annotations

import unittest

from planner import build_plan


class TestPlannerCreateCustomerFallback(unittest.TestCase):
    def test_exact_create_customer_unchanged(self) -> None:
        p = build_plan("create customer Acme AS")
        self.assertEqual(p.workflow, "create_customer")
        self.assertEqual(p.workflow_route, "exact")

    def test_natural_english_new_customer_with_name(self) -> None:
        p = build_plan(
            "Please create a new customer Acme AS for our accounting department."
        )
        self.assertEqual(p.workflow, "create_customer")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("verb=create", p.workflow_route_detail)
        self.assertIn("entity=customer", p.workflow_route_detail)
        self.assertIn("Acme", p.customer_name)

    def test_register_client_with_tail(self) -> None:
        p = build_plan("Please register a new client Nordic AS before year-end.")
        self.assertEqual(p.workflow, "create_customer")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("Nordic", p.customer_name)

    def test_labeled_customer_name(self) -> None:
        p = build_plan(
            "Add a customer: TestFirma AS — use standard billing. Contact billing@testfirma.no"
        )
        self.assertEqual(p.workflow, "create_customer")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("TestFirma", p.customer_name)

    def test_noop_without_create_verb(self) -> None:
        p = build_plan("The customer support team is available Monday to Friday.")
        self.assertEqual(p.workflow, "noop")

    def test_noop_create_without_name_signal(self) -> None:
        p = build_plan("Please create a new customer.")
        self.assertEqual(p.workflow, "noop")


if __name__ == "__main__":
    unittest.main()
