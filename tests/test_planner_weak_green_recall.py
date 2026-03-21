"""Weak-green recall: third heuristic pass + get/fetch customer phrasing; OOS unchanged."""

from __future__ import annotations

import unittest

from planner_llm import (
    _non_green_accounting_context,
    _weak_green_recall_eligible,
    heuristic_green_workflow_after_llm_noop_two_pass,
)


class TestWeakGreenEligible(unittest.TestCase):
    def test_weak_eligible_fetch_customer_english(self) -> None:
        self.assertTrue(_weak_green_recall_eligible("Fetch customer Hansen AS from the CRM"))

    def test_weak_eligible_staff_who(self) -> None:
        self.assertTrue(
            _weak_green_recall_eligible("Who are the staff members working here today?")
        )

    def test_weak_eligible_product_stock_check(self) -> None:
        self.assertTrue(
            _weak_green_recall_eligible("Check whether we have bolt M8 in stock")
        )

    def test_invoice_oos_not_weak_eligible(self) -> None:
        p = "The invoice is wrong for customer Acme AS"
        self.assertTrue(_non_green_accounting_context(p))
        self.assertFalse(_weak_green_recall_eligible(p))

    def test_payment_oos_not_weak_eligible(self) -> None:
        p = "Register payment on invoice 9901 for customer Hansen"
        self.assertFalse(_weak_green_recall_eligible(p))

    def test_project_customer_oos_not_weak_eligible(self) -> None:
        p = "Create a project linked to customer Hansen AS"
        self.assertFalse(_weak_green_recall_eligible(p))

    def test_payroll_oos_not_weak_eligible(self) -> None:
        p = "Payroll run: send payslip to ola@firma.no"
        self.assertFalse(_weak_green_recall_eligible(p))

    def test_month_close_oos_not_weak_eligible(self) -> None:
        p = "Post accrual reversal for monthly close"
        self.assertFalse(_weak_green_recall_eligible(p))


class TestWeakGreenHeuristicRoutes(unittest.TestCase):
    def test_fetch_customer_routes_search_customer(self) -> None:
        h = heuristic_green_workflow_after_llm_noop_two_pass(
            "Fetch customer Nordisk Demo AS from the register"
        )
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")
        # May win on first pass now that fetch + «the register» are scored correctly.
        self.assertTrue(
            "standard" in h[1] or "relaxed" in h[1] or "weak" in h[1]
        )

    def test_who_staff_routes_list_employees_via_weak_pass(self) -> None:
        """Non-standalone employee+who cue: third pass may apply."""
        h = heuristic_green_workflow_after_llm_noop_two_pass(
            "Who are the staff members working here today?"
        )
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "list_employees")

    def test_ok_thanks_still_no_route(self) -> None:
        self.assertIsNone(heuristic_green_workflow_after_llm_noop_two_pass("ok thanks"))


if __name__ == "__main__":
    unittest.main()
