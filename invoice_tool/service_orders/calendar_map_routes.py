"""Calendar and map endpoint registration; external clients stay in existing services."""
from flask import abort, g, jsonify, render_template, request


def register_calendar_map_routes(app, *,
    resolve_GEOCODER_VERSION,
    resolve_GEOCODING_ENABLED,
    resolve_SERVICE_ORDER_STATIC_MAP_CENTER,
    resolve_SERVICE_ORDER_STATIC_MAP_LABELED_LIMIT,
    resolve_SERVICE_ORDER_STATIC_MAP_MAX_MARKERS,
    resolve_SERVICE_ORDER_STATIC_MAP_STATUS_COLORS,
    resolve_SERVICE_ORDER_STATIC_MAP_ZOOM,
    resolve_STATIC_MAP_COUNTRIES,
    TemporaryGeocodingError,
    buyer_map_payload,
    buyer_map_rows,
    can_view_invoices,
    resolve_date,
    db,
    geocode_buyer,
    get_company_profile,
    get_google_maps_browser_api_key,
    get_google_static_maps_api_key,
    has_action_permission,
    headquarters_coordinates,
    is_external_manager,
    is_internal_user,
    login_required,
    request_country_code,
    service_order_calendar_weeks,
):
    @login_required
    def service_order_calendar():
        if not has_action_permission("service_order_calendar", "view"):
            abort(403)
        today = resolve_date().today()
        try:
            year = int(request.args.get("year", today.year))
            month = int(request.args.get("month", today.month))
            visible_month = resolve_date()(year, month, 1)
        except ValueError:
            visible_month = resolve_date()(today.year, today.month, 1)
        previous_month = resolve_date()(visible_month.year - 1, 12, 1) if visible_month.month == 1 else resolve_date()(visible_month.year, visible_month.month - 1, 1)
        next_month = resolve_date()(visible_month.year + 1, 1, 1) if visible_month.month == 12 else resolve_date()(visible_month.year, visible_month.month + 1, 1)
        return render_template(
            "service_order_calendar.html",
            visible_month=visible_month,
            previous_month=previous_month,
            next_month=next_month,
            weeks=service_order_calendar_weeks(visible_month.year, visible_month.month),
        )


    @login_required
    def service_order_map():
        if not is_internal_user() and not is_external_manager():
            abort(403)
        rows = (
            buyer_map_rows("buyers.client_id = ?", (g.user["client_id"],))
            if is_external_manager()
            else buyer_map_rows()
        )
        buyers_payload = [buyer_map_payload(row) for row in rows]
        google_maps_browser_api_key = get_google_maps_browser_api_key()
        company = get_company_profile()
        headquarters = headquarters_coordinates()
        if headquarters:
            headquarters = {
                **headquarters,
                "name": company["name"],
                "address": company["address"],
            }
        route_origin_address = (g.user["address"] or "").strip() or company["address"]
        country_code = request_country_code()
        preferred_map_mode = "static" if country_code in resolve_STATIC_MAP_COUNTRIES() else "interactive"
        return render_template(
            "service_order_map.html",
            map_buyers=buyers_payload,
            preferred_map_mode=preferred_map_mode,
            map_country_code=country_code,
            show_invoice_amounts=can_view_invoices(),
            headquarters=headquarters,
            company_address=company["address"],
            route_origin_address=route_origin_address,
            geocoding_enabled=resolve_GEOCODING_ENABLED(),
            google_maps_enabled=bool(google_maps_browser_api_key),
            google_maps_browser_api_key=google_maps_browser_api_key,
        )


    @login_required
    def service_order_map_static_image():
        """Render the currently filtered site map as a Google Static Maps image.

        The browser sends the coordinates of the sites that are already filtered
        client-side; the server draws them with Static Maps (no geocoding cost,
        coordinates are already stored) and returns a base64 data URL. This is the
        same zero-Google-in-the-browser approach as the travel tools, so the page
        works from inside China when the interactive (Google Maps JS) mode cannot.
        """
        if not is_internal_user() and not is_external_manager():
            abort(403)
        payload = request.get_json(silent=True) or {}
        raw_points = payload.get("points")
        if not isinstance(raw_points, list):
            return jsonify({"available": False, "error": "invalid_request"}), 400
        points = []
        for item in raw_points[:resolve_SERVICE_ORDER_STATIC_MAP_MAX_MARKERS()]:
            if not isinstance(item, dict):
                continue
            try:
                latitude = float(item.get("latitude"))
                longitude = float(item.get("longitude"))
            except (TypeError, ValueError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            status = str(item.get("inspection_status") or "none")
            if status not in resolve_SERVICE_ORDER_STATIC_MAP_STATUS_COLORS():
                status = "none"
            points.append((round(latitude, 6), round(longitude, 6), status))
        truncated = isinstance(raw_points, list) and len(raw_points) > resolve_SERVICE_ORDER_STATIC_MAP_MAX_MARKERS()
        if not points:
            return jsonify({"available": False, "error": "no_visible_sites"}), 400
        api_key = get_google_static_maps_api_key()
        if not api_key:
            return jsonify({"available": False, "error": "static_maps_api_not_configured"}), 503
        headquarters = headquarters_coordinates() if payload.get("include_headquarters") else None
        labeled = len(points) <= resolve_SERVICE_ORDER_STATIC_MAP_LABELED_LIMIT()
        markers = []
        site_labels = []
        if headquarters:
            markers.append(
                {
                    "color": "purple",
                    "position": f"{headquarters['latitude']},{headquarters['longitude']}",
                }
            )
        for index, (latitude, longitude, status) in enumerate(points):
            marker = {
                "color": resolve_SERVICE_ORDER_STATIC_MAP_STATUS_COLORS()[status],
                "position": f"{latitude},{longitude}",
            }
            if labeled:
                # Static Maps labels are a single character: number the first 9
                # sites 1-9, then A-Z for sites 10-35. The client renders the same
                # sequence next to the site list.
                if index < 9:
                    marker["label"] = str(index + 1)
                else:
                    marker["label"] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index - 9]
            markers.append(marker)
            site_labels.append(marker.get("label", ""))
        from travel_tools.static_maps import FlexStaticMapsService

        result = FlexStaticMapsService(api_key).get_map(
            markers,
            None,
            size="640x640",
            scale=2,
            center=resolve_SERVICE_ORDER_STATIC_MAP_CENTER(),
            zoom=resolve_SERVICE_ORDER_STATIC_MAP_ZOOM(),
        )
        if not result.success or not result.image_bytes:
            error = result.error or "static_maps_unknown_error"
            if error == "static_maps_api_not_configured":
                status_code = 503
            elif error == "static_maps_rate_limit":
                status_code = 429
            else:
                status_code = 502
            return jsonify({"available": False, "error": error}), status_code
        import base64

        encoded = base64.b64encode(result.image_bytes).decode("ascii")
        content_type = (result.content_type or "image/png").split(";")[0].strip() or "image/png"
        return jsonify(
            {
                "available": True,
                "image": f"data:{content_type};base64,{encoded}",
                "labels": site_labels,
                "count": len(points),
                "labeled": labeled,
                "truncated": truncated,
                "headquarters": bool(headquarters),
            }
        )


    @login_required
    def geocode_next_service_order():
        if not is_internal_user() and not is_external_manager():
            abort(403)
        if not resolve_GEOCODING_ENABLED():
            return jsonify({"available": False, "remaining": 0}), 503
        client_clause = "and client_id = ?" if is_external_manager() else ""
        geocode_params = [resolve_GEOCODER_VERSION()]
        if is_external_manager():
            geocode_params.append(g.user["client_id"])
        buyer = db().execute(
            f"""
            select id from buyers
            where coalesce(manual_coordinates, 0) = 0 and (
               geocode_status is null or geocode_status = 'pending'
               or geocode_address is null
               or lower(trim(geocode_address)) != lower(trim(detailed_address))
               or coalesce(geocode_version, '') != ?
            )
            {client_clause}
            order by created_at desc, id desc
            limit 1
            """,
            geocode_params,
        ).fetchone()
        if not buyer:
            return jsonify({"available": True, "remaining": 0, "buyer": None})
        try:
            geocode_buyer(buyer["id"])
        except TemporaryGeocodingError as error:
            db().rollback()
            app.logger.warning("Temporary geocoding failure for buyer %s: %s", buyer["id"], error)
            return jsonify({"available": True, "temporary_error": True}), 503
        db().commit()
        refreshed = buyer_map_rows("buyers.id = ?", (buyer["id"],))[0]
        remaining = db().execute(
            f"""
            select count(*) as count from buyers
            where coalesce(manual_coordinates, 0) = 0 and (
               geocode_status is null or geocode_status = 'pending'
               or geocode_address is null
               or lower(trim(geocode_address)) != lower(trim(detailed_address))
               or coalesce(geocode_version, '') != ?
            )
            {client_clause}
            """,
            geocode_params,
        ).fetchone()["count"]
        return jsonify(
            {
                "available": True,
                "remaining": remaining,
                "buyer": buyer_map_payload(refreshed),
            }
        )


    @login_required
    def retry_failed_service_order_geocodes():
        if not is_internal_user() and not is_external_manager():
            abort(403)
        client_clause = "and client_id = ?" if is_external_manager() else ""
        params = [g.user["client_id"]] if is_external_manager() else []
        db().execute(
            f"""
            update buyers
            set geocode_status = 'pending', geocode_attempted_at = null, geocode_version = null
            where geocode_status = 'failed' and coalesce(manual_coordinates, 0) = 0
            {client_clause}
            """
            ,
            params,
        )
        db().commit()
        return jsonify({"ok": True})


    app.add_url_rule('/service-orders/calendar', endpoint='service_order_calendar', view_func=service_order_calendar, methods=['GET'])
    app.add_url_rule('/service-orders/map', endpoint='service_order_map', view_func=service_order_map, methods=['GET'])
    app.add_url_rule('/service-orders/map/static-image', endpoint='service_order_map_static_image', view_func=service_order_map_static_image, methods=['POST'])
    app.add_url_rule('/service-orders/map/geocode-next', endpoint='geocode_next_service_order', view_func=geocode_next_service_order, methods=['POST'])
    app.add_url_rule('/service-orders/map/retry-failed', endpoint='retry_failed_service_order_geocodes', view_func=retry_failed_service_order_geocodes, methods=['POST'])
    return {'service_order_calendar': service_order_calendar, 'service_order_map': service_order_map, 'service_order_map_static_image': service_order_map_static_image, 'geocode_next_service_order': geocode_next_service_order, 'retry_failed_service_order_geocodes': retry_failed_service_order_geocodes}
