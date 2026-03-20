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
        # NM observert: 14 tegn — typisk «find employees» eller «show employees» (ikke «get employees», 13).
        self.assertEqual(len("find employees"), 14)
        self.assertEqual(len("show employees"), 14)

    def test_list_employees_unchanged(self) -> None:
        p = build_plan("list employees")
        self.assertEqual(p.workflow, "list_employees")


if __name__ == "__main__":
    unittest.main()
