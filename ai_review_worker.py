"""Expense AI review nightly batch worker.

Runs as a separate process/container (docker-compose ai-review-worker), same
pattern as ai_interpret_worker.py. Run time and switch come from the settings
table (ai_review_time, default 03:00; ai_review_enabled, default true; env
AI_REVIEW_RUN_TIME overrides the time). Batch covers submitted expenses with no
usable AI review record (missing or pending); done records are kept and failed
records are not retried automatically.
"""
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

import ai_review
from database import PostgreSQLConnection, postgres_enabled

POLL_SECONDS = max(30, int(os.environ.get("AI_REVIEW_POLL_SECONDS", "300")))
BATCH_LIMIT = max(1, int(os.environ.get("AI_REVIEW_BATCH_LIMIT", "100")))
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
LAST_RUN_KEY = "ai_review_last_run_date"


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def open_connection():
    if postgres_enabled():
        return PostgreSQLConnection()
    connection = sqlite3.connect(os.path.join(DATA_DIR, "invoices.db"))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    return connection


def setting_value(connection, key, default=""):
    row = connection.execute("select value from settings where key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else default


def enabled(connection):
    return setting_value(connection, "ai_review_enabled", "true") != "false"


def run_time(connection):
    raw = os.environ.get("AI_REVIEW_RUN_TIME", "") or setting_value(connection, "ai_review_time", "03:00")
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(raw).strip())
    if not match:
        return 3, 0
    return int(match.group(1)), int(match.group(2))


def seconds_until(hour, minute, now=None):
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def already_ran_today(connection):
    today = datetime.now().strftime("%Y-%m-%d")
    return setting_value(connection, LAST_RUN_KEY) == today


def mark_ran_today(connection):
    today = datetime.now().strftime("%Y-%m-%d")
    connection.execute(
        """
        insert into settings (key, value) values (?, ?)
        on conflict(key) do update set value = excluded.value
        """,
        (LAST_RUN_KEY, today),
    )
    connection.commit()


def run_batch(connection):
    ids = ai_review.pending_review_expense_ids(connection, limit=BATCH_LIMIT)
    log(f"pending expense reviews: {len(ids)}")
    done = failed = 0
    for expense_id in ids:
        result = ai_review.run_expense_ai_review(
            connection, expense_id, os.path.join(DATA_DIR, "expense-attachments")
        )
        connection.commit()
        if result["ok"]:
            done += 1
        else:
            failed += 1
        log(
            f"expense {expense_id}: {result['status']}"
            + (f" / conclusion {result['conclusion']}" if result["ok"] else " - " + result["error"][:120])
        )
    log(f"batch finished: ok {done}, failed {failed}")


def main():
    log("expense AI review nightly worker started (time/switch from settings, default 03:00)")
    while True:
        try:
            connection = open_connection()
            if not enabled(connection):
                connection.close()
                time.sleep(POLL_SECONDS)
                continue
            if already_ran_today(connection):
                connection.close()
                time.sleep(POLL_SECONDS)
                continue
            hour, minute = run_time(connection)
            wait = seconds_until(hour, minute)
            if wait > 60:
                connection.close()
                time.sleep(min(wait - 30, 900))
                continue
            log("run time reached, starting batch review")
            run_batch(connection)
            mark_ran_today(connection)
            connection.close()
        except KeyboardInterrupt:
            log("exit signal received, stopping")
            return
        except Exception as error:
            log(f"batch review error (retry next poll): {type(error).__name__}: {error}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
