"""NM-style prompts that previously risked noop — should map to green workflows."""

from __future__ import annotations

import unittest

from planner import build_plan_rules
from planner_llm import (
    collect_router_signals,
    _score_green_workflows,
    heuristic_green_workflow_after_llm_noop,
)


class TestNmExactRules(unittest.TestCase):
    def test_price_question_norwegian(self) -> None:
        p = build_plan_rules("Hva er prisen på 'Stålplate 2mm'?")
        self.assertEqual(p.workflow, "search_product")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_stock_check_norwegian(self) -> None:
        p = build_plan_rules("Kan du sjekke om vi har 'Skrue M8' på lager?")
        self.assertEqual(p.workflow, "search_product")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_create_product_opprett_et_nytt(self) -> None:
        p = build_plan_rules(
            "Opprett et nytt produkt: 'Gummipakning', pris 45 kr, mva 25%."
        )
        self.assertEqual(p.workflow, "create_product")
        self.assertEqual(p.planner_mode, "exact_rule")

    def test_who_are_employees_norwegian(self) -> None:
        p = build_plan_rules("Hvem er de ansatte i bedriften?")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.planner_mode, "exact_rule")


class TestNmHeuristicWhenLlmNoop(unittest.TestCase):
    """When rules miss (e.g. wording variation), heuristics should still pick green."""

    def test_price_without_product_keyword_scores_search_product(self) -> None:
        text = "What is the cost for steel plate 2mm"
        self.assertEqual(build_plan_rules(text).workflow, "noop")
        scores = _score_green_workflows(text)
        self.assertGreater(scores["search_product"], scores["create_customer"])
        self.assertGreater(scores["search_product"], scores["list_employees"])
        h = heuristic_green_workflow_after_llm_noop(text)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_product")

    def test_inventory_word_triggers_search_product_heuristic(self) -> None:
        text = "Tell me inventory for bolt M8"
        self.assertEqual(build_plan_rules(text).workflow, "noop")
        h = heuristic_green_workflow_after_llm_noop(text)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_product")

    def test_staff_phrase_signal_boosts_list_employees_scores(self) -> None:
        text = "Hvem er de ansatte i bedriften i dag?"
        self.assertTrue(collect_router_signals(text).get("mentions_staff_in_company"))
        scores = _score_green_workflows(text)
        self.assertGreater(scores["list_employees"], scores["search_product"])


if __name__ == "__main__":
    unittest.main()
