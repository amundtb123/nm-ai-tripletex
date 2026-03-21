"""NM-style prompts that previously risked noop — should map to green workflows."""

from __future__ import annotations

import unittest

from planner import build_plan_rules
from planner_llm import (
    collect_router_signals,
    _score_green_workflows,
    heuristic_green_workflow_after_llm_noop,
    heuristic_green_workflow_after_llm_noop_two_pass,
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


class TestImplicitContactWhenLlmNoop(unittest.TestCase):
    """Phone/email without explicit «kunde»/customer — still CRM; avoid llm_noop + zero scores."""

    def test_phone_only_no_customer_word_defaults_to_search_customer(self) -> None:
        text = "Ring tilbake på 98765432 for å gå videre med saken"
        self.assertFalse(collect_router_signals(text)["mentions_customer_terms"])
        scores = _score_green_workflows(text)
        self.assertGreater(scores["search_customer"], 4.5)
        self.assertGreater(scores["search_customer"], scores["create_customer"])
        h = heuristic_green_workflow_after_llm_noop(text)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")

    def test_find_verbs_plus_phone_without_customer_word(self) -> None:
        # Avoid «sjekk om» — it sets mentions_price_or_stock_lookup and favors search_product.
        text = "Finn mer informasjon via telefon +47 22 33 44 55"
        scores = _score_green_workflows(text)
        self.assertGreater(scores["search_customer"], 6.0)
        self.assertGreater(scores["search_customer"], scores["search_product"])
        h = heuristic_green_workflow_after_llm_noop_two_pass(text)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "search_customer")

    def test_create_verbs_plus_phone_without_customer_word(self) -> None:
        text = "Legg til og registrer telefon 40123456 for oppfølging"
        scores = _score_green_workflows(text)
        self.assertGreater(scores["create_customer"], scores["search_customer"])
        h = heuristic_green_workflow_after_llm_noop(text)
        self.assertIsNotNone(h)
        self.assertEqual(h[0], "create_customer")


if __name__ == "__main__":
    unittest.main()
