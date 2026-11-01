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
            create table service_orders(id integer,order_number text,buyer_id integer);
            insert into service_orders values(1,'SO-TEST',null);
            create table buyers(id integer,owner text,owner_id integer);
            create table owners(id integer,name text);
            create table customer_reimbursements(id integer,service_order_id integer,is_current boolean);
            insert into customer_reimbursements values(10,1,true);
            insert into expense_items values(1,100,'a',50),(2,101,'b',60);
            insert into expense_attachments values(11,100,'a'),(12,101,'b');
            insert into customer_reimbursement_expense_links values(10,1,50,0),(10,2,60,25);
            insert into customer_reimbursement_attachments values
            (1,10,'receipt.pdf','zero.pdf',11),(2,10,'receipt.pdf','positive.pdf',12),
            (3,10,'receipt.pdf','manual.pdf',null);
        """)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'customer-reimbursements'/'10'/'attachments'
            root.mkdir(parents=True)
            for name in ('zero.pdf','positive.pdf','manual.pdf'):
                (root/name).write_bytes(name.encode())
            with patch('invoice_tool.batch_exports.service.build_settlement_xlsx',return_value=BytesIO(b'xlsx')), patch('invoice_tool.batch_exports.service.build_master_ledger',return_value=BytesIO(b'ledger')):
                run_export_job('test',app=Flask(__name__),db=lambda:self.conn,
                    data_dir=directory,build_service_report_docx=None,build_simple_xlsx=None,
                    customer_reimbursement_items=lambda _:[],expense_amount=None,
                    render_invoice_pdf=None,report_attachment_path=None)
            job=self.conn.execute("select * from batch_export_jobs").fetchone()
            self.assertEqual(job['status'],'completed',job['error'])
            with ZipFile(job['stored_path']) as archive:
                bodies=[archive.read(name) for name in archive.namelist()]
                self.assertNotIn(b'zero.pdf',bodies)
                self.assertIn(b'positive.pdf',bodies)
                self.assertIn(b'manual.pdf',bodies)
                self.assertEqual(len(archive.namelist()),len(set(archive.namelist())))
                self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
