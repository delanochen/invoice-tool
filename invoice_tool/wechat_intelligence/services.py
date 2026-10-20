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

    def _allowed_work_group(self, connection, chat_username):
        # Privacy boundary: direct/private chats are never accepted. A work group must
        # be explicitly enabled in invoice-tool before any message can be stored.
        if not chat_username.endswith("@chatroom"):
            return False
        return connection.execute(
            """select 1 from wechat_group_project_mappings
               where chat_username = ? and enabled = true limit 1""",
            (chat_username,),
        ).fetchone() is not None

    def ingest_group_catalog(self, groups):
        """Store privacy-safe group metadata only; never store private-chat entries."""
        connection = self.db()
        accepted, rejected, errors = [], [], []
        timestamp = self._now()
        for index, item in enumerate(groups):
            try:
                chat_username = str(item.get("chat_username") or "").strip()
                if not chat_username.endswith("@chatroom"):
                    rejected.append({"index": index, "reason": "not_group_chat"})
                    continue
                chat_name = str(item.get("chat_name") or "").strip()[:255]
                account_wxid = str(item.get("account_wxid") or "").strip()[:255]
                existing = connection.execute(
                    "select id from wechat_group_catalog where chat_username = ?",
                    (chat_username,),
                ).fetchone()
                if existing:
                    connection.execute(
                        """update wechat_group_catalog
                           set chat_name=?, account_wxid=?, last_seen_at=?, updated_at=?
                           where chat_username=?""",
                        (chat_name, account_wxid, timestamp, timestamp, chat_username),
                    )
                else:
                    connection.execute(
                        """insert into wechat_group_catalog
                           (chat_username, chat_name, account_wxid, first_seen_at, last_seen_at, updated_at)
                           values (?, ?, ?, ?, ?, ?)""",
                        (chat_username, chat_name, account_wxid, timestamp, timestamp, timestamp),
                    )
                accepted.append(chat_username)
            except Exception as exc:
                errors.append({"index": index, "error": str(exc)})
        if accepted:
            connection.commit()
        return {"accepted": accepted, "rejected": rejected, "errors": errors}

    def list_group_sources(self):
        return self.db().execute(
            """select c.chat_username, c.chat_name, c.account_wxid, c.last_seen_at,
                      m.service_order_id, m.project_name, coalesce(m.enabled, false) as enabled
               from wechat_group_catalog c
               left join wechat_group_project_mappings m on m.chat_username = c.chat_username
               order by coalesce(m.enabled, false) desc, lower(c.chat_name), c.chat_username"""
        ).fetchall()

    def list_service_orders(self):
        return self.db().execute(
            """select id, order_number, client_order_number, client_name, status
               from service_orders
               order by id desc
               limit 500"""
        ).fetchall()

    def save_group_source(self, chat_username, *, enabled, service_order_id=None, project_name=""):
        chat_username = str(chat_username or "").strip()
        if not chat_username.endswith("@chatroom"):
            raise ValueError("Only WeChat group chats can be enabled")
        db = self.db()
        catalog = db.execute(
            "select chat_name from wechat_group_catalog where chat_username=?",
            (chat_username,),
        ).fetchone()
        if not catalog:
            raise ValueError("Unknown WeChat group; sync the group catalog first")

        timestamp = self._now()
        existing = db.execute(
            "select id from wechat_group_project_mappings where chat_username=?",
            (chat_username,),
        ).fetchone()
        chat_name = str(catalog["chat_name"] or "")
        project_name = str(project_name or "").strip()[:255]
        if existing:
            db.execute(
                """update wechat_group_project_mappings
                   set chat_name=?, service_order_id=?, project_name=?, mapping_source='manual',
                       confidence=1.0, enabled=?, updated_at=?
                   where chat_username=?""",
                (
                    chat_name,
                    service_order_id,
                    project_name,
                    bool(enabled),
                    timestamp,
                    chat_username,
                ),
            )
        else:
            db.execute(
                """insert into wechat_group_project_mappings
                   (chat_username, chat_name, service_order_id, project_name, mapping_source,
                    confidence, enabled, created_at, updated_at)
                   values (?, ?, ?, ?, 'manual', 1.0, ?, ?, ?)""",
                (
                    chat_username,
                    chat_name,
                    service_order_id,
                    project_name,
                    bool(enabled),
                    timestamp,
                    timestamp,
                ),
            )
        db.commit()

    def ingest_batch(self, messages):
        accepted, duplicate, rejected, errors = [], [], [], []
        connection = self.db()
        for index, item in enumerate(messages):
            try:
                required = ("account_wxid", "chat_username", "message_time")
                missing = [key for key in required if not str(item.get(key) or "").strip()]
                if missing:
                    raise ValueError("missing required fields: " + ", ".join(missing))
                chat_username = str(item["chat_username"]).strip()
                if not self._allowed_work_group(connection, chat_username):
                    # Do not persist payload/content for rejected chats. Only return the
                    # batch index so private chat metadata is not echoed to logs/clients.
                    rejected.append({"index": index, "reason": "chat_not_allowed"})
                    continue
                uid = self.message_uid(item)
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
        return {
            "accepted": accepted,
            "duplicate": duplicate,
            "rejected": rejected,
            "errors": errors,
        }
