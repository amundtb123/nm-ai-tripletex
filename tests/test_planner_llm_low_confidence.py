"""Low-confidence LLM paths: two-pass heuristic, relaxed override, clearer noop labels."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from planner_llm import (
    LLMRouterJSON,
    _heuristic_relax_eligible,
    heuristic_green_workflow_after_llm_noop,
    heuristic_green_workflow_after_llm_noop_two_pass,
    try_llm_plan_after_noop_with_detail,
)


class TestHeuristicTwoPass(unittest.TestCase):
    def test_two_pass_calls_relaxed_when_standard_returns_none(self) -> None:
        fake = (
            "search_customer",
            "heuristic_scores winner=search_customer best=9.0 second=0.0|relaxed",
            0.7,
            "sc=1.0",
        )
        with patch(
            "planner_llm.heuristic_green_workflow_after_llm_noop",
            side_effect=[None, fake],
        ) as mock_h:
            out = heuristic_green_workflow_after_llm_noop_two_pass("Finn kunden Acme AS")
        self.assertEqual(out, fake)
        self.assertEqual(mock_h.call_count, 2)
        second_kw = mock_h.call_args_list[1][1]
        self.assertTrue(second_kw.get("relaxed"))

    def test_relax_eligible_requires_standalone_and_not_oos(self) -> None:
        self.assertTrue(_heuristic_relax_eligible("Locate the customer Hansen AS"))
        self.assertFalse(_heuristic_relax_eligible("Invoice dispute for customer Hansen"))

    def test_relaxed_reason_tag_in_heuristic_string(self) -> None:
        """Relaxed pick appends |relaxed to the reason fragment."""
        with patch("planner_llm._score_green_workflows") as mock_scores:
            mock_scores.return_value = {
                "list_employees": 0.0,
                "search_customer": 2.2,
                "create_customer": 2.15,
                "search_product": 0.0,
                "create_product": 0.0,
            }
            h = heuristic_green_workflow_after_llm_noop("Finn kunden X", relaxed=True)
        self.assertIsNotNone(h)
        self.assertIn("relaxed", h[1])


class TestLowConfidenceGreenPreserved(unittest.TestCase):
    @patch("planner_llm.heuristic_green_workflow_after_llm_noop_two_pass", return_value=None)
    def test_green_workflow_kept_when_confidence_zero(self, _mock_h: MagicMock) -> None:
        """When heuristics do not override, sub-threshold green LLM output is still used."""
        llm = LLMRouterJSON(
            workflow="search_customer",
            confidence=0.0,
            language="en",
            customer_name="Acme",
            extraction_summary="x",
        )
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "1", "OPENAI_API_KEY": "sk-test"}):
            with patch("planner_llm.call_llm_router", return_value=llm):
                p, d = try_llm_plan_after_noop_with_detail("Find customer Acme Ltd")
        self.assertIsNotNone(p)
        self.assertEqual(d, "ok_low_confidence_llm")
        assert p is not None
        self.assertEqual(p.workflow, "search_customer")


if __name__ == "__main__":
    unittest.main()
