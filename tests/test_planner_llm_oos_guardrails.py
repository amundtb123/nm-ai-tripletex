"""Guardrails: invoice/payment/project/payroll/close must not map to green workflows (esp. create_*)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from planner import build_plan
from planner_llm import (
    LLMRouterJSON,
    _fixed_price_or_project_booking_prompt,
    _heuristic_blocked,
    _non_green_accounting_context,
    _score_green_workflows,
    _standalone_green_request,
    heuristic_green_workflow_after_llm_noop,
    try_llm_plan_after_noop_with_detail,
)


class TestOosHeuristicBlocked(unittest.TestCase):
    def test_invoice_complaint_with_customer_name_blocked(self) -> None:
        p = "The invoice is wrong for customer Acme AS — dispute"
        self.assertTrue(_non_green_accounting_context(p))
        self.assertFalse(_standalone_green_request(p))
        self.assertTrue(_heuristic_blocked(p))

    def test_overdue_reminder_fee_blocked(self) -> None:
        p = "Overdue invoice 9901: send reminder and add late fee"
        self.assertTrue(_heuristic_blocked(p))

    def test_monthly_close_accrual_blocked(self) -> None:
        p = "Run monthly close and post accrual reversal for March"
        self.assertTrue(_non_green_accounting_context(p))
        self.assertTrue(_heuristic_blocked(p))

    def test_create_project_linked_to_customer_blocked(self) -> None:
        p = "Create a new project linked to customer Hansen AS"
        self.assertTrue(_non_green_accounting_context(p))
        self.assertTrue(_heuristic_blocked(p))

    def test_payroll_with_employee_email_blocked(self) -> None:
        p = "Payroll: send payslip to employee ola@firma.no"
        self.assertTrue(_non_green_accounting_context(p))
        self.assertTrue(_heuristic_blocked(p))

    def test_finn_kunde_not_blocked(self) -> None:
        p = "Finn kunden Hansen AS for meg"
        self.assertTrue(_standalone_green_request(p))
        self.assertFalse(_heuristic_blocked(p))

    def test_hva_er_prisen_not_blocked(self) -> None:
        p = "Hva er prisen på 'Bolt M8'?"
        self.assertTrue(_standalone_green_request(p))
        self.assertFalse(_heuristic_blocked(p))

    def test_invoice_price_not_standalone(self) -> None:
        p = "What is the price on invoice line 3 for customer Acme?"
        self.assertFalse(_standalone_green_request(p))

    def test_german_festpreis_projekt_blocks_green(self) -> None:
        p = (
            'Legen Sie einen Festpreis von 473250 NOK für das Projekt "Datensicherheit" '
            "für Windkraft GmbH (Org.-Nr. 886395582) fest."
        )
        self.assertTrue(_fixed_price_or_project_booking_prompt(p))
        self.assertTrue(_non_green_accounting_context(p))
        self.assertTrue(_heuristic_blocked(p))

    def test_finn_kunde_not_blocked_by_festpreis_guard(self) -> None:
        p = "Finn kunden Hansen AS for meg"
        self.assertFalse(_fixed_price_or_project_booking_prompt(p))
        self.assertFalse(_heuristic_blocked(p))


class TestOosScoresAndOverride(unittest.TestCase):
    def test_invoice_context_zeros_heuristic_scores(self) -> None:
        p = "Invoice dispute: customer Ola says amount is wrong"
        scores = _score_green_workflows(p)
        self.assertEqual(sum(scores.values()), 0.0)

    def test_festpreis_prompt_zeros_heuristic_scores(self) -> None:
        p = "Legen Sie einen Festpreis von 100 NOK für das Projekt X für Firma Y fest."
        self.assertEqual(sum(_score_green_workflows(p).values()), 0.0)

    def test_guardrail_rejects_llm_create_customer(self) -> None:
        llm = LLMRouterJSON(
            workflow="create_customer",
            confidence=0.9,
            customer_name="Acme",
            extraction_summary="Request to create customer",
        )
        prompt = "The invoice is wrong for customer Acme AS with orgnr 123"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            with patch("planner_llm.call_llm_router", return_value=llm):
                plan, detail = try_llm_plan_after_noop_with_detail(prompt)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")

    def test_standalone_still_allows_heuristic_override_when_model_noops(self) -> None:
        """Clear 'Finn kunde' should not be blocked; heuristic can pick search_customer."""
        p = "Finn kunden Nordisk Demo AS"
        self.assertFalse(_heuristic_blocked(p))
        h = heuristic_green_workflow_after_llm_noop(p)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")


class TestBuildPlanGuardrailStatus(unittest.TestCase):
    @patch("planner_llm.call_llm_router")
    def test_build_plan_sets_guardrail_status(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(
            workflow="create_customer",
            confidence=0.9,
            customer_name="X",
            extraction_summary="x",
        )
        # Avoid exact substring "invoice for customer" (would hit create_invoice rule before LLM).
        prompt = "The invoice is wrong for customer TestCo — please correct the billing"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(prompt)
        self.assertEqual(plan.workflow, "noop")
        self.assertEqual(plan.planner_llm_status, "guardrail_rejected_green")


if __name__ == "__main__":
    unittest.main()
