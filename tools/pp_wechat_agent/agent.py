"""Prasinos Power WeChat collector with a fail-closed privacy boundary."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from wechatauto import WeChatDB

LOG = logging.getLogger("pp-wechat-agent")
STATE_DB = Path(os.environ.get("PP_WECHAT_STATE_DB", "pp_wechat_agent.db"))
SERVER_URL = os.environ.get("PP_WECHAT_SERVER_URL", "").rstrip("/")
INGEST_TOKEN = os.environ.get("PP_WECHAT_INGEST_TOKEN", "")
POLL_SECONDS = max(2, int(os.environ.get("PP_WECHAT_POLL_SECONDS", "5")))
CATALOG_SECONDS = max(60, int(os.environ.get("PP_WECHAT_CATALOG_SECONDS", "600")))
FETCH_LIMIT = max(100, min(5000, int(os.environ.get("PP_WECHAT_FETCH_LIMIT", "2000"))))


def _require_config():
    if not SERVER_URL:
        raise RuntimeError("PP_WECHAT_SERVER_URL is required")
    if not INGEST_TOKEN:
        raise RuntimeError("PP_WECHAT_INGEST_TOKEN is required")

def _api(method, path, payload=None):
    _require_config()
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "X-WeChat-Ingest-Token": INGEST_TOKEN,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(SERVER_URL + path, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"invoice-tool HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"invoice-tool unavailable: {exc.reason}") from exc


def _state():
    db = sqlite3.connect(STATE_DB)
    db.execute(
        """create table if not exists checkpoints (
               chat_username text primary key,
               last_sort_seq integer not null,
               updated_at text not null
           )"""
    )
    db.commit()
    return db

def _checkpoint(connection, chat_username):
    row = connection.execute(
        "select last_sort_seq from checkpoints where chat_username=?",
        (chat_username,),
    ).fetchone()
    return None if row is None else int(row[0])


def _set_checkpoint(connection, chat_username, sort_seq):
    connection.execute(
        """insert into checkpoints(chat_username,last_sort_seq,updated_at)
           values(?,?,?)
           on conflict(chat_username) do update
           set last_sort_seq=excluded.last_sort_seq, updated_at=excluded.updated_at""",
        (chat_username, int(sort_seq), datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def sync_group_catalog(wxdb):
    groups = []
    for item in wxdb.get_groups() or []:
        username = str(item.get("username") or "").strip()
        if not username.endswith("@chatroom"):
            continue
        groups.append({
            "chat_username": username,
            "chat_name": str(item.get("name") or username),
            "account_wxid": str(getattr(wxdb, "wxid", "") or ""),
        })
    result = _api("POST", "/api/v1/wechat/groups/catalog", {"groups": groups})

    LOG.info(
        "group catalog synced: groups=%s rejected=%s",
        len(groups),
        len(result.get("rejected") or []),
    )
    return result


def allowed_groups():
    result = _api("GET", "/api/v1/wechat/groups/allowed")
    return result.get("groups") or []


def _sender_names(wxdb, chat_username):
    names = {}
    try:
        members = wxdb.get_group_members(chat_username) or []
    except Exception:
        members = []
    for member in members:
        wxid = str(member.get("username") or "")
        if wxid:
            names[wxid] = str(
                member.get("remark") or member.get("nick_name") or wxid
            )
    return names


def _message_uid(account_wxid, chat_username, row):
    raw = "|".join([
        account_wxid,
        chat_username,

        str(row.get("shard") or ""),
        str(row.get("sort_seq") or ""),
        str(row.get("local_id") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(wxdb, group, row, sender_names):
    account_wxid = str(getattr(wxdb, "wxid", "") or "")
    chat_username = str(group["chat_username"])
    timestamp = row.get("create_time")
    if timestamp is None:
        message_time = datetime.now(timezone.utc).isoformat()
    else:
        message_time = datetime.fromtimestamp(
            int(timestamp), tz=timezone.utc
        ).isoformat()
    sender_wxid = str(row.get("sender_username") or "")
    return {
        "message_uid": _message_uid(account_wxid, chat_username, row),
        "account_wxid": account_wxid,
        "chat_username": chat_username,
        "chat_name": str(group.get("chat_name") or chat_username),
        "chat_type": "group",
        "local_id": row.get("local_id"),
        "sort_seq": row.get("sort_seq"),
        "shard": str(row.get("shard") or ""),
        "sender_wxid": sender_wxid,

        "sender_name": sender_names.get(sender_wxid, ""),
        "sender_attr": str(row.get("attr") or ""),
        "message_type": str(row.get("type") or "unknown"),
        "content": str(row.get("content") or ""),
        "media_ref": row.get("media_ref"),
        "message_time": message_time,
    }


def _send_messages(messages):
    for start in range(0, len(messages), 500):
        batch = messages[start : start + 500]
        result = _api("POST", "/api/v1/wechat/messages/batch", {"messages": batch})
        if result.get("errors") or result.get("rejected"):
            raise RuntimeError(
                "message batch was not fully accepted: "
                + json.dumps(
                    {
                        "errors": result.get("errors"),
                        "rejected": result.get("rejected"),
                    },
                    ensure_ascii=False,
                )
            )


def collect_once(wxdb, *, backfill_limit=0):

    state = _state()
    try:
        for group in allowed_groups():
            chat_username = str(group.get("chat_username") or "")
            if not chat_username.endswith("@chatroom"):
                continue
            rows = wxdb.get_messages(chat_username, limit=FETCH_LIMIT) or []
            rows = sorted(
                rows,
                key=lambda row: (
                    int(row.get("sort_seq") or 0),
                    int(row.get("local_id") or 0),
                ),
            )
            if not rows:
                continue
            max_seq = max(int(row.get("sort_seq") or 0) for row in rows)
            last_seq = _checkpoint(state, chat_username)
            if last_seq is None and backfill_limit <= 0:
                _set_checkpoint(state, chat_username, max_seq)
                LOG.info(
                    "initialized %s at current position; no history uploaded",
                    group.get("chat_name") or chat_username,
                )
                continue
            if last_seq is None:
                selected = rows[-backfill_limit:] if backfill_limit else []
            else:
                selected = [
                    row for row in rows
                    if int(row.get("sort_seq") or 0) > last_seq
                ]

            if not selected:
                continue
            sender_names = _sender_names(wxdb, chat_username)
            messages = [
                _normalize(wxdb, group, row, sender_names) for row in selected
            ]
            _send_messages(messages)
            _set_checkpoint(
                state,
                chat_username,
                max(int(row.get("sort_seq") or 0) for row in selected),
            )
            LOG.info(
                "uploaded %s messages from %s",
                len(messages),
                group.get("chat_name") or chat_username,
            )
    finally:
        state.close()


def daemon(wxdb):
    next_catalog = 0.0
    while True:
        now = time.monotonic()
        try:
            if now >= next_catalog:
                sync_group_catalog(wxdb)
                next_catalog = now + CATALOG_SECONDS
            collect_once(wxdb)
        except KeyboardInterrupt:
            raise

        except Exception:
            LOG.exception("collector cycle failed; raw WeChat data remains local")
        time.sleep(POLL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="PP WeChat -> invoice-tool collector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync-groups", help="sync group IDs/names only; no message content")
    sub.add_parser("collect-once", help="collect new messages from enabled groups")
    backfill = sub.add_parser(
        "backfill",
        help="explicitly import recent history from enabled groups",
    )
    backfill.add_argument("--limit", type=int, default=200)
    sub.add_parser("daemon", help="continuously sync catalog and collect groups")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("PP_WECHAT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    wxdb = WeChatDB()
    if args.command == "sync-groups":
        sync_group_catalog(wxdb)
    elif args.command == "collect-once":
        collect_once(wxdb)

    elif args.command == "backfill":
        collect_once(
            wxdb,
            backfill_limit=max(1, min(args.limit, FETCH_LIMIT)),
        )
    elif args.command == "daemon":
        daemon(wxdb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
