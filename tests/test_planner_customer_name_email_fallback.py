"""Fallback customer label from corporate email domain when name is missing."""

from __future__ import annotations

import unittest

from planner import _extract_customer_name_fallback_from_email


class TestCustomerNameEmailFallback(unittest.TestCase):
    def test_corporate_domain_no(self) -> None:
        self.assertEqual(
            _extract_customer_name_fallback_from_email("kontakt@ola-bygg.no"),
            "Ola Bygg",
        )

    def test_mail_subdomain(self) -> None:
        self.assertEqual(
            _extract_customer_name_fallback_from_email("x@mail.acme.no"),
            "Acme",
        )

    def test_gmail_skipped(self) -> None:
        self.assertEqual(_extract_customer_name_fallback_from_email("a@gmail.com"), "")

    def test_hotmail_skipped(self) -> None:
        self.assertEqual(_extract_customer_name_fallback_from_email("x@hotmail.com"), "")


if __name__ == "__main__":
    unittest.main()
