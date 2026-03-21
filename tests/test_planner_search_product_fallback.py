"""search_customer / search_product / create_product word-based fallbacks."""

from __future__ import annotations

import unittest

from planner import build_plan


class TestPlannerSearchProductFallbacks(unittest.TestCase):
    def test_search_customer_natural_prompt(self) -> None:
        p = build_plan(
            "Please search for a customer named Acme AS in the Tripletex sandbox."
        )
        self.assertEqual(p.workflow, "search_customer")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("Acme", p.customer_name)

    def test_exact_find_customer_unchanged(self) -> None:
        p = build_plan("find customer Acme")
        self.assertEqual(p.workflow, "search_customer")
        self.assertEqual(p.workflow_route, "exact")

    def test_search_product_list_natural(self) -> None:
        p = build_plan("Please list all products that match coffee in the name.")
        self.assertEqual(p.workflow, "search_product")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("coffee", p.product_name.lower())

    def test_search_product_varenummer_only(self) -> None:
        # Ingen sammenhengende «find product» — kun ord-basert fallback + varenummer.
        p = build_plan("Find every product where varenummer is ABC-99 in catalog.")
        self.assertEqual(p.workflow, "search_product")
        self.assertEqual(p.workflow_route, "fallback")

    def test_create_product_natural(self) -> None:
        p = build_plan(
            "Please add a new product Kaffe Premium for the catalog with price 59 kr."
        )
        self.assertEqual(p.workflow, "create_product")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("Kaffe", p.product_name)

    def test_noop_search_without_entity(self) -> None:
        p = build_plan("Please search the database for anything useful.")
        self.assertEqual(p.workflow, "noop")


if __name__ == "__main__":
    unittest.main()
