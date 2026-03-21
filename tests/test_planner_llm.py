"""LLM router (Spor B): mapping to Plan, env gating, and safe fallback — no real HTTP."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from planner import build_plan, build_plan_rules
from planner_llm import (
    LLMRouterJSON,
    llm_router_json_to_plan,
    try_llm_plan_after_noop_with_detail,
)


class TestPlannerLLMMapping(unittest.TestCase):
    def test_llm_json_to_plan_search_customer_no(self) -> None:
        llm = LLMRouterJSON(
            workflow="search_customer",
            confidence=0.92,
            language="no",
            customer_name="Nordisk Demo",
            extraction_summary="Bruker vil finne eksisterende kunde",
        )
        p = llm_router_json_to_plan("Kan du finne kunden Nordisk Demo", llm)
        self.assertEqual(p.workflow, "search_customer")
        self.assertEqual(p.planner_mode, "llm")
        self.assertEqual(p.workflow_route, "llm")
        self.assertEqual(p.planner_confidence, 0.92)
        self.assertEqual(p.planner_language, "no")
        self.assertEqual(p.planner_selected_workflow, "search_customer")
        self.assertEqual(p.planner_selected_entity, "customer")
        self.assertIn("Nordisk", p.customer_name)

    def test_llm_json_to_plan_create_product_en(self) -> None:
        llm = LLMRouterJSON(
            workflow="create_product",
            confidence=0.88,
            language="en",
            product_name="Widget Pro",
            extraction_summary="Create new product",
        )
        p = llm_router_json_to_plan("Please add a new product called Widget Pro", llm)
        self.assertEqual(p.workflow, "create_product")
        self.assertEqual(p.planner_mode, "llm")
        self.assertEqual(p.name, "Widget Pro")


class TestPlannerLLMFallback(unittest.TestCase):
    def test_disabled_without_env(self) -> None:
        # Never clear entire environ (would drop PATH and break subprocesses).
        with patch.dict(
            os.environ,
            {"LLM_PLANNER_ENABLED": "0", "OPENAI_API_KEY": "", "LLM_PLANNER_API_KEY": ""},
            clear=False,
        ):
            p, d = try_llm_plan_after_noop_with_detail("anything")
        self.assertIsNone(p)
        self.assertEqual(d, "llm_disabled")

    def test_low_confidence_returns_reason(self) -> None:
        llm = LLMRouterJSON(
            workflow="list_employees",
            confidence=0.1,
            language="en",
            extraction_summary="x",
        )
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            with patch("planner_llm.call_llm_router", return_value=llm):
                p, d = try_llm_plan_after_noop_with_detail("natural prompt")
        self.assertIsNone(p)
        self.assertTrue(d.startswith("low_confidence:"))

    def test_llm_noop_from_model(self) -> None:
        llm = LLMRouterJSON(
            workflow="noop",
            confidence=0.99,
            language="unknown",
            extraction_summary="unclear",
        )
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            with patch("planner_llm.call_llm_router", return_value=llm):
                p, d = try_llm_plan_after_noop_with_detail("something vague")
        self.assertIsNone(p)
        self.assertEqual(d, "llm_chose_noop")


class TestBuildPlanIntegration(unittest.TestCase):
    _NOOP_PROMPT = "jeg trenger hjelp med noe helt annet uten workflow signaler"

    @patch("planner_llm.call_llm_router")
    def test_noop_then_llm_routes_list_employees(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = LLMRouterJSON(
            workflow="list_employees",
            confidence=0.95,
            language="en",
            extraction_summary="User wants staff list",
        )
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(self._NOOP_PROMPT)
        self.assertEqual(build_plan_rules(self._NOOP_PROMPT).workflow, "noop")
        self.assertEqual(plan.workflow, "list_employees")
        self.assertEqual(plan.planner_mode, "llm")
        mock_llm.assert_called_once()

    @patch("planner_llm.try_llm_plan_after_noop_with_detail")
    def test_exact_rule_never_calls_llm_path(self, mock_try: MagicMock) -> None:
        plan = build_plan("list employees")
        self.assertEqual(plan.workflow, "list_employees")
        self.assertEqual(plan.planner_mode, "exact_rule")
        mock_try.assert_not_called()

    @patch("planner_llm.call_llm_router")
    def test_noop_llm_invalid_keeps_noop_with_status(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = None
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            plan = build_plan(self._NOOP_PROMPT)
        self.assertEqual(plan.workflow, "noop")
        self.assertEqual(plan.planner_mode, "noop")
        self.assertEqual(plan.planner_llm_status, "invalid_response")

    def test_rules_noop_sets_planner_mode_noop(self) -> None:
        p = build_plan_rules(self._NOOP_PROMPT)
        self.assertEqual(p.workflow, "noop")
        self.assertEqual(p.planner_mode, "noop")


if __name__ == "__main__":
    unittest.main()
