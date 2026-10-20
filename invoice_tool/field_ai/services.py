"""Read-only retrieval core for PP Field AI.

Authorization is resolved before evidence is loaded. This layer never mutates
formal work-order, billing, reimbursement, or settlement records.
"""
from __future__ import annotations


class FieldAIService:
    def __init__(self, *, db):
        self.db = db

    def _assigned_order_ids(self, user_id):
        rows = self.db().execute(
            "select service_order_id from user_service_orders where user_id=?",
            (user_id,),
        ).fetchall()
        return {int(row["service_order_id"]) for row in rows}

    def authorized_order_ids(self, user):
        """Return None for unrestricted staff, otherwise the allowed order IDs."""
        role = str(user["role"] or "")
        if role in {"admin", "manager", "finance"}:
            return None
        return self._assigned_order_ids(int(user["id"]))

    @staticmethod
    def _like_query(question):
        text = str(question or "").strip().lower()
        return "%" + text.replace("%", "\\%").replace("_", "\\_") + "%"

    def search_wechat(self, user, question, limit=12):
        allowed = self.authorized_order_ids(user)
        if allowed == set():
            return []
        params = [self._like_query(question)]
        scope = ""
        if allowed is not None:
            placeholders = ",".join("?" for _ in allowed)
            scope = f" and m.service_order_id in ({placeholders})"
            params.extend(sorted(allowed))
        params.append(int(limit))
        return self.db().execute(
            f"""select r.id, r.message_time, r.sender_name, r.content,
                       r.chat_name, m.service_order_id, m.project_name
                from wechat_raw_messages r
                join wechat_group_project_mappings m
                  on m.chat_username=r.chat_username and m.enabled=true
                where lower(r.content) like ? escape '\\'
                  {scope}
                order by r.message_time desc, r.id desc
                limit ?""",
            tuple(params),
        ).fetchall()

    def recent_wechat(self, user, limit=20):
        allowed = self.authorized_order_ids(user)
        if allowed == set():
            return []
        params = []
        scope = ""
        if allowed is not None:
            placeholders = ",".join("?" for _ in allowed)
            scope = f" and m.service_order_id in ({placeholders})"
            params.extend(sorted(allowed))
        params.append(int(limit))
        return self.db().execute(
            f"""select r.id, r.message_time, r.sender_name, r.content,
                       r.chat_name, m.service_order_id, m.project_name
                from wechat_raw_messages r
                join wechat_group_project_mappings m
                  on m.chat_username=r.chat_username and m.enabled=true
                where 1=1 {scope}
                order by r.message_time desc, r.id desc
                limit ?""",
            tuple(params),
        ).fetchall()

    def confirmed_technical_knowledge(self, user, question, limit=8):
        allowed = self.authorized_order_ids(user)
        if allowed == set():
            return []
        like = self._like_query(question)
        params = [like, like, like, like]
        scope = ""
        if allowed is not None:
            placeholders = ",".join("?" for _ in allowed)
            scope = f" and service_order_id in ({placeholders})"
            params.extend(sorted(allowed))
        params.append(int(limit))
        return self.db().execute(
            f"""select id, service_order_id, project_name, equipment, component,
                       error_code, symptom, guidance, resolution, provenance_type
                from technical_knowledge_candidates
                where status='confirmed'
                  and (lower(error_code) like ? escape '\\'
                    or lower(symptom) like ? escape '\\'
                    or lower(guidance) like ? escape '\\'
                    or lower(resolution) like ? escape '\\')
                  {scope}
                order by updated_at desc, id desc
                limit ?""",
            tuple(params),
        ).fetchall()

    def gather_context(self, user, question):
        """Return permission-filtered evidence for a later LLM answer step."""
        text = str(question or "").strip()
        if not text:
            return {"wechat": [], "technical_knowledge": []}
        wechat = self.search_wechat(user, text)
        if not wechat:
            wechat = self.recent_wechat(user, limit=8)
        return {
            "wechat": [dict(row) for row in wechat],
            "technical_knowledge": [
                dict(row) for row in self.confirmed_technical_knowledge(user, text)
            ],
        }
