"""Financial statements derived only from posted accounting entries."""

from decimal import Decimal


ZERO = Decimal("0")


class FinancialReportService:
    def __init__(self, connection):
        self.connection = connection

    def account_balances(self, *, date_from=None, date_to=None):
        conditions = ["v.status in ('posted','reversed')"]
        params = []
        if date_from:
            conditions.append("v.accounting_date>=?")
            params.append(str(date_from))
        if date_to:
            conditions.append("v.accounting_date<=?")
            params.append(str(date_to))
        rows = self.connection.execute(
            "select a.id,a.account_code,a.account_name,a.account_type,a.normal_balance,"
            "coalesce(sum(case when v.id is not null then e.debit else 0 end),0) debit,"
            "coalesce(sum(case when v.id is not null then e.credit else 0 end),0) credit "
            "from accounts a left join voucher_entries e on e.account_id=a.id "
            "left join vouchers v on v.id=e.voucher_id and " + " and ".join(conditions) +
            " where a.is_active=true group by a.id order by a.account_code", params,
        ).fetchall()
        result = []
        for row in rows:
            debit = Decimal(str(row["debit"]))
            credit = Decimal(str(row["credit"]))
            normal_balance = debit - credit if row["normal_balance"] == "debit" else credit - debit
            result.append({
                "id": row["id"], "account_code": row["account_code"],
                "account_name": row["account_name"], "account_type": row["account_type"],
                "normal_balance": row["normal_balance"], "debit": debit,
                "credit": credit, "balance": normal_balance,
            })
        return result

    def income_statement(self, *, date_from=None, date_to=None):
        balances = self.account_balances(date_from=date_from, date_to=date_to)
        revenues = [row for row in balances if row["account_type"] == "revenue"]
        expenses = [row for row in balances if row["account_type"] == "expense"]
        revenue_total = sum((row["balance"] for row in revenues), ZERO)
        expense_total = sum((row["balance"] for row in expenses), ZERO)
        return {
            "revenues": revenues,
            "expenses": expenses,
            "revenue_total": revenue_total,
            "expense_total": expense_total,
            "net_income": revenue_total - expense_total,
        }

    def balance_sheet(self, *, as_of=None):
        balances = self.account_balances(date_to=as_of)
        assets = [row for row in balances if row["account_type"] == "asset"]
        liabilities = [row for row in balances if row["account_type"] == "liability"]
        equity = [row for row in balances if row["account_type"] == "equity"]
        revenues = [row for row in balances if row["account_type"] == "revenue"]
        expenses = [row for row in balances if row["account_type"] == "expense"]
        current_earnings = (
            sum((row["balance"] for row in revenues), ZERO)
            - sum((row["balance"] for row in expenses), ZERO)
        )
        asset_total = sum((row["balance"] for row in assets), ZERO)
        liability_total = sum((row["balance"] for row in liabilities), ZERO)
        equity_account_total = sum((row["balance"] for row in equity), ZERO)
        equity_total = equity_account_total + current_earnings
        right_total = liability_total + equity_total
        return {
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "asset_total": asset_total,
            "liability_total": liability_total,
            "equity_account_total": equity_account_total,
            "current_earnings": current_earnings,
            "equity_total": equity_total,
            "right_total": right_total,
            "difference": asset_total - right_total,
            "balanced": asset_total == right_total,
        }
