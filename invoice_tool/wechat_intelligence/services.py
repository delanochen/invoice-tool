"""Storage boundary for WeChat field evidence.

Raw messages are inserted before any LLM work. AI failures must never lose evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


class WeChatIntelligenceService:
    def __init__(self, *, db):
        self.db = db

    @staticmethod
    def message_uid(item):
        supplied = str(item.get("message_uid") or "").strip()
        if supplied:
            return supplied[:128]
        identity = "|".join(
            str(item.get(key) or "")
            for key in ("account_wxid", "chat_username", "shard", "sort_seq", "local_id")
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def ingest_batch(self, messages):
        accepted, duplicate, errors = [], [], []
        connection = self.db()
        for index, item in enumerate(messages):
            try:
                uid = self.message_uid(item)
                required = ("account_wxid", "chat_username", "message_time")
                missing = [key for key in required if not str(item.get(key) or "").strip()]
                if missing:
                    raise ValueError("missing required fields: " + ", ".join(missing))
                existing = connection.execute(
                    "select id from wechat_raw_messages where message_uid = ?", (uid,)
                ).fetchone()
                if existing:
                    duplicate.append(uid)
                    continue
                connection.execute(
                    """insert into wechat_raw_messages (
                        message_uid, account_wxid, chat_username, chat_name, chat_type,
                        local_id, sort_seq, shard, sender_wxid, sender_name, sender_attr,
                        message_type, content, media_ref, message_time, raw_payload, received_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uid, str(item["account_wxid"]), str(item["chat_username"]),
                        str(item.get("chat_name") or ""), str(item.get("chat_type") or "group"),
                        item.get("local_id"), item.get("sort_seq"), str(item.get("shard") or ""),
                        str(item.get("sender_wxid") or ""), str(item.get("sender_name") or ""),
                        str(item.get("sender_attr") or ""), str(item.get("message_type") or "text"),
                        str(item.get("content") or ""), item.get("media_ref"),
                        str(item["message_time"]), json.dumps(item, ensure_ascii=False), self._now(),
                    ),
                )
                accepted.append(uid)
            except Exception as exc:
                errors.append({"index": index, "error": str(exc)})
        if accepted:
            connection.commit()
        return {"accepted": accepted, "duplicate": duplicate, "errors": errors}
