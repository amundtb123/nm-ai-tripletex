"""Invoice/faktura as reference vs primary billing task — standalone and OOS boundaries."""

from __future__ import annotations

import unittest

from planner_llm import (
    _billing_invoice_primary_task,
    _non_green_accounting_context,
    _score_green_workflows,
    _standalone_green_request,
    _weak_green_recall_eligible,
)


class TestInvoiceReferenceAllowsGreen(unittest.TestCase):
    def test_norwegian_price_with_faktura_kun_referanse(self) -> None:
        p = "Hva er prisen på 'Bolt M8'? Faktura er kun referanse."
        self.assertFalse(_billing_invoice_primary_task(p))
        self.assertTrue(_standalone_green_request(p))
        self.assertFalse(_non_green_accounting_context(p))

    def test_english_price_product_invoice_hash_context_only(self) -> None:
        p = "What is the price for 'SuperWidget 3000'? Invoice # is for context only."
        self.assertFalse(_billing_invoice_primary_task(p))
        self.assertTrue(_standalone_green_request(p))
        self.assertFalse(_non_green_accounting_context(p))

    def test_finn_kunde_with_faktura_nr_reference_same_search_score_as_without(self) -> None:
        """Heuristic must not dock search_customer when faktura nr is incidental reference."""
        base = "Finn kunden Hansen AS."
        with_ref = "Finn kunden Hansen AS. Faktura nr 991234 kun referanse."
        self.assertFalse(_billing_invoice_primary_task(with_ref))
        self.assertTrue(_standalone_green_request(with_ref))
        self.assertEqual(
            _score_green_workflows(base)["search_customer"],
            _score_green_workflows(with_ref)["search_customer"],
        )

    def test_register_new_customer_invoice_reference_still_weak_eligible(self) -> None:
        p = (
            "Registrer ny kunde Ola Bygg AS med e-post post@ola.no — "
            "faktura vedlegg kun referanse"
        )
        self.assertFalse(_billing_invoice_primary_task(p))
        self.assertTrue(_weak_green_recall_eligible(p))


class TestInvoicePrimaryTaskStaysOos(unittest.TestCase):
    def test_invoice_line_price_not_standalone(self) -> None:
        p = "What is the price on invoice line 3 for customer Acme?"
        self.assertTrue(_billing_invoice_primary_task(p))
        self.assertFalse(_standalone_green_request(p))

    def test_wrong_invoice_dispute_not_green(self) -> None:
        p = "The invoice is wrong for customer Acme — dispute"
        self.assertTrue(_billing_invoice_primary_task(p))
        self.assertTrue(_non_green_accounting_context(p))

    def test_opprett_faktura_primary(self) -> None:
        p = "Opprett faktura for kunde Acme AS"
        self.assertTrue(_billing_invoice_primary_task(p))


if __name__ == "__main__":
    unittest.main()
