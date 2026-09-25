"""Expense AI review opinion: amount consistency / project correctness / duplicate
attachments / overall reasonableness, via the local LLM (OpenAI-compatible).

Reuses ai_interpretation settings and per-attachment interpretations. Input is a
text summary (items + interpretation results + duplicate checks); images are not
sent one by one. Results are stored in expense_ai_reviews (one row per expense,
unique(expense_id)). This module NEVER mutates the expense status itself.
"""
import re
from datetime import datetime, timezone

from ai_interpretation import call_chat_completion, effective_settings, interpret_attachment

REVIEW_PROMPT = (
    "你是财务审核助理。请根据以下报销单摘要逐项核查，并输出审核意见。\n"
    "核查维度：\n"
    "1) 金额一致性：明细行金额合计 vs 各附件解读出的金额，不一致请列出差额；\n"
    "2) 项目选择正确性：附件解读出的供应商/商品类型 vs 明细行报销项目"
    "（燃油小票应选 Fuel Expenses、住宿发票应选 Accommodation/Lodging、"
    "工具耗材应选 MRO Supplies 等），判断是否选错；\n"
    "3) 附件重复：引用重复检查结果（是否命中已确认/待确认重复）；\n"
    "4) 整体合理性：币种与日期是否在工单时间窗内、金额量级、缺必要要素"
    "（发票号/供应商缺失）等。\n"
    "输出格式（严格遵守）：\n"
    "第一行：结论：通过 或 结论：存疑 或 结论：需人工复核\n"
    "随后分四段：金额核对 / 项目核对 / 重复检查 / 整体判断，每段列出具体理由与疑点。"
)

CONCLUSION_PATTERN = re.compile(r"结论\s*[:：]\s*(通过|存疑|需人工复核)")
RISK_ORDER = {"low": 1, "medium": 2, "high": 3}


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_review(connection, expense_id, model, status, conclusion, content, error):
    connection.execute(
        """
        insert into expense_ai_reviews
            (expense_id, status, conclusion, content, model, error, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(expense_id) do update set
            status = excluded.status, conclusion = excluded.conclusion, content = excluded.content,
            model = excluded.model, error = excluded.error, updated_at = excluded.updated_at
        """,
        (expense_id, status, conclusion, content, model, error, _now(), _now()),
    )


def load_review(connection, expense_id):
    return connection.execute(
        "select * from expense_ai_reviews where expense_id = ?", (expense_id,)
    ).fetchone()


def pending_review_expense_ids(connection, limit=None):
    """夜间批量 worker 的待审核清单：status='submitted' 且（无意见记录 或 记录为 pending）。

    done 保留已有意见；failed 不自动重试（避免每天重复失败），只可人工强制重跑。
    """
    sql = """
        select e.id
        from expenses e
        left join expense_ai_reviews r on r.expense_id = e.id
        where e.status = 'submitted'
          and (r.expense_id is null or r.status = 'pending')
        order by e.id
    """
    if limit:
        sql += f" limit {int(limit)}"
    return [row["id"] for row in connection.execute(sql).fetchall()]


