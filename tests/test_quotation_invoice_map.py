"""Quotation catalog items map onto invoice lines."""
import unittest
from decimal import Decimal

from invoice_tool.quotations.invoice_lines import project_quote_invoice_items
from invoice_tool.quotations.project_layout import parse_project_lines


class QuoteInvoiceLineTests(unittest.TestCase):
    def test_three_lines_total_44100_using_map_name_and_fallback(self):
        technical = {"id": 10, "name": "Technical Services", "tax_rate": 0}
        invoice_projects = [
            {"id": 21, "name": "Installation", "tax_rate": 0},
            {"id": 22, "name": "Recycling", "tax_rate": 0},
            technical,
        ]
        lines = [
            {"section_name": "设备部署", "scope_of_work": "安装管道", "notes": "一周完成",
             "line_total": Decimal("20000.00"), "invoice_project_id": 21},
            {"section_name": "Recycling", "scope_of_work": "纸箱回收", "notes": "",
             "line_total": Decimal("14100.00")},
            {"section_name": "未映射的调试", "scope_of_work": "系统调试",
             "notes": "这是一段明显超过四十个汉字的备注，用来确认过长备注不会写进发票说明，只保留工作范围本身。",
             "line_total": Decimal("10000.00")},
        ]
        items = project_quote_invoice_items(lines, invoice_projects, technical)
        self.assertEqual(len(items), 3)
        self.assertEqual(sum(item["amount"] for item in items), 44100)
        self.assertEqual([item["project_id"] for item in items], [21, 22, 10])
        self.assertEqual([item["mapping"] for item in items], ["map", "name", "technical_services"])
        self.assertIn("安装管道", items[0]["description"])
        self.assertIn("一周完成", items[0]["description"])
        self.assertEqual(items[2]["description"], "系统调试")

    def test_unmapped_line_without_technical_services_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            project_quote_invoice_items(
                [{"section_name": "孤立项目", "line_total": "50", "scope_of_work": "范围"}],
                [{"id": 1, "name": "Other", "tax_rate": 0}],
                None,
            )
        self.assertIn("孤立项目", str(caught.exception))

    def test_legacy_line_keeps_free_text_until_a_catalog_item_is_chosen(self):
        lines, _subtotal = parse_project_lines(
            ["历史安装项"], ["安装管道"], [""], [""], [""], [""], ["800"], ["备注"],
            project_ids=["legacy"],
        )
        self.assertIsNone(lines[0]["project_id"])
        self.assertEqual(lines[0]["section_name"], "历史安装项")
        chosen, _subtotal = parse_project_lines(
            ["历史安装项"], ["安装管道"], ["2"], ["台"], [""], ["14"], [""], ["备注"],
            project_ids=["15"],
        )
        self.assertEqual(chosen[0]["project_id"], 15)
        self.assertEqual(chosen[0]["line_total"], Decimal("28.00"))


class QuoteMapCrudTests(unittest.TestCase):
    def test_add_edit_and_delete_mapping(self):
        import sqlite3
        from flask import Flask, g
        from invoice_tool.catalog.routes import register_catalog_routes

        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.executescript(
            """
            create table projects (
                id integer primary key, name text, project_type text,
                name_key text, name_zh text, name_en text,
                default_amount real, unit_price real, tax_rate real,
                is_active integer, created_at text
            );
            create table quotation_invoice_project_map (
                id integer primary key,
                quotation_project_id integer unique,
                invoice_project_id integer,
                created_at text, updated_at text, updated_by integer
            );
            insert into projects values (1, '设备部署', 'quotation', '', '设备部署', 'Machine deployment', 0, 14, 0, 1, 't');
            insert into projects values (2, 'Installation', 'invoice', '', '安装', 'Installation', 0, 0, 0, 1, 't');
            insert into projects values (3, 'Technical Services', 'invoice', '', '技术服务', 'Technical Services', 0, 0, 0, 1, 't');
            """
        )

        class Connection:
            def execute(self, sql, params=()):
                return raw.execute(sql, params or ())

            def commit(self):
                raw.commit()

            def rollback(self):
                raw.rollback()

        app = Flask(__name__)
        app.secret_key = "test"

        @app.before_request
        def actor():
            g.user = {"id": 9}

        register_catalog_routes(
            app,
            db=lambda: Connection(),
            now=lambda: "2026-11-23",
            login_required=lambda view: view,
            has_action_permission=lambda *args, **kwargs: True,
            to_float=float,
            project_type_labels={"invoice": "发票项目", "quotation": "报价项目"},
            settlement_expense_field_labels={},
            customer_reimbursement_invoice_projects=(),
            merge_duplicate_projects=lambda connection: None,
            project_name_exists=lambda *args, **kwargs: False,
            country_rows=lambda **kwargs: [],
            country_translations=lambda code: [],
        )
        client = app.test_client()
        added = client.post("/projects", data={
            "quote_map_action": "add",
            "quotation_project_id": "1",
            "invoice_project_id": "2",
        })
        self.assertEqual(added.status_code, 302)
        row = raw.execute("select * from quotation_invoice_project_map").fetchone()
        self.assertEqual(row["invoice_project_id"], 2)
        self.assertEqual(row["updated_by"], 9)
        edited = client.post("/projects", data={
            "quote_map_action": "edit",
            "quote_map_id": str(row["id"]),
            "quotation_project_id": "1",
            "invoice_project_id": "3",
        })
        self.assertEqual(edited.status_code, 302)
        self.assertEqual(raw.execute("select invoice_project_id from quotation_invoice_project_map").fetchone()[0], 3)
        deleted = client.post("/projects", data={"quote_map_action": "delete", "quote_map_id": str(row["id"])})
        self.assertEqual(deleted.status_code, 302)
        self.assertIsNone(raw.execute("select id from quotation_invoice_project_map").fetchone())


class CatalogTypeTests(unittest.TestCase):
    def test_quotation_type_is_offered_beside_invoice_items(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "app.py").read_text(encoding="utf-8")
        template = (root / "templates" / "projects.html").read_text(encoding="utf-8")
        self.assertIn('"quotation": "报价项目"', app_source)
        self.assertIn("报价项目映射", template)
        self.assertIn("quote_map_action", template)
        self.assertIn('value="quotation"', template)
        self.assertIn('name="name_zh"', template)
        self.assertIn('name="name_en"', template)


if __name__ == "__main__":
    unittest.main()
