"""统一商务单据计费纯逻辑单元测试（无需数据库，可直接 pytest 运行）。

覆盖已确认业务规则的验收项：
  * 自驾计费：100 英里、2 小时，里程单价 $0.70、交通工时单价 $30
      - 仅里程 $70；仅时长 $60；里程＋时长 $130。
  * 折扣：折扣前 $1,000、折扣 $100，最终报价 $900；重复计算仍只扣一次。
  * 固定总价 / 按实际数量结算分别符合规则；
  * 未填写与明确 0 的费率区分；折扣约束（负数 / 超过总额拒绝）；
  * 按实际数量且折扣超额时不产生负数应收。
"""

import unittest
from decimal import Decimal

from invoice_tool import commercial_billing as cb


class DrivingTransportTest(unittest.TestCase):
    """验收：自驾计费三种方式。"""

    def setUp(self):
        # 100 英里、2 小时、$0.70/英里、$30/小时
        self.miles = 100
        self.hours = 2
        self.mileage_rate = "0.70"
        self.time_rate = "30"

    def test_mileage_only(self):
        self.assertEqual(
            cb.driving_transport_amount("mileage_only", self.miles, self.hours,
                                        self.mileage_rate, self.time_rate),
            Decimal("70.00"),
        )

    def test_time_only(self):
        self.assertEqual(
            cb.driving_transport_amount("time_only", self.miles, self.hours,
                                        self.mileage_rate, self.time_rate),
            Decimal("60.00"),
        )

    def test_mileage_and_time(self):
        self.assertEqual(
            cb.driving_transport_amount("mileage_and_time", self.miles, self.hours,
                                        self.mileage_rate, self.time_rate),
            Decimal("130.00"),
        )

    def test_rounding_to_cent(self):
        # 0.333 英里 × 0.70 = 0.2331 → 0.23
        self.assertEqual(
            cb.driving_transport_amount("mileage_only", "0.333", 0, "0.70", 0),
            Decimal("0.23"),
        )


class DrivingRateMissingTest(unittest.TestCase):
    """验收：启用项目须校验对应费率已明确填写；未填写与明确 0 区分。"""

    def test_mileage_only_requires_mileage_rate(self):
        self.assertEqual(
            cb.driving_rate_missing("mileage_only", None, "30"), ["自驾里程单价"]
        )
        self.assertEqual(
            cb.driving_rate_missing("mileage_only", "", "30"), ["自驾里程单价"]
        )
        # 明确填 0 = 已配置，不算缺失
        self.assertEqual(cb.driving_rate_missing("mileage_only", 0, "30"), [])

    def test_time_only_requires_time_rate(self):
        self.assertEqual(
            cb.driving_rate_missing("time_only", "0.70", None), ["自驾交通工时单价"]
        )

    def test_mileage_and_time_requires_both(self):
        self.assertEqual(
            cb.driving_rate_missing("mileage_and_time", None, None),
            ["自驾里程单价", "自驾交通工时单价"],
        )
        self.assertEqual(cb.driving_rate_missing("mileage_and_time", "0.70", "30"), [])

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            cb.normalize_driving_mode("per_vehicle")


class QuoteDiscountTest(unittest.TestCase):
    """验收：报价单折扣。"""

    def test_gross_1000_discount_100_final_900(self):
        result = cb.quote_amounts("1000", "100")
        self.assertEqual(result["gross_total"], Decimal("100.00") * 10)  # 1000.00
        self.assertEqual(result["discount_amount"], Decimal("100.00"))
        self.assertEqual(result["final_amount"], Decimal("900.00"))

    def test_default_discount_zero(self):
        result = cb.quote_amounts("1234.56")
        self.assertEqual(result["discount_amount"], Decimal("0.00"))
        self.assertEqual(result["final_amount"], Decimal("1234.56"))

    def test_idempotent_apply_once(self):
        # 重复计算仍只扣一次：纯函数，同输入同输出；折扣只被消费一次。
        first = cb.quote_amounts("1000", "100")
        second = cb.quote_amounts("1000", "100")
        self.assertEqual(first["final_amount"], Decimal("900.00"))
        self.assertEqual(second["final_amount"], Decimal("900.00"))

    def test_negative_discount_rejected(self):
        with self.assertRaises(ValueError):
            cb.quote_amounts("1000", "-5")

    def test_discount_exceeding_gross_rejected(self):
        with self.assertRaises(ValueError):
            cb.quote_amounts("1000", "1001")


class SettlementResolutionTest(unittest.TestCase):
    """验收：固定总价 / 按实际数量结算。"""

    def test_fixed_uses_final_quote_amount(self):
        result = cb.resolve_quote_settlement("fixed", "1000", "100", actual_gross_total=None)
        self.assertEqual(result["basis"], "fixed")
        self.assertEqual(result["base_amount"], Decimal("900.00"))
        self.assertEqual(result["discount_amount"], Decimal("100.00"))

    def test_actual_applies_discount_once_to_actual_total(self):
        # 实际数量合计 1100，折扣 100 → 应收 1000（只扣一次）
        result = cb.resolve_quote_settlement("actual", "1000", "100", actual_gross_total="1100")
        self.assertEqual(result["basis"], "actual")
        self.assertEqual(result["base_amount"], Decimal("1000.00"))
        self.assertEqual(result["discount_amount"], Decimal("100.00"))
        self.assertFalse(result["excess_discount"])

    def test_actual_repeated_calc_no_double_discount(self):
        # 重算：以实际折扣前金额为基础，每次只减一次折扣
        r1 = cb.resolve_quote_settlement("actual", "1000", "100", "1100")
        r2 = cb.resolve_quote_settlement("actual", "1000", "100", "1100")
        self.assertEqual(r1["base_amount"], r2["base_amount"])
        self.assertEqual(r1["base_amount"], Decimal("1000.00"))

    def test_actual_discount_exceeding_actual_total_no_negative(self):
        # 实际折扣前 80，折扣 100 → 应收 0（封顶），提示超额，不产生负数
        result = cb.resolve_quote_settlement("actual", "1000", "100", actual_gross_total="80")
        self.assertEqual(result["base_amount"], Decimal("0.00"))
        self.assertEqual(result["discount_amount"], Decimal("80.00"))
        self.assertTrue(result["excess_discount"])

    def test_invalid_settlement_mode_rejected(self):
        with self.assertRaises(ValueError):
            cb.normalize_settlement_mode("hybrid")


if __name__ == "__main__":
    unittest.main()
