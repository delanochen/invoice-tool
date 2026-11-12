import sqlite3
import unittest

from invoice_tool.customer_reimbursements.export_attachments import exportable_settlement_attachments


class SettlementExportAttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            create table customer_reimbursement_attachments (
                id integer primary key,
                customer_reimbursement_id integer not null,
                original_filename text,
                stored_filename text,
                source_expense_attachment_id integer
            );
            create table expense_attachments (
                id integer primary key,
                expense_id integer not null,
                expense_item_key text
            );
            create table expense_items (
                id integer primary key,
                expense_id integer not null,
                line_key text,
                amount real
            );
            create table customer_reimbursement_expense_links (
                customer_reimbursement_id integer not null,
                expense_item_id integer not null,
                amount_snapshot real,
                settled_amount real
            );
            """
        )

    def names(self):
        return [row["original_filename"] for row in exportable_settlement_attachments(self.conn, 10)]

    def test_export_filename_uses_customer_order_site_state_and_dates(self):
        from invoice_tool.customer_reimbursements.expense_report import settlement_export_filename

        self.conn.execute("create table buyers (id integer, name text, state_code text)")
        self.conn.execute("insert into buyers values (1, 'Wever / East', 'ia')")
        order = {"buyer_id": 1, "client_order_number": "SHPG2026102913515",
                 "order_number": "SO2610004", "site_address": "Wever, IA 52658"}
        context = (order, {}, "INV-1", "2026-11-02", "2026-11-06")
        expected = "工单_SHPG2026102913515_Wever - East_IA_2026-11-02~2026-11-06.xlsx"
        self.assertEqual(settlement_export_filename(context, self.conn), expected)
        self.conn.execute("update buyers set state_code = null")
        self.assertEqual(settlement_export_filename(context, self.conn), expected)

    def test_manual_kept_zero_customer_amount_excluded_positive_kept(self):
        self.conn.executemany(
            "insert into expense_items(id, expense_id, line_key, amount) values(?,?,?,?)",
            [(1, 100, "a", 50), (2, 101, "b", 60), (3, 102, "c", 70)],
        )
        self.conn.executemany(
            "insert into expense_attachments(id, expense_id, expense_item_key) values(?,?,?)",
            [(11, 100, "a"), (12, 101, "b"), (13, 102, "c")],
        )
        self.conn.executemany(
            "insert into customer_reimbursement_expense_links values(?,?,?,?)",
            [(10, 1, 50, 0), (10, 2, 60, 25), (10, 3, 70, None)],
        )
        self.conn.executemany(
            "insert into customer_reimbursement_attachments values(?,?,?,?,?)",
            [
                (1, 10, "manual.pdf", "m.pdf", None),
                (2, 10, "zero.pdf", "z.pdf", 11),
                (3, 10, "partial.pdf", "p.pdf", 12),
                (4, 10, "full.pdf", "f.pdf", 13),
                (5, 10, "里程佐证-2026-11-12-map.png", "proof.png", None),
            ],
        )
        self.assertEqual(self.names(), ["manual.pdf", "partial.pdf", "full.pdf"])

    def test_expense_level_attachment_kept_when_any_linked_line_is_positive(self):
        self.conn.executemany(
            "insert into expense_items(id, expense_id, line_key, amount) values(?,?,?,?)",
            [(1, 100, "a", 50), (2, 100, "b", 60)],
        )
        self.conn.execute("insert into expense_attachments values(11,100,null)")
        self.conn.executemany(
            "insert into customer_reimbursement_expense_links values(?,?,?,?)",
            [(10, 1, 50, 0), (10, 2, 60, 10)],
        )
        self.conn.execute(
            "insert into customer_reimbursement_attachments values(1,10,'shared.pdf','s.pdf',11)"
        )
        self.assertEqual(self.names(), ["shared.pdf"])



    def test_expense_level_attachment_excluded_when_all_lines_zero(self):
        self.conn.executemany("insert into expense_items values(?,?,?,?)",
                              [(1,100,"a",50),(2,100,"b",60)])
        self.conn.execute("insert into expense_attachments values(11,100,'')")
        self.conn.executemany("insert into customer_reimbursement_expense_links values(?,?,?,?)",
                              [(10,1,50,0),(10,2,60,0)])
        self.conn.execute("insert into customer_reimbursement_attachments values(1,10,'shared.pdf','s.pdf',11)")
        self.assertEqual(self.names(), [])

    def test_unselected_line_and_other_settlement_do_not_include_attachment(self):
        self.conn.execute("insert into expense_items values(1,100,'a',50)")
        self.conn.execute("insert into expense_attachments values(11,100,'a')")
        self.conn.execute("insert into customer_reimbursement_expense_links values(11,1,50,50)")
        self.conn.execute("insert into customer_reimbursement_attachments values(1,10,'other.pdf','s.pdf',11)")
        self.assertEqual(self.names(), [])

    def test_legacy_null_amount_uses_live_amount_when_snapshot_missing(self):
        self.conn.execute("insert into expense_items values(1,100,'a',50)")
        self.conn.execute("insert into expense_attachments values(11,100,'a')")
        self.conn.execute("insert into customer_reimbursement_expense_links values(10,1,null,null)")
        self.conn.execute("insert into customer_reimbursement_attachments values(1,10,'legacy.pdf','s.pdf',11)")
        self.assertEqual(self.names(), ["legacy.pdf"])


    def test_batch_zip_includes_only_exportable_attachments_and_keeps_duplicate_names(self):
        import tempfile
        from io import BytesIO
        from pathlib import Path
        from zipfile import ZipFile
        from unittest.mock import patch
        from flask import Flask
        from invoice_tool.batch_exports.service import run_export_job
        self.conn.executescript("""
            create table batch_export_jobs(id text,order_ids text,export_type text,status text,progress integer,started_at text,file_name text,stored_path text,completed_at text,error text);
            insert into batch_export_jobs(id,order_ids,export_type) values('test','[1]','settlements');
            create table service_orders(id integer,order_number text,buyer_id integer,client_order_number text,client_name text);
            insert into service_orders values(1,'SO-TEST',null,null,'Test Site');
            create table users (id integer, name text);
            create table field_photos (order_id integer, user_id integer, photo_type text, repair_phase text, captured_at text, id integer);
            create table field_repairs (id text,order_id integer,completed_date text,equipment_model text,equipment_number text,fault text,result text,work_content text);
            insert into field_repairs values('repair',1,'2026-11-12','LCI-400CR','12345','Alarm','Clear','Fuse replacement');
            create table service_reports (id integer, service_order_id integer, report_date text);
            insert into service_reports values(1,1,'2026-11-12'),(2,1,'2026-11-12');
            create table service_report_attachments (id integer,report_id integer,category text,stored_filename text,original_filename text);
            insert into service_report_attachments values(1,1,'mileage_proof','proof.pdf','route.pdf');
            create table buyers(id integer,owner text,owner_id integer);
            create table owners(id integer,name text);
            create table customer_reimbursements(id integer,service_order_id integer,is_current boolean);
            insert into customer_reimbursements values(10,1,true);
            insert into expense_items values(1,100,'a',50),(2,101,'b',60);
            insert into expense_attachments values(11,100,'a'),(12,101,'b');
            insert into customer_reimbursement_expense_links values(10,1,50,0),(10,2,60,25);
            insert into customer_reimbursement_attachments values
            (1,10,'receipt.pdf','zero.pdf',11),(2,10,'receipt.pdf','positive.pdf',12),
            (3,10,'receipt.pdf','manual.pdf',null),(4,10,'里程佐证-2026-11-12-map.png','proof.png',null);
        """)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'customer-reimbursements'/'10'/'attachments'
            root.mkdir(parents=True)
            for name in ('zero.pdf','positive.pdf','manual.pdf','proof.png'):
                (root/name).write_bytes(name.encode())
            proof = Path(directory) / 'proof.pdf'
            proof.write_bytes(b'route')
            context = ({"order_number": "SO-TEST"}, {"id": 10}, "INV-1", None, None)
            with (
                patch('invoice_tool.batch_exports.service.batch_contexts', return_value={1: context}),
                patch('invoice_tool.batch_exports.service.build_expense_report', return_value=BytesIO(b'xlsx')),
                patch('invoice_tool.batch_exports.service.build_batch_summary', return_value=BytesIO(b'ledger')),
            ):
                run_export_job('test',app=Flask(__name__),db=lambda:self.conn,
                    data_dir=directory,build_service_report_docx=lambda report, order: b'word',build_simple_xlsx=None,
                    customer_reimbursement_items=lambda _:[],expense_amount=None,
                    render_invoice_pdf=None,report_attachment_path=lambda attachment: proof)
            job=self.conn.execute("select * from batch_export_jobs").fetchone()
            self.assertEqual(job['status'],'completed',job['error'])
            self.assertEqual(job["file_name"], "工单_SO-TEST.zip")
            with ZipFile(job["stored_path"]) as archive:
                names = archive.namelist()
                self.assertIn("工单_SO-TEST/工单_SO-TEST.xlsx", names)
                report_path = "工单_SO-TEST/现场维修统计表.xlsx"
                self.assertIn(report_path, names)
                from openpyxl import load_workbook
                report = load_workbook(BytesIO(archive.read(report_path))).active
                self.assertEqual([cell.value for cell in report[3]][:5],
                                 ["2026-11-12", "Test Site", "LCI-400CR", "12345", "Fuse replacement"])
                self.assertIn("工单_SO-TEST/附件/", names)
                self.assertIn("工单结算总账单.xlsx", names)
                self.assertIn("工单_SO-TEST/", names)
                self.assertIn("工单_SO-TEST/日报/SO-TEST-2026-11-12-report.docx", names)
                self.assertIn("工单_SO-TEST/日报/SO-TEST-2026-11-12-report-2.docx", names)
                self.assertEqual(
                    archive.read("工单_SO-TEST/日报/SO-TEST-2026-11-12-report_里程佐证/1-route.pdf"), b"route",
                )
                self.assertEqual([name for name in names if name.endswith("工单结算总账单.xlsx")],
                                 ["工单结算总账单.xlsx"])
                self.assertFalse(any(name.startswith("工单结算/") for name in names))
                bodies = [archive.read(name) for name in names if name.startswith("工单_SO-TEST/")]
                self.assertNotIn(b'zero.pdf',bodies)
                self.assertNotIn(b'proof.png',bodies)
                self.assertIn(b'positive.pdf',bodies)
                self.assertIn(b'manual.pdf',bodies)
                self.assertEqual(len(archive.namelist()),len(set(archive.namelist())))
                self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
