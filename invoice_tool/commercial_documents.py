"""商务单据（合同 / 报价单）父模型的数据库辅助。

本模块不 import app；所有函数接收 db（app 的请求级连接调用）与 api（globals）。
负责：
  * 确保合同/报价单对应的 commercial_documents 父行存在（迁移已回填存量）；
  * 工单统一绑定：把 contract_id / quotation_id 同步成 commercial_document_id+type；
  * 读取统一费率与自驾/结算方式，供工单结算取价。

合同结算仍以 contract_rate_versions（版本费率，历史追溯依据）为准；本模块的
commercial_document_rates 是统一结构镜像，用于「从合同/报价单复制费率」、
自驾计费取价、以及按报价单结算。员工交通工资、里程补贴、报销一律不受影响。
"""

from __future__ import annotations

from datetime import date

from invoice_tool.commercial_billing import (
    DRIVING_MODE_MILEAGE_ONLY,
    SETTLEMENT_MODE_ACTUAL,
)


def _today():
    return date.today().isoformat()


def ensure_commercial_document(db, doc_type, doc_ref_id, client_id=None,
                               currency="USD", driving_billing_mode=None,
                               settlement_mode=None, created_at=None, updated_at=None):
    """确保商务单据父行存在并返回其 id（幂等；已存在则返回，不改既有配置）。"""
    row = db.execute(
        "select id from commercial_documents where doc_type = ? and doc_ref_id = ?",
        (doc_type, doc_ref_id),
    ).fetchone()
    if row:
        return row["id"]
    now = created_at or _today()
    mode = driving_billing_mode or DRIVING_MODE_MILEAGE_ONLY
    settle = settlement_mode or SETTLEMENT_MODE_ACTUAL
    cursor = db.execute(
        """
        insert into commercial_documents (
            doc_type, doc_ref_id, client_id, currency, status,
            driving_billing_mode, settlement_mode, created_at, updated_at
        ) values (?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (doc_type, doc_ref_id, client_id, currency, mode, settle, now, updated_at or now),
    )
    return cursor.lastrowid


def update_document_config(db, doc_type, doc_ref_id, *, client_id=None, currency=None,
                           driving_billing_mode=None, settlement_mode=None, updated_at=None):
    """更新既有商务单据父行的自驾/结算方式等配置（编辑单据时同步，幂等）。"""
    fields, params = [], []
    if currency is not None:
        fields.append("currency = ?"); params.append(currency)
    if client_id is not None:
        fields.append("client_id = ?"); params.append(client_id)
    if driving_billing_mode is not None:
        fields.append("driving_billing_mode = ?"); params.append(driving_billing_mode)
    if settlement_mode is not None:
        fields.append("settlement_mode = ?"); params.append(settlement_mode)
    fields.append("updated_at = ?"); params.append(updated_at or _today())
    if not fields:
        return
    params.extend([doc_type, doc_ref_id])
    db.execute(
        f"update commercial_documents set {', '.join(fields)} where doc_type = ? and doc_ref_id = ?",
        tuple(params),
    )


def order_document_refs(db, contract_id, quotation_id):
    """按工单的合同/报价选择统一商务单据：返回 (commercial_document_id, doc_type)。

    报价单优先（一份报价单对应一个工单）；合同次之；两者皆无返回 (None, None)。
    """
    if quotation_id:
        doc_id = ensure_commercial_document(db, "quotation", quotation_id)
        return doc_id, "quotation"
    if contract_id:
        doc_id = ensure_commercial_document(db, "contract", contract_id)
        return doc_id, "contract"
    return None, None


def sync_order_commercial_document(db, order_id, contract_id, quotation_id):
    """把工单绑定同步写回 commercial_document_id / type（真实外键指向父表）。"""
    doc_id, doc_type = order_document_refs(db, contract_id, quotation_id)
    db.execute(
        "update service_orders set commercial_document_id = ?, commercial_document_type = ? where id = ?",
        (doc_id, doc_type, order_id),
    )
    return doc_id, doc_type


def document_config(db, commercial_document_id):
    """读商务单据的自驾/结算方式与货币。返回 dict 或缺省值。"""
    if not commercial_document_id:
        return {
            "driving_billing_mode": DRIVING_MODE_MILEAGE_ONLY,
            "settlement_mode": SETTLEMENT_MODE_ACTUAL,
            "currency": "USD",
            "client_id": None,
        }
    row = db.execute(
        "select * from commercial_documents where id = ?", (commercial_document_id,)
    ).fetchone()
    if not row:
        return {
            "driving_billing_mode": DRIVING_MODE_MILEAGE_ONLY,
            "settlement_mode": SETTLEMENT_MODE_ACTUAL,
            "currency": "USD",
            "client_id": None,
        }
    return dict(row)


def document_rates(db, commercial_document_id):
    """读统一费率表，返回 {rate_type: rate}。"""
    if not commercial_document_id:
        return {}
    rows = db.execute(
        "select rate_type, rate from commercial_document_rates where commercial_document_id = ?",
        (commercial_document_id,),
    ).fetchall()
    return {row["rate_type"]: float(row["rate"] or 0) for row in rows}
