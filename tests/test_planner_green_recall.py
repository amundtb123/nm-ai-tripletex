"""Higher-recall routing for the five green workflows (rules + heuristics)."""

from __future__ import annotations

import unittest

from planner import build_plan_rules
from planner_llm import _heuristic_blocked, heuristic_green_workflow_after_llm_noop


class TestExactTriggersNorwegian(unittest.TestCase):
    def test_sok_kunde_exact(self) -> None:
        p = build_plan_rules("Kan du søk kunde Acme AS for meg")
        self.assertEqual(p.workflow, "search_customer")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_liste_over_ansatte_exact(self) -> None:
        p = build_plan_rules("Jeg trenger liste over ansatte i firmaet")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_ny_kunde_exact(self) -> None:
        p = build_plan_rules("Vi har fått ny kunde Hansen AS")
        self.assertEqual(p.workflow, "create_customer")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_sok_vare_exact(self) -> None:
        p = build_plan_rules("søk vare kaffe")
        self.assertEqual(p.workflow, "search_product")
        self.assertEqual(p.planner_mode, "exact_rule")


class TestFallbackNorwegian(unittest.TestCase):
    def test_oversikt_ansatte_fallback(self) -> None:
        # Avoid substring "ansatte" alone — it is an exact trigger and would mask fallback.
        p = build_plan_rules("Vis liste over medarbeidere hos oss")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.planner_mode, "regex_fallback")

    def test_hvem_jobber_fallback(self) -> None:
        # No contiguous "hvem jobber" phrase — word-boundary verb + entity still route.
        p = build_plan_rules("Hvem er medarbeidere hos oss?")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.planner_mode, "regex_fallback")


class TestHeuristicUnblock(unittest.TestCase):
    def test_finn_kunde_with_faktura_word_not_blocked(self) -> None:
        prompt = "Finn kunde som har ubetalt faktura fra i fjor"
        self.assertFalse(_heuristic_blocked(prompt))
        h = heuristic_green_workflow_after_llm_noop(prompt)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")

    def test_payment_still_blocked(self) -> None:
        self.assertTrue(_heuristic_blocked("Registrer betaling på faktura 1234"))


if __name__ == "__main__":
    unittest.main()
