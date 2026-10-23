import os
import tempfile

import settlement_revisions


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.insert = None

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("select * from customer_reimbursement_attachments"):
            return Result([self.row])
        if sql.lstrip().startswith("insert into customer_reimbursement_attachments"):
            columns = sql.split("(", 1)[1].split(")", 1)[0].replace("\n", " ").split(",")
            self.insert = {key.strip(): value for key, value in zip(columns, params)}
            return Result()
        raise AssertionError(sql)


def test_revision_attachment_snapshot_clears_unique_expense_source():
    with tempfile.TemporaryDirectory() as root:
        source_dir = os.path.join(root, "1", "attachments")
        target_dir = os.path.join(root, "2", "attachments")
        os.makedirs(source_dir)
        os.makedirs(target_dir)
        with open(os.path.join(source_dir, "old.pdf"), "wb") as stream:
            stream.write(b"test")
        row = {
            "id": 5, "customer_reimbursement_id": 1, "original_filename": "proof.pdf",
            "stored_filename": "old.pdf", "content_type": "application/pdf",
            "source_expense_attachment_id": 89, "uploaded_by": 7, "uploaded_at": "2026-10-23",
        }
        db = FakeDB(row)
        api = {
            "db": lambda: db,
            "CUSTOMER_REIMBURSEMENT_DIR": root,
            "customer_reimbursement_attachment_dir": lambda reimbursement_id: target_dir,
        }
        assert settlement_revisions._copy_attachments(api, 1, 2) == 1
        assert db.insert["customer_reimbursement_id"] == 2
        assert db.insert["source_expense_attachment_id"] is None
        assert os.path.isfile(os.path.join(target_dir, db.insert["stored_filename"]))
