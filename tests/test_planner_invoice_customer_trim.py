"""Narrow planner fix: kunde: without comma before produkt."""

from __future__ import annotations

import unittest

from planner import build_plan


class TestPlannerInvoiceCustomerTrim(unittest.TestCase):
    def test_kunde_label_stops_before_produkt_without_comma(self) -> None:
        p = build_plan("opprett faktura kunde: Acme AS produkt: Kaffe 500 kr")
        self.assertEqual(p.customer_name.strip(), "Acme AS")
        self.assertIn("Kaffe", p.product_name)

    def test_comma_form_unchanged(self) -> None:
        p = build_plan("opprett faktura kunde: Acme AS, produkt: Kaffe 500 kr")
        self.assertEqual(p.customer_name.strip(), "Acme AS")
        self.assertIn("Kaffe", p.product_name)


if __name__ == "__main__":
    unittest.main()
