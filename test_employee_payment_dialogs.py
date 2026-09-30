"""员工付款单详情：需要填原因 / 选账户的动作必须走弹窗（v0.1.346）。

用户诉求：「选中付款单，点『取消』后弹出窗口填『取消原因』——请用弹出式，界面也很丑」。
原因是这些动作原本是挤在 ERP 工具栏里的裸 `<input name="reason" placeholder="取消原因">`：
没有标签、看不出必填，几个输入框还会把整条工具栏撑破。

契约（改这几处必须同步本文件）：
- 工具栏只留按钮，需要补充信息的动作一律 `<button data-dialog-open=...>`；
- 每个弹窗是一个独立 `<form>`，隐藏字段 `action` 对应后端的 transition 白名单
  （submit / approve / reject / ready / mark_paid / retry / fail / cancel）；
- `mark_paid` 必须带 `bank_account_id` + `payment_method`（后端校验必填）；
- 页面必须加载 `modal-forms.js`，否则 `data-dialog-open` 点了没反应。
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
TEMPLATE = ROOT / "templates" / "employee_payment_detail.html"


class PaymentActionDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8")

    def test_toolbar_has_no_inline_reason_inputs(self):
        """工具栏里不再塞裸输入框 —— 那是「界面很丑」的直接原因。"""
        for placeholder in ("取消原因", "驳回原因", "失败原因"):
            self.assertNotIn(f'placeholder="{placeholder}"', self.html)
        self.assertNotIn('name="bank_account_id" required><option value="">付款账户', self.html)

    def test_actions_open_dialogs(self):
        for dialog in ("rejectPayment", "payPayment", "failPayment", "retryPayment", "cancelPayment"):
            self.assertIn(f'data-dialog-open="{dialog}"', self.html)
            self.assertIn(f'<dialog id="{dialog}" class="modal-dialog">', self.html)

    def test_each_dialog_posts_its_own_action(self):
        pairs = {
            "rejectPayment": "reject",
            "payPayment": "mark_paid",
            "failPayment": "fail",
            "retryPayment": "retry",
            "cancelPayment": "cancel",
        }
        for dialog, action in pairs.items():
            block = self.html.split(f'<dialog id="{dialog}"', 1)[1].split("</dialog>", 1)[0]
            self.assertIn(f'name="action" value="{action}"', block, f"{dialog} 应提交 action={action}")

    def test_pay_dialog_requires_account_and_method(self):
        block = self.html.split('<dialog id="payPayment"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn('name="bank_account_id" required', block)
        self.assertIn('name="payment_method" required', block)
        self.assertIn('name="external_transaction_id"', block)

    def test_reason_fields_are_textareas_with_labels(self):
        for dialog, label in (("rejectPayment", "驳回原因"), ("failPayment", "失败原因"), ("cancelPayment", "取消原因")):
            block = self.html.split(f'<dialog id="{dialog}"', 1)[1].split("</dialog>", 1)[0]
            self.assertIn(f"<label>{label}<textarea", block, f"{dialog} 的原因要有标签 + textarea")

    def test_page_loads_modal_forms(self):
        self.assertIn("modal-forms.js", self.html)


if __name__ == "__main__":
    unittest.main()
