"""WhatsApp identity binding and webhook persistence services."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone


class WhatsAppService:
    BIND_TTL_MINUTES = 10

    def __init__(self, db, now=None):
        self.db = db
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _hash_code(code):
        return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()

    def create_binding_code(self, user_id, created_by=None):
        code = f"PP-{secrets.randbelow(1000000):06d}"
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(minutes=self.BIND_TTL_MINUTES)).isoformat()
        db = self.db()
        db.execute(
            "delete from whatsapp_binding_codes where user_id = ? and consumed_at is null",
            (user_id,),
        )
        db.execute(
            """insert into whatsapp_binding_codes
               (user_id, code_hash, expires_at, created_at, created_by)
               values (?, ?, ?, ?, ?)""",
            (user_id, self._hash_code(code), expires_at, now_dt.isoformat(), created_by),
        )
        db.commit()
        return {"code": code, "expires_at": expires_at}

    def binding_for_user(self, user_id):
        return self.db().execute(
            """select id, user_id, wa_id, phone_number, display_name, verified_at, enabled
               from whatsapp_employee_identities where user_id = ?""",
            (user_id,),
        ).fetchone()

    def unbind(self, user_id):
        db = self.db()
        db.execute("delete from whatsapp_employee_identities where user_id = ?", (user_id,))
        db.execute("delete from whatsapp_binding_codes where user_id = ?", (user_id,))
        db.commit()

    def consume_binding_code(self, code, wa_id, phone_number=None, display_name=None):
        now_dt = datetime.now(timezone.utc)
        db = self.db()
        row = db.execute(
            """select * from whatsapp_binding_codes
               where code_hash = ? and consumed_at is null
               order by id desc limit 1""",
            (self._hash_code(code),),
        ).fetchone()
        if not row:
            return None
        try:
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if expires <= now_dt:
            return None
        conflict = db.execute(
            "select user_id from whatsapp_employee_identities where wa_id = ? and user_id != ?",
            (wa_id, row["user_id"]),
        ).fetchone()
        if conflict:
            return None
        timestamp = now_dt.isoformat()
        existing = db.execute(
            "select id from whatsapp_employee_identities where user_id = ?",
            (row["user_id"],),
        ).fetchone()
        if existing:
            db.execute(
                """update whatsapp_employee_identities
                   set wa_id=?, phone_number=?, display_name=?, verified_at=?, enabled=1, updated_at=?
                   where user_id=?""",
                (wa_id, phone_number, display_name, timestamp, timestamp, row["user_id"]),
            )
        else:
            db.execute(
                """insert into whatsapp_employee_identities
                   (user_id, wa_id, phone_number, display_name, verified_at, enabled, created_at, updated_at)
                   values (?, ?, ?, ?, ?, 1, ?, ?)""",
                (row["user_id"], wa_id, phone_number, display_name, timestamp, timestamp, timestamp),
            )
        db.execute("update whatsapp_binding_codes set consumed_at=? where id=?", (timestamp, row["id"]))
        db.commit()
        return row["user_id"]

    def user_for_wa_id(self, wa_id):
        return self.db().execute(
            """select users.* from whatsapp_employee_identities wi
               join users on users.id = wi.user_id
               where wi.wa_id = ? and wi.enabled = 1 and users.is_active = 1""",
            (wa_id,),
        ).fetchone()

    def record_inbound(self, *, meta_message_id, wa_id, message_type, content, media_id, message_time, payload):
        db = self.db()
        existing = None
        if meta_message_id:
            existing = db.execute("select id from whatsapp_messages where meta_message_id=?", (meta_message_id,)).fetchone()
        if existing:
            return False
        db.execute(
            """insert into whatsapp_messages
               (meta_message_id, wa_id, direction, message_type, content, media_id, raw_payload, message_time, created_at)
               values (?, ?, 'inbound', ?, ?, ?, ?, ?, ?)""",
            (meta_message_id, wa_id, message_type, content, media_id, json.dumps(payload, ensure_ascii=False), message_time, self.now()),
        )
        db.commit()
        return True