def _collect_review_input(connection, expense_id, attachments_root):
    expense = connection.execute(
        """
        select e.*, so.order_number, so.client_name, so.start_date,
               creator.name as creator_name, beneficiary.name as beneficiary_name
        from expenses e
        left join service_orders so on so.id = e.service_order_id
        left join users creator on creator.id = e.created_by
        left join users beneficiary on beneficiary.id = coalesce(e.beneficiary_id, e.created_by)
        where e.id = ?
        """,
        (expense_id,),
    ).fetchone()
    items = connection.execute(
        """
        select ei.*, coalesce(p.name, ei.project) as project_name
        from expense_items ei
        left join projects p on p.id = ei.project_id
        where ei.expense_id = ?
        order by ei.sort_order, ei.id
        """,
        (expense_id,),
    ).fetchall()
    attachments = connection.execute(
        "select * from expense_attachments where expense_id = ? order by id",
        (expense_id,),
    ).fetchall()
    attachment_ids = [attachment["id"] for attachment in attachments]
    interpretations = {}
    if attachment_ids:
        interpretations = {
            row["attachment_id"]: row
            for row in connection.execute(
                "select * from expense_attachment_interpretations where attachment_id in "
                f"({', '.join('?' for _ in attachment_ids)})",
                attachment_ids,
            ).fetchall()
        }
    attachment_lines = []
    for attachment in attachments:
        record = interpretations.get(attachment["id"])
        if record is None or record["status"] != "done":
            result = interpret_attachment(connection, attachment["id"], attachments_root)
            if result["ok"]:
                attachment_lines.append(
                    f"- {attachment['original_filename']}：{(result['content'] or '').strip()[:800]}"
                )
            else:
                attachment_lines.append(
                    f"- {attachment['original_filename']}：该附件未能解读（{result['error'][:120]}）"
                )
            continue
        attachment_lines.append(f"- {attachment['original_filename']}：{(record['content'] or '').strip()[:800]}")
    check_lines = []
    for check in connection.execute(
        "select * from expense_duplicate_checks where expense_id = ?", (expense_id,)
    ).fetchall():
        level = (check["risk_level"] or "").lower()
        if RISK_ORDER.get(level, 0) < RISK_ORDER.get("medium", 2) and (check["score"] or 0) < 40:
            continue
        analysis = (check["deepseek_analysis"] or "").strip()[:300]
        check_lines.append(
            f"- [{check['risk_level']}/score {check['score']}] review_status={check['review_status']}"
            + (f"：{analysis}" if analysis else "")
        )
    return expense, items, attachment_lines, check_lines


def run_expense_ai_review(connection, expense_id, attachments_root, force=False):
    """生成（或复用）整张报销单的 AI 审核意见。失败记录 status=failed，可重试。"""
    existing = load_review(connection, expense_id)
    if existing and existing["status"] == "done" and not force:
        return {
            "ok": True, "status": "done", "conclusion": existing["conclusion"],
            "content": existing["content"], "model": existing["model"], "error": existing["error"],
        }
    settings = effective_settings(connection)
    try:
        expense, items, attachment_lines, check_lines = _collect_review_input(
            connection, expense_id, attachments_root
        )
        item_lines = [
            f"- 行{index + 1}：{row['project_name']} / {row['amount']} {expense['currency']}"
            f" / {expense['expense_date'] or ''} / {(row['description'] or '')[:80]}"
            for index, row in enumerate(items)
        ]
        summary = "\n".join([
            "报销单元数据：",
            f"- 单号 {expense['expense_number']}；金额 {expense['amount']} {expense['currency']}；状态 {expense['status']}",
            f"- 提交人 {expense['creator_name']}；受益人 {expense['beneficiary_name']}",
            f"- 工单 {expense['order_number'] or '-'}（{expense['client_name'] or '-'}），工单开始日期 {expense['start_date'] or '-'}",
            "",
            "明细行（项目 / 金额 / 日期 / 说明）：",
            *(item_lines or ["- （无明细行）"]),
            "",
            "附件智能解读摘要：",
            *(attachment_lines or ["- （无附件）"]),
            "",
            "重复附件检查（仅列出 medium 及以上或 score>=40 的疑点）：",
            *(check_lines or ["- （无命中疑点）"]),
        ])
        messages = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": summary},
        ]
        content = call_chat_completion(settings, messages)
    except RuntimeError as error:
        message = str(error)
        save_review(connection, expense_id, settings["model"], "failed", "", "", message)
        return {"ok": False, "status": "failed", "conclusion": "", "content": "", "model": settings["model"], "error": message}
    match = CONCLUSION_PATTERN.search(content)
    conclusion = match.group(1) if match else "需人工复核"
    save_review(connection, expense_id, settings["model"], "done", conclusion, content.strip(), "")
    return {"ok": True, "status": "done", "conclusion": conclusion, "content": content.strip(), "model": settings["model"], "error": ""}
