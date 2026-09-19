import sqlite3
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-delete-sync")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")

import app as app_module


class AiDailyReportDeleteSyncTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            create table service_reports (id integer primary key);
            create table ai_daily_report_drafts (
                id integer primary key,
                status text not null,
                saved_report_id integer,
                draft_version integer not null,
                updated_at text
            );
            create table ai_daily_report_formal_commits (
                id integer primary key,
                draft_id integer not null,
                service_report_id integer
            );
            """
        )

    def tearDown(self):
        self.db.close()

    def test_deleting_formal_report_reopens_linked_draft(self):
        self.db.execute("insert into service_reports values (41)")
        self.db.execute("insert into ai_daily_report_drafts values (7, 'saved', 41, 3, 'old')")
        self.db.execute("insert into ai_daily_report_formal_commits values (1, 7, 41)")
        with patch.object(app_module, "now", return_value="2026-09-19T10:00:00-05:00"):
            count = app_module.reset_ai_drafts_for_deleted_service_report(self.db, 41)
        draft = self.db.execute(
            "select status, saved_report_id, draft_version from ai_daily_report_drafts where id = 7"
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(draft, ("draft", None, 4))
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_formal_commits").fetchone()[0], 0)

    def test_startup_repair_only_reopens_orphaned_saved_drafts(self):
        self.db.execute("insert into service_reports values (41)")
        self.db.executemany(
            "insert into ai_daily_report_drafts values (?, 'saved', ?, 1, 'old')",
            [(1, 41), (2, 99), (3, None)],
        )
        self.db.executemany(
            "insert into ai_daily_report_formal_commits values (?, ?, ?)",
            [(1, 1, 41), (2, 2, 99), (3, 3, None)],
        )
        with patch.object(app_module, "now", return_value="2026-09-19T10:00:00-05:00"):
            count = app_module.repair_orphaned_ai_daily_report_drafts(self.db)
        rows = self.db.execute(
            "select id, status, saved_report_id, draft_version from ai_daily_report_drafts order by id"
        ).fetchall()
        self.assertEqual(count, 2)
        self.assertEqual(rows[0], (1, "saved", 41, 1))
        self.assertEqual(rows[1], (2, "draft", None, 2))
        self.assertEqual(rows[2], (3, "draft", None, 2))
        remaining = self.db.execute("select draft_id from ai_daily_report_formal_commits order by draft_id").fetchall()
        self.assertEqual(remaining, [(1,)])


if __name__ == "__main__":
    unittest.main()
