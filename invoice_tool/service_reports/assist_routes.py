"""Report assist preview and mileage evidence endpoints; service implementations remain unchanged."""
import os
from flask import abort, g, jsonify, request, url_for


def register_report_assist_routes(app, *,
    resolve_DATA_DIR,
    resolve_DEFAULT_TRIP_TYPE,
    EmployeeResolutionService,
    GoogleRoutesService,
    GoogleStaticMapsService,
    MileageEvidenceService,
    PhotoDiscoveryService,
    PhotoMetadataService,
    resolve_SHARED_PHOTOS_DIR,
    ServiceReportAssistService,
    ServiceReportEvidenceService,
    can_create_service_report,
    resolve_datetime,
    db,
    get_google_routes_api_key,
    get_google_static_maps_api_key,
    is_external_employee,
    is_external_manager,
    log_action,
    login_required,
    now,
    require_service_order,
    require_service_report,
    save_generated_report_attachment,
    service_order_photo_folder,
    service_report_worker_options,
    service_report_workers,
):
    @login_required
    def generate_service_report_mileage_evidence(report_id):
        """按员工清单里的出发地生成（或重新生成）里程佐证图片。

        随行 / 飞机不生成；地点（出发地或目的地）或行程类型变了 -> 线路指纹变化
        -> 自动重新生成；否则复用已有佐证，`force=1` 表示用户明确要求重做。
        """
        report, order = require_service_report(report_id)
        if is_external_employee() and report["created_by"] != g.user["id"]:
            abort(403)
        if is_external_manager():
            abort(403)

        payload = request.get_json(silent=True) or {}
        force = str(payload.get("force", "")).lower() in ("1", "true", "yes")

        worker_rows = service_report_workers(report_id)
        destination = (report["site_address"] or "").strip() or (order["site_address"] or "").strip()
        existing_rows = db().execute(
            "select worker_user_id, route_fingerprint, attachment_id from service_report_mileage_evidence "
            "where report_id = ?",
            (report_id,),
        ).fetchall()
        existing_by_user = {
            int(row["worker_user_id"]): {
                "route_fingerprint": row["route_fingerprint"],
                "attachment_id": row["attachment_id"],
            }
            for row in existing_rows
        }

        workers = [
            {
                "user_id": row["id"],
                "name": row["name"],
                "travel_mode": row["travel_mode"],
                "trip_type": row["trip_type"],
                "origin": (row["origin_address"] or "").strip(),
            }
            for row in worker_rows
        ]

        routes_api_key = get_google_routes_api_key()
        static_maps_key = get_google_static_maps_api_key()
        evidence_root = os.path.join(resolve_DATA_DIR(), "service-report-mileage")
        # 同步接口：用更短的超时/重试（fail-fast）。否则每台设备最坏 ~96 秒，
        # 多台设备会超过 gunicorn/网关超时被切断连接，前端只会看到"网络错误"。
        try:
            evidence_svc = ServiceReportEvidenceService(
                # 工作日报里程佐证：采用省油路线（Google Maps 手机端绿色叶子语义）。
                # 必须与「辅助填写预览」用同一策略，否则预览里程和最终佐证里程会对不上。
                # pref 关闭时请求体与历史完全一致，其他调用方不受影响。
                routes_service=GoogleRoutesService(
                    routes_api_key, timeout=8, max_retries=1, prefer_fuel_efficient=True
                ) if routes_api_key else None,
                evidence_service=MileageEvidenceService(
                    GoogleStaticMapsService(static_maps_key, timeout=8),
                    evidence_root,
                ),
                attachment_saver=save_generated_report_attachment,
                evidence_root=evidence_root,
                uploaded_by=g.user["id"],
                now_fn=now,
            )
            summary = evidence_svc.generate(
                report_id=report_id,
                order_id=order["id"],
                report_date=report["report_date"],
                workers=workers,
                destination=destination,
                existing_by_user=existing_by_user,
                force=force,
            )

            # 只有成功生成的写指纹/附件 id，失败的保留上一次记录（避免抖一下就丢线索）
            for index, result in enumerate(summary["results"]):
                if result["outcome"] != "generated":
                    continue
                db().execute(
                    """
                    insert into service_report_mileage_evidence (
                        report_id, worker_user_id, route_fingerprint, origin_address, destination_address,
                        trip_type, distance_meters, duration_seconds, one_way_miles, reported_miles,
                        attachment_id, status, generated_by, generated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, ?)
                    on conflict(report_id, worker_user_id) do update set
                        route_fingerprint=excluded.route_fingerprint,
                        origin_address=excluded.origin_address,
                        destination_address=excluded.destination_address,
                        trip_type=excluded.trip_type,
                        distance_meters=excluded.distance_meters,
                        duration_seconds=excluded.duration_seconds,
                        one_way_miles=excluded.one_way_miles,
                        reported_miles=excluded.reported_miles,
                        attachment_id=excluded.attachment_id,
                        generated_by=excluded.generated_by,
                        generated_at=excluded.generated_at
                    """,
                    (
                        report_id, result["user_id"], result["route_fingerprint"],
                        workers[index]["origin"], destination, result.get("trip_type") or resolve_DEFAULT_TRIP_TYPE(),
                        None, None, result.get("one_way_miles"), result.get("reported_miles"),
                        result["attachment_id"], g.user["id"], now(),
                    ),
                )
            db().commit()
        except Exception:
            db().rollback()
            app.logger.exception("生成里程佐证失败：report_id=%s", report_id)
            return jsonify({
                "ok": False,
                "error": "生成里程佐证时服务器出现异常，数据没有改动。请稍后重试；如果反复出现，请联系管理员并告知操作时间。",
            }), 500
        log_action(
            "update",
            "service_report",
            report_id,
            f"{order['order_number']} / {report['report_date']}",
            f"生成里程佐证（新增 {summary['generated']}、复用 {summary['reused']}、失败 {summary['failed']}）",
        )
        return jsonify({"ok": True, **summary})


    @login_required
    def service_report_assist_plan():
        """只读预览：产出「辅助填写」建议（照片归类 / 进出场时间 / 出发地 / 里程时长）。

        不写库、不改表单。新增页没有 report_id，入参只依赖 order_id + report_date，
        结果由前端填入表单，用户点保存才真正落库。
        """
        payload = request.get_json(silent=True) or {}
        order_id = payload.get("order_id")
        if not order_id or not str(order_id).isdigit():
            return jsonify({"ok": False, "error": "缺少工单信息。"}), 400
        order = require_service_order(int(order_id))
        if not can_create_service_report(order):
            abort(403)

        report_date = (payload.get("report_date") or "").strip()
        try:
            resolve_datetime().strptime(report_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "请先在表单里选择有效的报告日期。"}), 400

        allowed_workers = {str(row["id"]): row for row in service_report_worker_options(order)}
        workers = []
        for item in payload.get("workers") or []:
            user_id = str(item.get("user_id") or "").strip()
            if user_id not in allowed_workers:
                continue  # 不在可选人员范围内的一律忽略，避免越权取地址
            workers.append(
                {
                    "user_id": int(user_id),
                    "name": allowed_workers[user_id]["name"],
                    "travel_mode": item.get("travel_mode") or "self_drive",
                    "trip_type": item.get("trip_type"),
                    "current_origin": (item.get("current_origin") or "").strip(),
                }
            )

        site_address = (payload.get("site_address") or "").strip() or (order["site_address"] or "")
        fallback_origin = (payload.get("departure_address") or "").strip()

        # field_photos.photo_type 是权威分类（arrival / departure / safety / equipment…）
        fp_map = {}
        try:
            fp_rows = db().execute("select relative_path, photo_type from field_photos").fetchall()
            fp_map = {row["relative_path"]: row["photo_type"] for row in fp_rows}
        except Exception:
            fp_map = {}

        photo_discovery = PhotoDiscoveryService(
            shared_photos_root=resolve_SHARED_PHOTOS_DIR(),
            service_order_photo_folder_func=service_order_photo_folder,
        )
        photo_metadata = PhotoMetadataService(shared_photos_root=resolve_SHARED_PHOTOS_DIR())
        routes_api_key = get_google_routes_api_key()
        emp_resolver = EmployeeResolutionService(db(), g.user["id"], g.user["name"])

        assist = ServiceReportAssistService(
            photo_discovery=photo_discovery,
            photo_metadata=photo_metadata,
            # 工作日报辅助填写预览：与里程佐证生成使用同一省油路线策略，
            # 保证预览里程 == 最终佐证里程。其他 GoogleRoutesService 调用点
            # （AI 日报草稿、出行工具等）保持默认 TRAFFIC_UNAWARE 行为不变。
            routes_service=GoogleRoutesService(routes_api_key, prefer_fuel_efficient=True) if routes_api_key else None,
            employee_address_lookup=lambda uid: emp_resolver.get_employee_default_address(uid),
            photo_url_builder=lambda relative_path: {
                "thumbnail": url_for("shared_photo_thumbnail", path=relative_path),
                "preview": url_for("shared_photo_preview", path=relative_path),
            },
        )
        plan = assist.build_plan(
            order_number=order["order_number"],
            report_date=report_date,
            workers=workers,
            destination=site_address,
            fallback_origin=fallback_origin,
            photo_type_lookup=lambda relative_path: fp_map.get(relative_path) or None,
            include_routes=bool(payload.get("include_routes", True)),
        )
        return jsonify(plan)


    app.add_url_rule('/service-reports/<int:report_id>/mileage-evidence/generate', endpoint='generate_service_report_mileage_evidence', view_func=generate_service_report_mileage_evidence, methods=['POST'])
    app.add_url_rule('/api/service-reports/assist-plan', endpoint='service_report_assist_plan', view_func=service_report_assist_plan, methods=['POST'])
    return {'generate_service_report_mileage_evidence': generate_service_report_mileage_evidence, 'service_report_assist_plan': service_report_assist_plan}
