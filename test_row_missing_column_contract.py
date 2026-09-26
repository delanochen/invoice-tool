# -*- coding: utf-8 -*-
"""Row 缺失列的异常契约（回归 v0.1.311 前「生成工单结算草稿」生产 500）。

背景：模板引用了不存在的列 `reimbursement.reimbursement_number`。
- SQLite 测试环境用 sqlite3.Row，缺列抛 IndexError（LookupError），Jinja 按
  undefined 渲染成空串 → 测试全绿。
- PostgreSQL 生产用自定义 Row，缺列抛 ValueError → Jinja 不捕获 → 500。
两种环境行为不一致，测试永远发现不了这类模板 bug。

修复：自定义 Row 缺列改抛 MissingRowColumnError(KeyError, ValueError)：
- KeyError 让 Jinja 按 undefined 渲染，与 SQLite 测试一致，不再 500；
- ValueError 保持旧兼容面。
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class RowMissingColumnContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "database.py"
        module_path.write_text((REPO_DIR / "database.py").read_text(encoding="utf-8"), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("invoice_tool_row_contract_db", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_missing_column_raises_dual_keyerror_valueerror(self):
        MissingRowColumnError = self.module.MissingRowColumnError
        row = self.module.Row(["id", "status"], [1, "draft"])
        with self.assertRaises(MissingRowColumnError):
            row["reimbursement_number"]
        try:
            row["reimbursement_number"]
        except KeyError:
            pass
        try:
            row["reimbursement_number"]
        except ValueError:
            pass

    def test_jinja_renders_missing_column_as_empty_not_500(self):
        import jinja2
        row = self.module.Row(["id"], [7])
        html = jinja2.Environment().from_string("A{{ r.missing_col }}B").render(r=row)
        self.assertEqual(html, "AB")

    def test_normal_access_and_iteration_unaffected(self):
        row = self.module.Row(["id", "status"], [1, "draft"])
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["status"], "draft")
        self.assertEqual(list(row), [1, "draft"])
        self.assertEqual(len(row), 2)
        self.assertEqual(row[0], 1)
        with self.assertRaises(IndexError):
            row[5]


if __name__ == "__main__":
    unittest.main()
