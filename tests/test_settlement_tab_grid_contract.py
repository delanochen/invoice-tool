import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettlementTabGridContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "customer_reimbursement_form.html").read_text(
            encoding="utf-8"
        )

    def test_hidden_tab_grids_are_redrawn_after_activation(self):
        self.assertIn('instance.source.closest("[data-settlement-tab-panel]") === panel', self.template)
        self.assertIn("instance.grid.redraw(true)", self.template)

    def test_tab_grid_loading_state_is_visible_and_accessible(self):
        self.assertIn('loading.className = "settlement-tab-loading"', self.template)
        self.assertIn('loading.setAttribute("role", "status")', self.template)
        self.assertIn("正在加载表格明细…", self.template)
        self.assertIn("@keyframes settlement-grid-spin", self.template)


if __name__ == "__main__":
    unittest.main()
