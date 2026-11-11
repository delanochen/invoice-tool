"""发票关联结算的附件：只读引用，不复制文件。

合并开票时，发票覆盖的每份结算都带着自己的凭证。历史上创建发票会把这些凭证
`shutil.copyfile` 到发票附件目录，导致同一份证据在磁盘上存两份（一张 13 份结算
的合并发票就此产生 430 个副本 / 253 MB）。这里改为按结算读取源文件：
详情页只读展示，打包下载时直接读源路径。
"""


def invoice_settlements(invoice_id, *, db):
    """发票覆盖的结算列表（customer_reimbursements.invoice_id 指向该发票）。"""
    return db().execute(
        """
        select customer_reimbursements.*, service_orders.order_number
        from customer_reimbursements
        left join service_orders on service_orders.id = customer_reimbursements.service_order_id
        where customer_reimbursements.invoice_id = ?
        order by customer_reimbursements.id
        """,
        (invoice_id,),
    ).fetchall()


def settlement_attachment_entries(settlements, *, get_customer_reimbursement_attachments, normalized_attachment_filename, skip_names=()):
    """把每份结算的附件展开成扁平列表，跳过与发票自身附件同名的条目。

    跳过同名用于兼容历史数据：0.1.483 之前的发票已经把结算附件复制成了自己的
    附件，详情页若再列一遍会出现两行同名文件。
    """
    entries = []
    skipped = set(skip_names or ())
    for settlement in settlements:
        order_number = settlement["order_number"] if "order_number" in settlement.keys() else None
        label = settlement["file_name"] or ("结算 %s" % settlement["id"])
        # 打包目录用「工单号-结算ID」：结算的 file_name 本身是 PDF 文件名，直接当目录名
        # 会让解压出来的层级看着像文件名。具体结算单号仍在详情页「来源」列显示。
        folder = '-'.join(part for part in (order_number, "结算%s" % settlement["id"]) if part)
        for attachment in get_customer_reimbursement_attachments(settlement["id"]):
            name = attachment["original_filename"] or "附件"
            if normalized_attachment_filename(name) in skipped:
                continue
            entries.append({
                "attachment_id": attachment["id"],
                "settlement_id": settlement["id"],
                "customer_reimbursement_id": settlement["id"],
                "stored_filename": attachment["stored_filename"],
                "settlement_label": label,
                "order_number": order_number,
                "folder": folder,
                "original_filename": name,
                "content_type": attachment["content_type"],
                "uploader_name": attachment["uploader_name"],
                "uploaded_at": attachment["uploaded_at"],
            })
    return entries
