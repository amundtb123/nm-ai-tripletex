"""Regression: NM-style short English prompts route to list_employees."""

from __future__ import annotations

import unittest

from planner import build_plan


class TestPlannerListEmployeesSynonyms(unittest.TestCase):
    def test_find_show_get_employees(self) -> None:
        for prompt in ("find employees", "show employees", "get employees"):
            with self.subTest(prompt=prompt):
                p = build_plan(prompt)
                self.assertEqual(p.workflow, "list_employees")
                self.assertEqual(p.workflow_route, "exact")
        # NM observert: 14 tegn — typisk «find employees» eller «show employees» (ikke «get employees», 13).
        self.assertEqual(len("find employees"), 14)
        self.assertEqual(len("show employees"), 14)

    def test_list_employees_unchanged(self) -> None:
        p = build_plan("list employees")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.workflow_route, "exact")
        self.assertEqual(p.workflow_route_detail, "list employees")

    def test_can_you_show_me_all_employees(self) -> None:
        p = build_plan("Can you show me all employees?")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.workflow_route, "fallback")

    def test_long_natural_language_list_all_employees(self) -> None:
        # Verb and entity not adjacent (no contiguous "list employees" substring).
        p = build_plan(
            "Please list all employees in the Tripletex sandbox company for the accounting review."
        )
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("verb=", p.workflow_route_detail)
        self.assertIn("entity=", p.workflow_route_detail)

    def test_nm_style_long_prompt_word_based(self) -> None:
        # ~178 chars: verb + entity separated by filler (typical NM natural phrasing).
        prompt = (
            "You are assisting with accounting. Please retrieve and display the full list of all "
            "employees registered in this Tripletex tenant so we can verify payroll data before closing."
        )
        self.assertGreater(len(prompt), 120)
        p = build_plan(prompt)
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.workflow_route, "fallback")

    def test_medarbeidere_vis_fallback_no_exact_substring(self) -> None:
        # «ansatte» alene er ikke i strengen — ingen eksakt trigger; ord-basert fallback.
        p = build_plan("Please vis alle medarbeidere i bedriften.")
        self.assertEqual(p.workflow, "list_employees")
        self.assertEqual(p.workflow_route, "fallback")
        self.assertIn("verb=vis", p.workflow_route_detail)
        self.assertIn("entity=medarbeidere", p.workflow_route_detail)

    def test_noop_without_verb_or_entity(self) -> None:
        p = build_plan("The accounting department needs a report.")
        self.assertEqual(p.workflow, "noop")
        self.assertIsNone(p.workflow_route)
        self.assertEqual(p.workflow_route_detail, "")


if __name__ == "__main__":
    unittest.main()
