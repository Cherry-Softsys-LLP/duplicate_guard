"""
Unit tests for the normalization engine.

These test only pure functions, so they need no database and run instantly.
Run just this file with::

    bench --site yoursite run-tests \\
        --module duplicate_guard.tests.test_normalizer
"""

import unittest

from duplicate_guard.core import normalizer


class TestNameNormalization(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(normalizer.normalize_name("ABC Industries"), "abc industries")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(normalizer.normalize_name("ABC     Industries"), "abc industries")

    def test_trims_edges(self):
        self.assertEqual(normalizer.normalize_name("  abc industries  "), "abc industries")

    def test_all_variants_equal(self):
        variants = ["ABC Industries", "ABC     Industries", "  abc industries "]
        normalized = {normalizer.normalize_name(v) for v in variants}
        self.assertEqual(normalized, {"abc industries"})

    def test_handles_none(self):
        self.assertEqual(normalizer.normalize_name(None), "")

    def test_handles_tabs_and_newlines(self):
        self.assertEqual(normalizer.normalize_name("ABC\tIndustries\n"), "abc industries")


class TestEmailNormalization(unittest.TestCase):
    def test_lowercases_and_trims(self):
        self.assertEqual(normalizer.normalize_email("Sales@ABC.com"), "sales@abc.com")

    def test_trims_whitespace(self):
        self.assertEqual(normalizer.normalize_email("  SALES@abc.COM "), "sales@abc.com")

    def test_handles_none(self):
        self.assertEqual(normalizer.normalize_email(None), "")


class TestPhoneNormalization(unittest.TestCase):
    def test_plus_country_code_spaced(self):
        self.assertEqual(normalizer.normalize_phone("+91 9876543210"), "+919876543210")

    def test_leading_zero(self):
        self.assertEqual(normalizer.normalize_phone("09876543210"), "+919876543210")

    def test_country_code_with_dash(self):
        self.assertEqual(normalizer.normalize_phone("+91-9876543210"), "+919876543210")

    def test_internal_space(self):
        self.assertEqual(normalizer.normalize_phone("98765 43210"), "+919876543210")

    def test_same_indian_number_variants_all_equal(self):
        # The SAME Indian number entered many ways must collapse to one value.
        # (A bare number that embeds its country code with no '+', e.g.
        # "919876543210", is intentionally omitted here: it is genuinely
        # ambiguous and its handling can differ between the library and the
        # built-in fallback.)
        variants = [
            "+91 9876543210",
            "09876543210",
            "+91-9876543210",
            "98765 43210",
            "9876543210",
            "0091 9876543210",
        ]
        normalized = {normalizer.normalize_phone(v) for v in variants}
        self.assertEqual(normalized, {"+919876543210"})

    def test_double_zero_international_prefix(self):
        self.assertEqual(normalizer.normalize_phone("00919876543210"), "+919876543210")

    def test_brackets_and_dots(self):
        self.assertEqual(normalizer.normalize_phone("(+91).98765.43210"), "+919876543210")

    def test_bare_ten_digits_gets_default_country_code(self):
        self.assertEqual(normalizer.normalize_phone("9876543210"), "+919876543210")

    def test_different_country_same_national_digits_do_not_match(self):
        # THE key fix: same 10 national digits, different country codes, must be
        # DIFFERENT canonical values (no false duplicate).
        india = normalizer.normalize_phone("+91 9876543210")
        usa = normalizer.normalize_phone("+1 9876543210")
        self.assertNotEqual(india, usa)
        self.assertEqual(india, "+919876543210")
        self.assertEqual(usa, "+19876543210")

    def test_us_number_with_default_region(self):
        # A bare US number interpreted with the US region keeps the +1 code.
        self.assertEqual(
            normalizer.normalize_phone(
                "(415) 555-2671", default_country_code="1",
                national_number_length=10, default_region="US",
            ),
            "+14155552671",
        )

    def test_handles_none(self):
        self.assertEqual(normalizer.normalize_phone(None), "")

    def test_handles_blank(self):
        self.assertEqual(normalizer.normalize_phone("   "), "")


if __name__ == "__main__":
    unittest.main()
