"""Customer name extraction: Spanish/French «el cliente» / «le client» before billing clauses."""

from __future__ import annotations

import unittest

from planner import _extract_customer_name_after_client_cue
from planner_llm import LLMRouterJSON, _synthetic_llm_from_heuristic, llm_router_json_to_plan


class TestExtractCustomerNameAfterClientCue(unittest.TestCase):
    def test_spanish_el_cliente_before_org_paren(self) -> None:
        p = (
            'El cliente Costa Brava SL (org. nº 923798498) tiene una factura pendiente '
            'de 47900 NOK sin IVA por "Horas de consultoría"'
        )
        self.assertEqual(_extract_customer_name_after_client_cue(p), "Costa Brava SL")

    def test_french_le_client_before_paren(self) -> None:
        p = "Le client Nordisk AS (org 123) a une facture impayée"
        self.assertEqual(_extract_customer_name_after_client_cue(p), "Nordisk AS")

    def test_empty_when_no_cue(self) -> None:
        self.assertEqual(_extract_customer_name_after_client_cue("Finn kunden Hansen AS"), "")


class TestSyntheticAndLlmPlanUseCue(unittest.TestCase):
    def test_synthetic_search_customer_gets_spanish_name(self) -> None:
        raw = (
            'El cliente Costa Brava SL (org. nº 923798498) tiene una factura pendiente '
            "de 47900 NOK"
        )
        synth = _synthetic_llm_from_heuristic(
            raw, "search_customer", "test", 0.7, "sc=x"
        )
        self.assertEqual(synth.customer_name.strip(), "Costa Brava SL")

    def test_llm_router_json_to_plan_fills_empty_customer_name(self) -> None:
        llm = LLMRouterJSON(
            workflow="search_customer",
            confidence=0.92,
            customer_name="",
            reason="model omitted slot",
        )
        raw = "El cliente Acme Iberia SL (cif B123) tiene saldo"
        plan = llm_router_json_to_plan(raw, llm)
        self.assertEqual(plan.customer_name.strip(), "Acme Iberia SL")


if __name__ == "__main__":
    unittest.main()
