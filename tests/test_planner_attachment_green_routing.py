"""Green routing with incidental invoice/attachment wording; files do not block heuristics."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from planner import build_plan
from planner_llm import (
    LLMRouterJSON,
    _billing_invoice_primary_task,
    _non_green_accounting_context,
    _score_green_workflows,
    _standalone_green_request,
    heuristic_green_workflow_after_llm_noop_two_pass,
    try_llm_plan_after_noop_with_detail,
)


class TestBillingPrimaryVsStandalone(unittest.TestCase):
    def test_incidental_invoice_word_not_billing_primary(self) -> None:
        p = "Look up customer Nordisk Demo AS. Invoice attached for your reference."
        self.assertFalse(_billing_invoice_primary_task(p))

    def test_unpaid_invoice_with_customer_search_is_billing_primary(self) -> None:
        p = "Finn kunde som har ubetalt faktura fra i fjor"
        self.assertTrue(_billing_invoice_primary_task(p))

    def test_find_invoice_document_is_billing_primary(self) -> None:
        self.assertTrue(_billing_invoice_primary_task("find invoice 9901 for reconciliation"))


class TestNonGreenWithStandaloneExemption(unittest.TestCase):
    def test_lookup_customer_with_invoice_boilerplate_not_oos(self) -> None:
        p = "Look up customer Nordisk Demo AS. Invoice attached for your reference."
        self.assertFalse(_non_green_accounting_context(p))

    def test_fetch_customer_with_invoice_reference_only_not_oos(self) -> None:
        """Incidental «invoice» (email/ref) must not block English fetch/get + customer."""
        p = (
            "Please fetch customer Nordisk AS; the invoice number "
            "is mentioned only for reference in the email."
        )
        self.assertFalse(_billing_invoice_primary_task(p))
        self.assertTrue(_standalone_green_request(p))
        self.assertFalse(_non_green_accounting_context(p))

    def test_product_price_with_vedlegg_not_oos(self) -> None:
        p = "Vedlegg følger. Hva er prisen på 'Bolt M8'?"
        self.assertFalse(_non_green_accounting_context(p))

    def test_staff_list_with_vedlegg_not_oos(self) -> None:
        p = "Se vedlegg. Hvem er de ansatte i bedriften?"
        self.assertFalse(_non_green_accounting_context(p))

    def test_invoice_dispute_still_oos(self) -> None:
        p = "The invoice is wrong for customer Acme AS — dispute"
        self.assertTrue(_non_green_accounting_context(p))

    def test_month_close_with_vedlegg_still_oos(self) -> None:
        p = "Vedlegg: rapport. Run monthly close and post accrual reversal for March"
        self.assertTrue(_non_green_accounting_context(p))

    def test_create_invoice_with_vedlegg_still_oos(self) -> None:
        p = "Vedlegg følger. Opprett faktura for kunde Acme AS"
        self.assertTrue(_non_green_accounting_context(p))


class TestHeuristicScoresWithAttachmentPhrasing(unittest.TestCase):
    def test_scores_nonzero_for_green_customer_prompt_with_invoice_word(self) -> None:
        p = "Look up customer Nordisk Demo AS. Invoice attached for your reference."
        scores = _score_green_workflows(p)
        self.assertGreater(scores.get("search_customer", 0.0), 0.0)

    def test_two_pass_finds_green_for_customer_with_invoice_boilerplate(self) -> None:
        p = "Look up customer Nordisk Demo AS. Invoice attached for your reference."
        h = heuristic_green_workflow_after_llm_noop_two_pass(p)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")


class TestBuildPlanWithFileCount(unittest.TestCase):
    @patch("planner_llm.call_llm_router")
    def test_file_count_passed_to_llm_router(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="noop", confidence=0.2)
        p = "Look up customer TestCo AS"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertEqual(mock_llm.call_count, 1)
        args, kwargs = mock_llm.call_args
        self.assertEqual(args[0], p)
        self.assertEqual(kwargs.get("file_count"), 1)

    @patch("planner_llm.call_llm_router")
    def test_heuristic_override_same_with_or_without_files(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="noop", confidence=0.2)
        prompt = "Look up customer Nordisk Demo AS. Invoice attached for reference."
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            a0, _ = try_llm_plan_after_noop_with_detail(prompt, file_count=0)
            a1, _ = try_llm_plan_after_noop_with_detail(prompt, file_count=1)
        self.assertIsNotNone(a0)
        self.assertIsNotNone(a1)
        self.assertEqual(a0.workflow if a0 else None, a1.workflow if a1 else None)
        self.assertEqual(a0.workflow, "search_customer")


class TestWeakGreenWithAttachmentNotNoop(unittest.TestCase):
    """LLM noop + heuristic override must work when file_count > 0 (NM-style)."""

    @patch("planner_llm.call_llm_router")
    def test_weak_customer_vedlegg_not_noop(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="noop", confidence=0.2)
        # «pull up» is find-verbs in planner_llm but not an exact planner.py substring — rules noop → LLM + heuristic.
        p = "Vedlegg: referanse.\nCould you pull up customer Hansen AS for me"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(p, file_count=1)
        self.assertNotEqual(plan.workflow, "noop")
        self.assertEqual(plan.workflow, "search_customer")

    @patch("planner_llm.call_llm_router")
    def test_weak_product_vedlegg_not_noop(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="noop", confidence=0.2)
        p = "Attachment: spec.pdf\nCheck whether we have bolt M8 in stock"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(p, file_count=1)
        self.assertNotEqual(plan.workflow, "noop")
        self.assertEqual(plan.workflow, "search_product")

    @patch("planner_llm.call_llm_router")
    def test_weak_employee_vedlegg_not_noop(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="noop", confidence=0.2)
        p = "Se vedlegg.\nWho are the staff members working here today?"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(p, file_count=1)
        self.assertNotEqual(plan.workflow, "noop")
        self.assertEqual(plan.workflow, "list_employees")


class TestOosWithAttachmentStillNoop(unittest.TestCase):
    """Guardrails: LLM green must be rejected — test router path only (avoid exact_rule shortcuts)."""

    @patch("planner_llm.call_llm_router")
    def test_invoice_dispute_guardrail_with_attachment(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="search_customer", confidence=0.95)
        p = "Vedlegg: kopi.\nThe invoice is wrong for customer Acme AS — dispute"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan, detail = try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")

    @patch("planner_llm.call_llm_router")
    def test_payment_intent_guardrail_with_attachment(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="search_customer", confidence=0.95)
        p = "Attachment: kvittering.png\nPlease record the payment for invoice 1234 today"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan, detail = try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")

    @patch("planner_llm.call_llm_router")
    def test_project_customer_guardrail_with_attachment(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="create_customer", confidence=0.95)
        p = "See attachment.\nWe should link a new project to customer Hansen AS"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan, detail = try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")

    @patch("planner_llm.call_llm_router")
    def test_payroll_guardrail_with_attachment(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="list_employees", confidence=0.95)
        p = "Vedlegg: lønn.pdf\nPayroll: send payslip to ola@firma.no"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan, detail = try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")

    @patch("planner_llm.call_llm_router")
    def test_month_close_guardrail_with_attachment(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(workflow="search_product", confidence=0.95)
        p = "Attached: report.\nRun monthly close and post accrual reversal for March"
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan, detail = try_llm_plan_after_noop_with_detail(p, file_count=1)
        self.assertIsNone(plan)
        self.assertEqual(detail, "guardrail_rejected_llm_green")


if __name__ == "__main__":
    unittest.main()
