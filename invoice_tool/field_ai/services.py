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
        return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    def _scope(self, user):
        allowed = self.authorized_order_ids(user)
        if allowed is None:
            return "", []
        if not allowed:
            return " and 1=0", []
        return " and m.service_order_id in (" + ",".join("?" for _ in allowed) + ")", sorted(allowed)

    def visible_groups(self, user):
        scope, params = self._scope(user)
        return self.db().execute(
            "select m.chat_username, m.chat_name from wechat_group_project_mappings m "
            "where m.enabled=true and m.chat_username like '%@chatroom'" + scope +
            " order by m.chat_name, m.chat_username", tuple(params)).fetchall()

    def list_wechat(self, user, *, keyword="", group="", sender="", start="", end="", limit=50, offset=0):
        scope, scope_params = self._scope(user)
        params = []
        filters = ["m.enabled=true", "r.chat_username like '%@chatroom'", "r.chat_type='group'"]
        for value, clause in ((keyword, "lower(r.content) like ? escape '\\'"),
                              (sender, "lower(r.sender_name) like ? escape '\\'")):
            if value:
                filters.append(clause)
                params.append(self._like_query(value))
        for value, clause in ((group, "r.chat_username=?"), (start, "r.message_time>=?"), (end, "r.message_time<?")):
            if value:
                filters.append(clause)
                params.append(value)
        params.extend(scope_params)
        params.extend([max(1, min(int(limit), 100)), max(0, int(offset))])
        return self.db().execute(
            "select r.id, r.message_time, r.sender_name, r.content, "
            "coalesce(nullif(m.chat_name,''),r.chat_name) as chat_name, m.service_order_id, m.project_name "
            "from wechat_raw_messages r join wechat_group_project_mappings m on m.chat_username=r.chat_username "
            "where " + " and ".join(filters) + scope + " order by r.message_time desc, r.id desc limit ? offset ?",
            tuple(params)).fetchall()

    def search_wechat(self, user, question, limit=12):
        return self.list_wechat(user, keyword=question, limit=limit)

    def recent_wechat(self, user, limit=20):
        return self.list_wechat(user, limit=limit)

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
