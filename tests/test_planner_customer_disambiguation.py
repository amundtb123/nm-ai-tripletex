"""Customer path: search vs create/reuse — planner rules and LLM heuristics."""

from __future__ import annotations

import unittest

from planner import build_plan_rules
from planner_llm import _score_green_workflows, collect_router_signals


class TestPlannerFallbackSearchBeforeCreate(unittest.TestCase):
    """Fallback order: search_customer is tried before create_customer."""

    def test_finn_before_opprett_when_both_verbs(self) -> None:
        """If both find- and create-style verbs appear, prefer search (first in fallback)."""
        p = build_plan_rules("Finn og opprett er ikke naturlig, men finn kunde Acme først")
        self.assertEqual(p.workflow, "search_customer")

    def test_opprett_without_finn_stays_create(self) -> None:
        p = build_plan_rules("Opprett kunde TestBedrift AS")
        self.assertEqual(p.workflow, "create_customer")


class TestHeuristicCustomerContact(unittest.TestCase):
    def test_find_customer_with_email_phone_prefers_search(self) -> None:
        """Lookup + contact details should not dominate toward create_customer."""
        text = (
            "Kan du finne kunden Hansen AS? Telefon +47 90011222 og e-post hansen@example.com"
        )
        s = collect_router_signals(text)
        self.assertTrue(s["mentions_find_verbs"])
        self.assertTrue(s["has_email_in_text"])
        self.assertTrue(s["has_phone_in_text"])
        scores = _score_green_workflows(text)
        self.assertGreater(
            scores["search_customer"],
            scores["create_customer"],
            f"scores={scores}",
        )

    def test_register_new_customer_prefers_create(self) -> None:
        text = "Registrer ny kunde Brønnøysund AS, tlf 75550000, kontakt@bro.no"
        scores = _score_green_workflows(text)
        self.assertGreaterEqual(scores["create_customer"], scores["search_customer"])

    def test_existing_cue_boosts_search(self) -> None:
        text = "Finn eksisterende kunde Acme i systemet"
        s = collect_router_signals(text)
        self.assertTrue(s.get("mentions_existing_customer_cue"))
        scores = _score_green_workflows(text)
        self.assertGreater(scores["search_customer"], scores["create_customer"])


if __name__ == "__main__":
    unittest.main()
