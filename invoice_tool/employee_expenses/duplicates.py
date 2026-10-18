"""Legacy-compatible employee-expense duplicates; explicit dependencies only."""

def image_hash_distance(left, right):
    if not left or not right or len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def expense_attachment_duplicate_context(attachment_id, *, db):
    return db().execute(
        """
        select ea.*, e.expense_number, e.expense_date, e.amount as expense_amount,
               e.description as expense_description,
               coalesce(e.beneficiary_id, e.created_by) as beneficiary_id,
               coalesce(ei.amount, e.amount) as matched_amount,
               coalesce(p.name, ei.project, e.project) as project_name,
               coalesce(ei.description, e.description, '') as item_description
        from expense_attachments ea
        join expenses e on e.id = ea.expense_id
        left join expense_items ei
          on ei.expense_id = ea.expense_id and ei.line_key = ea.expense_item_key
        left join projects p on p.id = ei.project_id
        where ea.id = ?
        """,
        (attachment_id,),
    ).fetchone()


def ensure_expense_attachment_fingerprints(attachment, *, db, expense_attachment_fingerprints, expense_attachment_path):
    if attachment["file_sha256"]:
        return attachment["file_sha256"], attachment["image_dhash"] or ""
    try:
        file_sha256, image_dhash = expense_attachment_fingerprints(expense_attachment_path(attachment))
    except OSError:
        return "", ""
    db().execute(
        "update expense_attachments set file_sha256 = ?, image_dhash = ? where id = ?",
        (file_sha256, image_dhash, attachment["id"]),
    )
    return file_sha256, image_dhash


