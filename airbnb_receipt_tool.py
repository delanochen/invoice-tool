"""Airbnb 收据生成工具 - Flask route registration."""
from __future__ import annotations

import io
import random
import string
from datetime import datetime, timedelta

from flask import abort, jsonify, render_template, request, send_file

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

# 税率和折扣率（从真实 Airbnb PDF 提取）
LONG_DISCOUNT_RATE = 0.07234  # 7.234%
TAX_RATE = 0.1072             # 10.72%

LANDLORD_NAMES = [
    "Megan Skaggs", "Sarah Johnson", "Mike Chen", "Emily Davis",
    "David Wilson", "Lisa Anderson", "James Brown", "Anna Martinez",
    "Chris Taylor", "Jessica White", "Ryan Lee", "Sophia Garcia",
]


def _random_confirmation_code():
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=8))


def _random_visa_last4():
    return "".join(random.choices(string.digits, k=4))


def _random_payment_time(checkin_date):
    day_before = checkin_date - timedelta(days=1)
    hour = random.randint(0, 23)
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return day_before.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _generate_receipt_pdf(form):
    location = form.get("location", "Goodyear").strip()
    guest = form.get("guest", "David Huang").strip()
    checkin_str = form.get("checkin", "")
    nights = int(form.get("nights", 6))
    beds = int(form.get("beds", 6))
    guests = int(form.get("guests", 4))

    checkin = datetime.strptime(checkin_str, "%Y-%m-%d")
    checkout = checkin + timedelta(days=nights)

    nightly_rate = round(random.uniform(87, 100), 2)
    subtotal = round(nightly_rate * nights, 2)
    long_discount = round(subtotal * LONG_DISCOUNT_RATE, 2)
    after_discount = round(subtotal - long_discount, 2)
    tax = round(after_discount * TAX_RATE, 2)
    total = round(after_discount + tax, 2)

    landlord = random.choice(LANDLORD_NAMES)
    confirm_code = _random_confirmation_code()
    visa_last4 = _random_visa_last4()
    payment_time = _random_payment_time(checkin)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    left = 50
    y = height - 50

    c.setFillColor(HexColor("#FF5A5F"))
    c.setFont("Helvetica-Bold", 24)
    c.drawString(left, y, "airbnb")
    y -= 30

    c.setFillColor(HexColor("#222222"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, location)
    y -= 22

    c.setFont("Helvetica", 12)
    c.drawString(left, y, f"{nights}-night stay in {location}")
    y -= 18

    c.setFont("Helvetica", 11)
    ci = f"{checkin.strftime('%B')} {checkin.day}, {checkin.strftime('%A')}"
    co = f"{checkout.strftime('%B')} {checkout.day}, {checkout.strftime('%A')}"
    c.drawString(left, y, f"{ci}  →  {co}")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(left, y, f"Entire guest suite/condominium · {beds} beds · {guests} guests")
    y -= 16
    c.drawString(left, y, f"Host: {landlord}")
    y -= 16
    c.drawString(left, y, f"Confirmation code: {confirm_code}")
    y -= 16
    c.drawString(left, y, f"Guest: {guest}")
    y -= 30

    c.setStrokeColor(HexColor("#DDDDDD"))
    c.line(left, y, width - 50, y)
    y -= 20

    c.setFillColor(HexColor("#222222"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(left, y, "Price details")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(left, y, f"${nightly_rate:.2f} × {nights} nights")
    c.drawRightString(width - 50, y, f"${subtotal:.2f}")
    y -= 16

    c.drawString(left, y, "Long-stay discount")
    c.setFillColor(HexColor("#008A05"))
    c.drawRightString(width - 50, y, f"-${long_discount:.2f}")
    y -= 16

    c.setFillColor(HexColor("#222222"))
    c.drawString(left, y, "Taxes")
    c.drawRightString(width - 50, y, f"${tax:.2f}")
    y -= 25

    c.setStrokeColor(HexColor("#DDDDDD"))
    c.line(left, y, width - 50, y)
    y -= 18

    c.setFont("Helvetica-Bold", 13)
    c.drawString(left, y, "Total (USD)")
    c.drawRightString(width - 50, y, f"${total:.2f}")
    y -= 30

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, "Payment")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(left, y, f"VISA ... {visa_last4}")
    y -= 16
    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#666666"))
    c.drawString(left, y, f"{payment_time.strftime('%B')} {payment_time.day}, {payment_time.year} GMT-7 {payment_time.strftime('%H:%M:%S')}")
    y -= 16
    c.setFillColor(HexColor("#222222"))
    c.drawString(left, y, "Paid amount (USD)")
    c.drawRightString(width - 50, y, f"${total:.2f}")
    y -= 30

    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor("#888888"))
    for line in [
        "Airbnb Payments, Inc. is the limited collection agent for the Host.",
        "Processing payment by: Airbnb Payments, Inc.",
        "888 Brannan Street, San Francisco, CA 94103",
    ]:
        c.drawString(left, y, line)
        y -= 11

    c.showPage()
    c.save()
    buf.seek(0)
    return buf, f"Airbnb_Receipt_{checkin.strftime('%Y%m%d')}_{confirm_code}.pdf"


def register_airbnb_receipt_routes(app, api):
    @app.get("/airbnb-receipt")
    @api["login_required"]
    def airbnb_receipt_page():
        if not api["is_internal_user"]() or not api["has_menu_permission"]("airbnb_receipt"):
            abort(403)
        return render_template("airbnb_receipt.html")

    @app.get("/airbnb-receipt/api/workorders")
    @api["login_required"]
    def airbnb_receipt_workorders():
        if not api["is_internal_user"]() or not api["has_menu_permission"]("airbnb_receipt"):
            abort(403)
        rows = api["db"]().execute(
            "SELECT so.id, so.order_number, so.client_name, "
            "MIN(sr.report_date) as first_report, MAX(sr.report_date) as last_report "
            "FROM service_orders so "
            "JOIN service_reports sr ON sr.service_order_id = so.id "
            "GROUP BY so.id, so.order_number, so.client_name "
            "ORDER BY so.id DESC LIMIT 50"
        ).fetchall()
        return jsonify([{
            "id": r["id"],
            "order_number": r["order_number"],
            "client_name": r["client_name"],
            "first_report": r["first_report"],
            "last_report": r["last_report"],
        } for r in rows])

    @app.get("/airbnb-receipt/api/workorders/<int:order_id>/workers")
    @api["login_required"]
    def airbnb_receipt_workers(order_id):
        if not api["is_internal_user"]() or not api["has_menu_permission"]("airbnb_receipt"):
            abort(403)
        order = api["db"]().execute(
            "SELECT site_address FROM service_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            return jsonify({"error": "Order not found"}), 404
        site_address = order["site_address"] or "Goodyear"
        parts = [p.strip() for p in site_address.split(",")]
        city = parts[1] if len(parts) >= 2 else parts[0]

        rows = api["db"]().execute(
            "SELECT w.user_id, u.name, sr.report_date::date as d "
            "FROM service_report_workers w "
            "JOIN service_reports sr ON sr.id = w.report_id "
            "JOIN users u ON u.id = w.user_id "
            "WHERE sr.service_order_id=? AND w.travel_mode='self_drive' "
            "AND sr.report_date NOT IN ('2026-09-11','2026-09-17') "
            "ORDER BY w.user_id, sr.report_date",
            (order_id,)
        ).fetchall()

        worker_dates = {}
        for r in rows:
            uid = r["user_id"]
            d = r["d"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            if uid not in worker_dates:
                worker_dates[uid] = {"name": r["name"], "dates": []}
            worker_dates[uid]["dates"].append(d)

        cutoff = datetime(2026, 9, 29).date()
        cutoff_start = datetime(2026, 9, 25).date()

        workers = []
        for uid, info in worker_dates.items():
            dates = sorted(info["dates"])
            first = dates[0]
            last = dates[-1]
            work_days = len(dates)
            date_set = set(dates)

            weekend_days = 0
            d = first
            while d <= last:
                if d.weekday() == 4 and d in date_set:
                    monday = d + timedelta(days=3)
                    if monday in date_set:
                        weekend_days += 2
                d += timedelta(days=1)

            cutoff_days = 0
            if last >= cutoff_start:
                cutoff_days = (cutoff - last).days

            nights = work_days + weekend_days + cutoff_days
            workers.append({
                "user_id": uid,
                "name": info["name"],
                "first_day": first.isoformat(),
                "last_day": last.isoformat(),
                "nights": nights,
                "work_days": work_days,
                "weekend_days": weekend_days,
                "cutoff_days": cutoff_days,
            })
        workers.sort(key=lambda x: x["user_id"])
        return jsonify({"site_address": site_address, "city": city, "workers": workers})

    @app.post("/airbnb-receipt/generate")
    @api["login_required"]
    def airbnb_receipt_generate():
        if not api["is_internal_user"]() or not api["has_menu_permission"]("airbnb_receipt"):
            abort(403)
        buf, filename = _generate_receipt_pdf(request.form)
        return send_file(
            buf, mimetype="application/pdf", as_attachment=True, download_name=filename,
        )
