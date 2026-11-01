"""Safety matrix for historical positive payroll reconciliation; no database."""
import unittest
from datetime import date
from decimal import Decimal as D
from invoice_tool.payroll.reconciliation import build_reconciliation_services

class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows

class ReplacementSafetyTest(unittest.TestCase):
    def row(self, *, expected="1995.50", booked="1563.28", snapshot=None,
            negative=False, manual=False, linked=False, held=False, carry=False, incomplete=False):
        order = dict(id=1, employee_id=7, employee_name="岳峰",
                     source_type="system_payroll", source_number="2026-09-14~2026-09-27",
                     status="paid", correction_status="replacement", gross_amount=D(booked),
                     payment_number="SL-2610-0053")
        orders = [order]
        if held:
            orders.append(dict(order, id=2, correction_status="correction_pending"))
        if manual or linked:
            orders.append(dict(order, id=3, source_type="manual",
                               source_number=order["source_number"] if linked else ""))
        def line(code, amount):
            return dict(component_code=code, component_name=code, amount=D(amount),
                        service_date="2026-09-14", work_order_id=1)
        recorded = [line("standard_pay", snapshot or booked)]
        current = [line("standard_pay", expected)]
        if negative:
            recorded.append(line("old_allowance", "10"))
            recorded[0]["amount"] -= D("10")
        if incomplete:
            current[0]["amount"] += D("1")
        class DB:
            def execute(self, sql, params=None):
                if "join users" in sql: return Result(orders)
                if "employee_salary_agreements" in sql: return Result([])
                if "effective_components" in sql: return Result(recorded)
                raise AssertionError(sql)
        api = dict(db=DB, payroll_period_dates=lambda start:(date(2026,9,27),),
                   payroll_cycle_start_date=lambda:date(2026,9,14),
                   payroll_rows_for_period=lambda start:dict(rows=[dict(worker_id=7,worker_name="岳峰",total_pay=D(expected))]),
                   payroll_payment_components=lambda *args:(current,None),
                   correction_component_cte=lambda:"unused",
                   flag_carry_forward_components=lambda *args:carry)
        return build_reconciliation_services(api)[0](include_balanced=True)[0]

    def test_yuefeng_43222_can_create(self):
        row=self.row()
        self.assertEqual(row["difference"],D("432.22"))
        self.assertTrue(row["can_create"])
        self.assertEqual(row["status_label"],"历史工资已变更 · 正差额可补发")
        self.assertEqual(row["blocked_reason"],"")

    def test_unsafe_replacements_remain_blocked(self):
        for kwargs, status in [
            (dict(snapshot="1500"),"source_mismatch"),
            (dict(negative=True),"negative_review"),
            (dict(expected="1500"),"negative_review"),
            (dict(manual=True),"manual_conflict"),
            (dict(linked=True),"manual_conflict"),
            (dict(held=True),"historical_change"),
            (dict(carry=True),"historical_change"),
            (dict(incomplete=True),"source_mismatch"),
            (dict(expected="1563.30"),"rounding"),
            (dict(expected="1563.26"),"rounding")]:
            with self.subTest(kwargs=kwargs):
                row=self.row(**kwargs)
                self.assertFalse(row["can_create"])
                self.assertEqual(row["status"],status)

if __name__ == "__main__": unittest.main()
