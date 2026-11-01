"""PDF totals formatting regressions; no application or database startup."""
import unittest
from decimal import Decimal

from invoice_tool.quotations.documents import _styles, _totals_table


class QuotationPdfTotalsTests(unittest.TestCase):
    def test_amounts_are_formatted_once(self):
        for final_amount, discount, expected in [
            (Decimal("7783.02"), Decimal("0"), "$ 7,783.02"),
            (Decimal("6783.02"), Decimal("1000"), "$ 6,783.02"),
            (None, Decimal("1000"), "$ 6,783.02"),
            (Decimal("0"), Decimal("7783.02"), "$ 0.00"),
        ]:
            with self.subTest(final_amount=final_amount, discount=discount):
                table = _totals_table(
                    {"final_amount": final_amount, "discount_amount": discount},
                    Decimal("7783.02"), Decimal("0"), Decimal("7783.02"), _styles(),
                )
                self.assertEqual(table._cellvalues[-1][1].text, expected)
                self.assertEqual(table._cellvalues[-2][1].text, f"$ {discount:,.2f}")


if __name__ == "__main__":
    unittest.main()