def deepseek_duplicate_analysis(current, matched, reasons, *, call_deepseek_chat, deepseek_assistant_settings, json):
    settings = deepseek_assistant_settings()
    if not settings["enabled"] or not settings["api_key"]:
        return "", ""
    prompt = {
        "task": "判断两条员工报销记录是否疑似为同一张单据。只根据提供的数据判断，不要虚构单据号码。",
        "current": {
            "expense_number": current["expense_number"],
            "date": current["expense_date"],
            "amount": current["matched_amount"],
            "project": current["project_name"],
            "description": current["item_description"],
            "filename": current["original_filename"],
        },
        "matched": {
            "expense_number": matched["expense_number"],
            "date": matched["expense_date"],
            "amount": matched["matched_amount"],
            "project": matched["project_name"],
            "description": matched["item_description"],
            "filename": matched["original_filename"],
        },
        "rule_matches": reasons,
        "response": "请用中文在120字内说明风险及需要人工核对的内容。",
    }
    try:
        message = call_deepseek_chat(
            [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            settings,
            include_tools=False,
            max_tokens=220,
        )
        return str(message.get("content") or "").strip(), ""
    except RuntimeError as error:
        return "", str(error)[:500]


def run_expense_duplicate_checks(expense_id, use_deepseek=False, *, date, db, deepseek_duplicate_analysis, ensure_expense_attachment_fingerprints, expense_attachment_duplicate_context, image_hash_distance, now):
    connection = db()
    connection.execute("delete from expense_duplicate_checks where expense_id = ?", (expense_id,))
    deepseek_checks_remaining = 3
    for historical_attachment in connection.execute(
        """
        select * from expense_attachments
        where expense_id != ? and coalesce(file_sha256, '') = ''
        order by id
        """,
        (expense_id,),
    ).fetchall():
        ensure_expense_attachment_fingerprints(historical_attachment)
    attachments = connection.execute(
        "select * from expense_attachments where expense_id = ? order by id", (expense_id,)
    ).fetchall()
    for attachment in attachments:
        current = expense_attachment_duplicate_context(attachment["id"])
        current_sha, current_dhash = ensure_expense_attachment_fingerprints(current)
        candidates = connection.execute(
            """
            select ea.id
            from expense_attachments ea
            join expenses e on e.id = ea.expense_id
            left join expense_items ei
              on ei.expense_id = ea.expense_id and ei.line_key = ea.expense_item_key
            where ea.id != ?
              and (ea.expense_id != ? or ea.id < ?)
              and (
                (? != '' and ea.file_sha256 = ?)
                or (
                  abs(julianday(e.expense_date) - julianday(?)) <= 3
                  and abs(coalesce(ei.amount, e.amount) - ?) < 0.005
                )
              )
            order by e.expense_date desc, ea.id desc
            limit 30
            """,
            (attachment["id"], expense_id, attachment["id"], current_sha, current_sha,
             current["expense_date"], current["matched_amount"]),
        ).fetchall()
        for candidate in candidates:
            matched = expense_attachment_duplicate_context(candidate["id"])
            matched_sha, matched_dhash = ensure_expense_attachment_fingerprints(matched)
            reasons = []
            score = 0
            if current_sha and current_sha == matched_sha:
                reasons.append("文件内容完全相同")
                score = 100
            distance = image_hash_distance(current_dhash, matched_dhash)
            if score < 100 and distance is not None and distance <= 5:
                reasons.append("图片内容高度相似")
                score += 60 if distance else 80
            same_item = (current["expense_id"] == matched["expense_id"] and
                         (current["expense_item_key"] or "") == (matched["expense_item_key"] or ""))
            if same_item and score == 0:
                continue
            try:
                days = abs((date.fromisoformat(current["expense_date"]) - date.fromisoformat(matched["expense_date"])).days)
            except (TypeError, ValueError):
                days = 999
            if abs(float(current["matched_amount"] or 0) - float(matched["matched_amount"] or 0)) < 0.005:
                reasons.append("报销金额相同")
                score += 15
            if days <= 3:
                reasons.append(f"费用日期相距{days}天")
                score += 10
            if current["beneficiary_id"] == matched["beneficiary_id"]:
                reasons.append("报销归属员工相同")
                score += 10
            if (current["project_name"] or "").strip() == (matched["project_name"] or "").strip():
                reasons.append("费用项目相同")
                score += 5
            score = min(score, 100)
            if score < 25:
                continue
            level = "high" if score >= 70 else "medium"
            analysis, analysis_error = ("", "")
            if use_deepseek and score < 100 and deepseek_checks_remaining > 0:
                analysis, analysis_error = deepseek_duplicate_analysis(current, matched, reasons)
                deepseek_checks_remaining -= 1
            timestamp = now()
            connection.execute(
                """
                insert into expense_duplicate_checks (
                    expense_id, attachment_id, matched_expense_id, matched_attachment_id,
                    risk_level, score, reasons, deepseek_analysis, deepseek_error,
                    review_status, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (expense_id, attachment["id"], matched["expense_id"], matched["id"],
                 level, score, "；".join(reasons), analysis, analysis_error, timestamp, timestamp),
            )


def expense_duplicate_checks(expense_id, *, db):
    return db().execute(
        """
        select checks.*, matched.expense_number as matched_expense_number,
               current_attachment.original_filename as attachment_name,
               current_attachment.content_type as attachment_content_type,
               matched_attachment.original_filename as matched_attachment_name,
               matched_attachment.content_type as matched_attachment_content_type,
               reviewer.name as reviewer_name
        from expense_duplicate_checks checks
        join expenses matched on matched.id = checks.matched_expense_id
        join expense_attachments current_attachment on current_attachment.id = checks.attachment_id
        join expense_attachments matched_attachment on matched_attachment.id = checks.matched_attachment_id
        left join users reviewer on reviewer.id = checks.reviewed_by
        where checks.expense_id = ?
          and (checks.review_status != 'pending' or checks.score > 40
               or checks.expense_id != checks.matched_expense_id
               or coalesce(current_attachment.expense_item_key, '') != coalesce(matched_attachment.expense_item_key, ''))
        order by checks.score desc, checks.id desc
        """,
        (expense_id,),
    ).fetchall()
