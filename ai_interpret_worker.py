"""报销附件智能解读后台 worker：每天凌晨 2 点解读所有未解读过的附件。

与 photo_worker 同模式：作为独立进程/容器运行（docker-compose 里的
ai-interpret-worker）。只负责批量触发，解读逻辑在 ai_interpretation.py。
* SQLite 模式：直接打开 DATA_DIR/invoices.db。
* PostgreSQL 模式：使用 DATABASE_URL（表由 migrations/postgresql/0281-*.sql 建立，
  见 scripts/upgrade_postgresql_0281.py）。
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta

import ai_interpretation
from database import PostgreSQLConnection, postgres_enabled

RUN_HOUR = int(os.environ.get("AI_INTERPRET_RUN_HOUR", "2"))
POLL_SECONDS = max(30, int(os.environ.get("AI_INTERPRET_POLL_SECONDS", "300")))
BATCH_LIMIT = max(1, int(os.environ.get("AI_INTERPRET_BATCH_LIMIT", "200")))
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def open_connection():
    if postgres_enabled():
        return PostgreSQLConnection()
    connection = sqlite3.connect(os.path.join(DATA_DIR, "invoices.db"))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    return connection


def seconds_until_next_run(now=None):
    now = now or datetime.now()
    target = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def already_ran_today(connection):
    today = datetime.now().strftime("%Y-%m-%d")
    row = connection.execute(
        "select value from settings where key = 'ai_interpret_last_run_date'"
    ).fetchone()
    return bool(row) and str(row["value"] or "") == today


def mark_ran_today(connection):
    today = datetime.now().strftime("%Y-%m-%d")
    connection.execute(
        """
        insert into settings (key, value) values ('ai_interpret_last_run_date', ?)
        on conflict(key) do update set value = excluded.value
        """,
        (today,),
    )
    connection.commit()


def run_batch(connection):
    ids = ai_interpretation.pending_attachment_ids(connection, limit=BATCH_LIMIT)
    log(f"待解读附件 {len(ids)} 个")
    done = failed = 0
    for attachment_id in ids:
        result = ai_interpretation.interpret_attachment(
            connection, attachment_id, os.path.join(DATA_DIR, "expense-attachments")
        )
        connection.commit()
        if result["ok"]:
            done += 1
        else:
            failed += 1
        log(f"附件 {attachment_id}: {result['status']}{'' if result['ok'] else ' - ' + result['error'][:120]}")
    log(f"本轮完成：成功 {done}，失败 {failed}")


def main():
    log(f"报销附件智能解读 worker 启动（每天 {RUN_HOUR:02d}:00 运行，单批上限 {BATCH_LIMIT}）")
    while True:
        try:
            connection = open_connection()
            if already_ran_today(connection):
                connection.close()
                time.sleep(POLL_SECONDS)
                continue
            wait = seconds_until_next_run()
            if wait > 60:
                connection.close()
                time.sleep(min(wait - 30, 900))
                continue
            log("到达运行时间，开始批量解读")
            run_batch(connection)
            mark_ran_today(connection)
            connection.close()
        except KeyboardInterrupt:
            log("收到退出信号，结束")
            return
        except Exception as error:
            log(f"批量解读异常（下轮重试）：{type(error).__name__}: {error}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
