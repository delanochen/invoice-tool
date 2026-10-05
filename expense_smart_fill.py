"""Pre-create AI workflow for employee expense claims."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import date
from pathlib import Path

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from ai_interpretation import analyze_expense_file

ALLOWED = {"pdf", "png", "jpg", "jpeg", "webp", "gif"}


def _json_write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _number(value):
    try:
        result = round(float(value), 2)
        return result if result > 0 else 0
    except (TypeError, ValueError):
        return 0


def register_expense_smart_fill_routes(app, deps):
    db = deps["db"]
    login_required = deps["login_required"]
    can_create_expense = deps["can_create_expense"]
    require_service_order = deps["require_service_order"]
    require_service_order_start_date = deps["require_service_order_start_date"]
    expense_beneficiary_options = deps["expense_beneficiary_options"]
    next_expense_number = deps["next_expense_number"]
    now = deps["now"]
    log_action = deps["log_action"]
    attachments_root = Path(deps["EXPENSE_ATTACHMENTS_DIR"])
    draft_root = attachments_root / "_smart-fill"

    def draft_path(token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token or ""):
            abort(404)
        return draft_root / token

    def load_manifest(token):
        folder = draft_path(token)
        path = folder / "manifest.json"
        if not path.is_file():
            abort(404)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("user_id") != g.user["id"]:
            abort(403)
        return folder, manifest

    def projects():
        return db().execute(
            "select * from projects where project_type='expense' and is_active=1 order by name"
        ).fetchall()

    @app.route("/service-orders/<int:order_id>/expenses/smart-fill", methods=["GET", "POST"])
    @login_required
    def expense_smart_fill(order_id):
        if not can_create_expense():
            abort(403)
        order = require_service_order(order_id)
        start_date_redirect = require_service_order_start_date(order)
        if start_date_redirect:
            return start_date_redirect
        available_projects = projects()
        if not available_projects:
            flash("请先创建员工报销项目。", "error")
            return redirect(url_for("service_order_detail", order_id=order_id))
        if request.method == "POST":
            raw_beneficiary = request.form.get("beneficiary_id", "")
            valid_people = {str(row["id"]): row for row in expense_beneficiary_options()}
            if raw_beneficiary not in valid_people:
                flash("请选择有效的报销归属员工。", "error")
                return redirect(request.url)
            uploads = [item for item in request.files.getlist("attachments") if item and item.filename]
            if not uploads:
                flash("请至少上传一个附件。", "error")
                return redirect(request.url)
            token = secrets.token_urlsafe(24)
            folder = draft_path(token)
            folder.mkdir(parents=True, exist_ok=False)
            files = []
            try:
                for index, upload in enumerate(uploads):
                    original = os.path.basename(upload.filename).strip()
                    extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
                    if extension not in ALLOWED:
                        raise ValueError("智能填报附件仅支持 PDF 和图片。")
                    stored = f"{index + 1:03d}-{secrets.token_hex(8)}.{extension}"
                    upload.save(folder / stored)
                    files.append({
                        "id": index + 1, "original_filename": original,
                        "stored_filename": stored, "content_type": upload.content_type or "",
                        "status": "pending", "project_id": "", "amount": "",
                        "expense_date": date.today().isoformat(), "description": "",
                        "fuel_vehicle_type": "", "error": "",
                    })
                manifest = {
                    "token": token, "user_id": g.user["id"], "order_id": order_id,
                    "beneficiary_id": int(raw_beneficiary), "created_at": now(), "files": files,
                }
                _json_write(folder / "manifest.json", manifest)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            return redirect(url_for("expense_smart_fill_review", token=token))
        return render_template(
            "expense_smart_fill.html", order=order, beneficiaries=expense_beneficiary_options(),
            projects=available_projects, stage="upload", manifest=None,
        )

    @app.get("/expenses/smart-fill/<token>")
    @login_required
    def expense_smart_fill_review(token):
        _folder, manifest = load_manifest(token)
        order = require_service_order(manifest["order_id"])
        return render_template(
            "expense_smart_fill.html", order=order, beneficiaries=expense_beneficiary_options(),
            projects=projects(), stage="review", manifest=manifest,
        )

    @app.post("/expenses/smart-fill/<token>/parse")
    @login_required
    def expense_smart_fill_parse(token):
        folder, manifest = load_manifest(token)
        available = projects()
        by_name = {row["name"].strip().casefold(): row for row in available}
        for item in manifest["files"]:
            try:
                result = analyze_expense_file(
                    db(), str(folder / item["stored_filename"]), item["original_filename"],
                    item["content_type"], [row["name"] for row in available],
                )
                project = by_name.get(str(result.get("project_name") or "").strip().casefold())
                amount = _number(result.get("amount"))
                confidence = _number(result.get("confidence"))
                if not project or not amount:
                    raise RuntimeError("无法可靠识别发票类别或金额，请人工干预。")
                item.update({
                    "status": "done", "project_id": str(project["id"]), "amount": f"{amount:.2f}",
                    "expense_date": str(result.get("expense_date") or date.today().isoformat()),
                    "description": str(result.get("description") or item["original_filename"])[:500],
                    "is_fuel": bool(result.get("is_fuel")), "confidence": confidence, "error": "",
                })
            except Exception as error:
                item.update({"status": "needs_manual", "error": str(error)[:300]})
        _json_write(folder / "manifest.json", manifest)
        return jsonify({"ok": True, "redirect": url_for("expense_smart_fill_review", token=token)})

    @app.post("/expenses/smart-fill/<token>/generate")
    @login_required
    def expense_smart_fill_generate(token):
        folder, manifest = load_manifest(token)
        order = require_service_order(manifest["order_id"])
        available = {str(row["id"]): row for row in projects()}
        mode = request.form.get("generation_mode", "single")
        rows = []
        for item in manifest["files"]:
            key = str(item["id"])
            project = available.get(request.form.get(f"project_id_{key}", ""))
            amount = _number(request.form.get(f"amount_{key}"))
            expense_date = request.form.get(f"expense_date_{key}", "").strip()
            description = request.form.get(f"description_{key}", "").strip()
            fuel = request.form.get(f"fuel_vehicle_type_{key}", "").strip()
            if not project or not amount or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expense_date):
                flash(f"附件 {item['original_filename']} 仍需人工补全类别、金额和日期。", "error")
                return redirect(url_for("expense_smart_fill_review", token=token))
            is_fuel = deps["is_fuel_project_name"](project["name"])
            if is_fuel and fuel not in {"personal", "rental"}:
                flash(f"附件 {item['original_filename']} 是加油费，请选择自驾油费或租车油费。", "error")
                return redirect(url_for("expense_smart_fill_review", token=token))
            rows.append({"item": item, "project": project, "amount": amount, "date": expense_date,
                         "description": description or item["original_filename"], "fuel": fuel if is_fuel else None})
        groups = [rows]
        if mode == "category" and len(rows) > 1:
            grouped = {}
            for row in rows:
                grouped.setdefault(str(row["project"]["id"]), []).append(row)
            groups = list(grouped.values())
        created = []
        moved_paths = []
        try:
            for group in groups:
                expense_number = next_expense_number()
                total = sum(row["amount"] for row in group)
                names = ", ".join(dict.fromkeys(row["project"]["name"] for row in group))
                descriptions = "；".join(row["description"] for row in group)
                cursor = db().execute(
                    """insert into expenses (service_order_id, expense_number, project_id, project, expense_date,
                    amount, currency, description, status, created_by, created_at, updated_at, beneficiary_id, business_purpose)
                    values (?, ?, ?, ?, ?, ?, 'USD', ?, 'draft', ?, ?, ?, ?, ?)""",
                    (order["id"], expense_number, group[0]["project"]["id"], names,
                     min(row["date"] for row in group), total, descriptions, g.user["id"], now(), now(),
                     manifest["beneficiary_id"], descriptions),
                )
                expense_id = cursor.lastrowid
                target = attachments_root / str(expense_id)
                target.mkdir(parents=True, exist_ok=True)
                for sort_order, row in enumerate(group):
                    line_key = f"smart-{secrets.token_hex(8)}"
                    db().execute(
                        """insert into expense_items (expense_id,line_key,project_id,project,amount,description,fuel_vehicle_type,sort_order)
                        values (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (expense_id, line_key, row["project"]["id"], row["project"]["name"], row["amount"],
                         row["description"], row["fuel"], sort_order),
                    )
                    source = folder / row["item"]["stored_filename"]
                    stored = f"{secrets.token_hex(12)}.{source.suffix.lstrip('.')}"
                    destination = target / stored
                    shutil.copy2(source, destination)
                    moved_paths.append(destination)
                    sha, dhash = deps["expense_attachment_fingerprints"](destination)
                    db().execute(
                        """insert into expense_attachments (expense_id,expense_item_key,original_filename,stored_filename,
                        content_type,uploaded_by,uploaded_at,file_sha256,image_dhash) values (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (expense_id, line_key, row["item"]["original_filename"], stored,
                         row["item"]["content_type"], g.user["id"], now(), sha, dhash),
                    )
                log_action("create", "expense", expense_id, expense_number, "智能填报生成草稿")
                created.append(expense_id)
            db().commit()
        except Exception:
            db().rollback()
            for path in moved_paths:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        shutil.rmtree(folder, ignore_errors=True)
        flash(f"智能填报已生成 {len(created)} 张报销草稿，请核对后提交审核。", "success")
        return redirect(url_for("edit_expense", expense_id=created[0]))

    return {"expense_smart_fill": expense_smart_fill}
