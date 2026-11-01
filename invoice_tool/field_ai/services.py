"""Read-only retrieval core for PP Field AI.

WeChat retrieval is limited to administrator-enabled work groups and is not
scoped by service-order assignment. This layer never mutates formal work-order,
billing, reimbursement, or settlement records.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


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
        # WeChat work groups are independent of service orders. Any signed-in user
        # may view groups explicitly enabled by the administrator.
        return self.db().execute(
            "select m.chat_username, m.chat_name from wechat_group_project_mappings m "
            "where m.enabled=true and m.chat_username like '%@chatroom' "
            "order by m.chat_name, m.chat_username"
        ).fetchall()

    def list_wechat(self, user, *, keyword="", group="", sender="", start="", end="", limit=50, offset=0):
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
        params.extend([max(1, min(int(limit), 100)), max(0, int(offset))])
        return self.db().execute(
            "select r.id, r.message_time, r.sender_name, r.content, "
            "coalesce(nullif(m.chat_name,''),r.chat_name) as chat_name, m.service_order_id, m.project_name "
            "from wechat_raw_messages r join wechat_group_project_mappings m on m.chat_username=r.chat_username "
            "where " + " and ".join(filters) + " order by r.message_time desc, r.id desc limit ? offset ?",
            tuple(params)).fetchall()

    @staticmethod
    def _search_term(question):
        """Extract the subject from simple identity questions without inventing context."""
        text = str(question or "").strip()
        text = re.sub(r"[?？!！。]+$", "", text).strip()
        if not text:
            return ""
        match = re.match(r"^(.+?)(?:是谁|是什么人|是哪个人)$", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.match(r"^(?:who\s+is|who's)\s+(.+)$", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text

    @classmethod
    def _search_terms(cls, question):
        """Extract useful lookup terms instead of requiring the whole question verbatim."""
        subject = cls._search_term(question)
        if not subject:
            return []
        identity_question = subject != re.sub(r"[?？!！。]+$", "", str(question or "").strip()).strip()
        if identity_question:
            return [subject]

        # Project/site/person names are often English inside a Chinese question.
        latin_terms = re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,}", subject)
        if latin_terms:
            return list(dict.fromkeys(latin_terms))[:4]

        cleaned = subject
        for phrase in (
            "请问", "帮我查一下", "帮我查", "帮我看一下", "帮我看", "今天", "今日", "最近",
            "有没有", "是否有", "是否", "有无", "发生过", "发生", "出现过", "出现",
            "谁处理过", "谁处理", "谁负责的", "谁负责", "故障", "问题", "异常", "报错",
            "场站", "项目", "情况", "怎么样", "怎么回事", "地址", "位置", "在哪里", "在哪儿", "哪儿",
            "的", "吗", "呢",
        ):
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"[?？!！。,:：，；;]+", " ", cleaned)
        candidates = [part.strip() for part in cleaned.split() if len(part.strip()) >= 2]
        return list(dict.fromkeys(candidates or [subject]))[:4]

    def search_wechat(self, user, question, limit=12):
        terms = self._search_terms(question)
        if not terms:
            return []
        clauses = []
        params = []
        for term in terms:
            like = self._like_query(term)
            clauses.append("(lower(r.content) like ? escape '\\' or lower(r.sender_name) like ? escape '\\')")
            params.extend([like, like])
        params.append(max(1, min(int(limit), 100)))
        return self.db().execute(
            "select r.id, r.message_time, r.sender_name, r.content, r.chat_username, "
            "coalesce(nullif(m.chat_name,''),r.chat_name) as chat_name, m.service_order_id, m.project_name "
            "from wechat_raw_messages r join wechat_group_project_mappings m on m.chat_username=r.chat_username "
            "where m.enabled=true and r.chat_username like '%@chatroom' and r.chat_type='group' and (" +
            " or ".join(clauses) + ") order by r.message_time desc, r.id desc limit ?",
            tuple(params),
        ).fetchall()

    @staticmethod
    def _parse_message_time(value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def expand_conversation(self, hits, *, minutes=20, per_hit=40, max_total=60):
        """Expand keyword hits into nearby messages from the same authorized group.

        This lets the answer model see follow-up replies that omit the original keyword,
        e.g. `Longbow ...` followed later by `明天就可以过去`.
        """
        if not hits:
            return []
        merged = {}
        for hit in list(hits)[:4]:
            item = dict(hit)
            chat_username = str(item.get("chat_username") or "")
            anchor = self._parse_message_time(item.get("message_time"))
            if not chat_username or anchor is None:
                merged[item.get("id")] = item
                continue
            start = (anchor - timedelta(minutes=minutes)).isoformat()
            end = (anchor + timedelta(minutes=minutes)).isoformat()
            rows = self.db().execute(
                "select r.id, r.message_time, r.sender_name, r.content, r.chat_username, "
                "coalesce(nullif(m.chat_name,''),r.chat_name) as chat_name, m.service_order_id, m.project_name "
                "from wechat_raw_messages r join wechat_group_project_mappings m on m.chat_username=r.chat_username "
                "where m.enabled=true and r.chat_username=? and r.chat_type='group' "
                "and r.message_time>=? and r.message_time<=? "
                "order by r.message_time asc, r.id asc limit ?",
                (chat_username, start, end, max(1, min(int(per_hit), 100))),
            ).fetchall()
            for row in rows:
                merged[row["id"]] = dict(row)
        ordered = sorted(
            merged.values(),
            key=lambda row: (str(row.get("message_time") or ""), int(row.get("id") or 0)),
        )
        return ordered[:max(1, min(int(max_total), 100))]

    @staticmethod
    def display_message(content):
        text = str(content or "").strip()
        lowered = text.lower()
        # WeChat media payloads may be prefixed by a sender label such as
        # "David_Huang_DR:\n<?xml ...>". Never expose the raw XML in Field AI.
        xml_pos = lowered.find("<?xml")
        msg_pos = lowered.find("<msg")
        xml_like = (
            0 <= xml_pos <= 200
            or 0 <= msg_pos <= 200
        )
        if xml_like:
            if "<img" in lowered:
                return "\U0001f5bc \u56fe\u7247"
            if "<videomsg" in lowered or "<video" in lowered:
                return "\U0001f3ac \u89c6\u9891"
            if "<voicemsg" in lowered or "<voice" in lowered:
                return "\U0001f3a4 \u8bed\u97f3"
            if "<appmsg" in lowered:
                return "\U0001f4ce \u6587\u4ef6 / \u94fe\u63a5 / \u5361\u7247"
            return "\U0001f4ce \u5fae\u4fe1\u5bcc\u5a92\u4f53\u6d88\u606f"
        return text

    def present_rows(self, rows):
        result = []
        for row in rows:
            item = dict(row)
            item["display_content"] = self.display_message(item.get("content"))
            result.append(item)
        return result

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

    @staticmethod
    def _needs_conversation_expansion(question):
        text = str(question or "").lower()
        # Expand nearby chat only when conversational context is likely to matter.
        # Simple lookup questions (address/location/contact/etc.) should surface only
        # directly matching evidence instead of dumping an unrelated time window.
        triggers = (
            "怎么", "如何", "为什么", "原因", "处理", "解决", "修", "故障", "异常", "报错",
            "下一步", "后来", "当时", "结果", "什么时候可以", "何时可以", "when can", "what happened",
        )
        return any(trigger in text for trigger in triggers)

    def gather_context(self, user, question):
        """Return permission-filtered evidence for a later LLM answer step."""
        text = str(question or "").strip()
        if not text:
            return {"wechat": [], "technical_knowledge": []}
        # Never fill a failed search with unrelated recent chat. No evidence is safer
        # than plausible-looking but irrelevant context for the answer model.
        hits = self.search_wechat(user, text)
        wechat = self.expand_conversation(hits) if self._needs_conversation_expansion(text) else hits
        return {
            "wechat": [dict(row) for row in wechat],
            "technical_knowledge": [
                dict(row) for row in self.confirmed_technical_knowledge(user, text)
            ],
        }
