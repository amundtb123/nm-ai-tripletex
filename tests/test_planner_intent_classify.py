"""Regression tests for planner._classify_intent word-boundary matching."""

from __future__ import annotations

import unittest

from planner import _classify_intent


class TestClassifyIntentWordBoundaries(unittest.TestCase):
    def test_search_not_inside_research(self) -> None:
        self.assertEqual(
            _classify_intent("Please research the general ledger for Q1"),
            "unknown",
        )

    def test_create_not_inside_created(self) -> None:
        self.assertEqual(
            _classify_intent("Total costs increased from January to February"),
            "unknown",
        )

    def test_create_word_still_create(self) -> None:
        self.assertEqual(
            _classify_intent("Please create a new customer Acme AS"),
            "create",
        )

    def test_search_word_still_search(self) -> None:
        self.assertEqual(
            _classify_intent("Search for customer Hansen AS"),
            "search",
        )

    def test_finne_kunden_still_search(self) -> None:
        self.assertEqual(
            _classify_intent("Kan du finne kunden Hansen AS"),
            "search",
        )


if __name__ == "__main__":
    unittest.main()
