"""Turn project-layout quote lines into invoice items."""

from __future__ import annotations

from decimal import Decimal


def quote_line_description(line):
    """Scope of work, plus a short note when one is present."""
    scope = str(line.get("scope_of_work") or "").strip()
    notes = str(line.get("notes") or "").strip()
    if notes and len(notes) <= 40:
        if scope and notes not in scope:
            return f"{scope}；{notes}"
        return notes or scope
    return scope or str(line.get("section_name") or "").strip()


def project_quote_invoice_items(lines, invoice_projects, technical_services=None):
    """One invoice row per quote line.

    Resolution order: explicit map, an invoice item with the same name, then
    Technical Services. Lines that still cannot be resolved are listed in the
    error so billing does not silently drop them.
    """
    by_id = {}
    by_name = {}
    for project in invoice_projects or []:
        by_id[project["id"]] = project
        key = str(project.get("name") or "").strip().lower()
        if key and key not in by_name:
            by_name[key] = project
    unresolved = []
    items = []
    for line in lines or []:
        amount = Decimal(str(line.get("line_total") or 0)).quantize(Decimal("0.01"))
        if amount <= 0:
            continue
        project = None
        mapped_id = line.get("invoice_project_id")
        if mapped_id in by_id:
            project = by_id[mapped_id]
        if project is None:
            project = by_name.get(str(line.get("section_name") or "").strip().lower())
        if project is None:
            project = technical_services
        if project is None:
            unresolved.append(str(line.get("section_name") or "未命名行"))
            continue
        items.append({
            "project_id": project["id"],
            "amount": float(amount),
            "tax_rate": project.get("tax_rate") or 0,
            "description": quote_line_description(line),
            "mapping": "map" if mapped_id and project.get("id") == mapped_id else (
                "name" if project is not technical_services else "technical_services"
            ),
        })
    if unresolved:
        raise ValueError(
            "以下报价行没有映射到发票项目，也没有同名发票项目或 Technical Services："
            + "、".join(unresolved)
        )
    if not items:
        raise ValueError("项目报价没有可开票的金额。")
    return items
