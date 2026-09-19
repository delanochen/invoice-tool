import os
import hashlib
import html
import csv
import json
import math
import re
import secrets
import shutil
import smtplib
import sqlite3
import tempfile
import threading
import time
import zipfile
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pypdf import PdfReader
from image_processing import compress_image
from field_work import init_field_schema, register_field_routes
from staff_reports import register_staff_reports
from customer_report_logic import (
    migrate_historical_customer_reports,
    normalize_customer_report_choice,
)
from rate_engine import contract_rate, init_rate_schema, register_rate_routes
from settlement_review import (
    init_settlement_review_schema,
    linked_approved_expense_rows,
    register_settlement_review_routes,
    selected_expense_count,
    selected_expense_item_ids,
    selected_expense_total,
)
from profitability import init_profitability_schema, register_profitability_routes
from ai_daily_report import (
    AIIntentService,
    DailyReportService,
    WorkOrderContextService,
    EmployeeResolutionService,
    TravelService,
    reconcile_travel_verification_fields,
    GoogleRoutesService,
    MileageService,
    PhotoDiscoveryService,
    PhotoMetadataService,
    generate_action_id,
    is_phase1_implemented,
    ACTION_VERSION,
    DraftVersionConflict,
    DraftStateError,
)
from ai_daily_report.schemas import WorkerTravel
from ai_daily_report.attachment_manifest import (
    AttachmentManifestService,
    ManifestError,
    ManifestIntegrityError,
    ManifestNotFoundError,
    ManifestStaleError,
    ManifestValidationBlockedError,
)
from ai_daily_report.formal_save import (
    FormalSaveService,
    FormalSaveError,
    FormalSaveIntegrityError,
    FormalSaveComplianceError,
    FormalSaveStaleError,
    FormalSaveStateError,
    FormalSaveCommitConflictError,
    FormalSaveValidationBlockedError,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "invoices.db")
ATTACHMENTS_DIR = os.path.join(DATA_DIR, "attachments")
CONTRACT_ATTACHMENTS_DIR = os.path.join(DATA_DIR, "contract-attachments")
REPORT_ATTACHMENTS_DIR = os.path.join(DATA_DIR, "service-report-attachments")
EXPENSE_ATTACHMENTS_DIR = os.path.join(DATA_DIR, "expense-attachments")
CUSTOMER_REIMBURSEMENT_DIR = os.path.join(DATA_DIR, "customer-reimbursements")
COMPANY_ATTACHMENT_DIR = os.path.join(DATA_DIR, "company-attachments")
USER_ATTACHMENT_DIR = os.path.join(DATA_DIR, "user-attachments")
KNOWLEDGE_BASE_DIR = os.path.join(DATA_DIR, "knowledge-base")
MOBILE_APP_DIR = os.path.join(DATA_DIR, "mobile-app")
IOS_IPA_PATH = os.path.join(MOBILE_APP_DIR, "PrasinosPower.ipa")
IOS_BUNDLE_ID = os.environ.get("IOS_BUNDLE_ID", "com.prasinospower.internal").strip()
IOS_APP_VERSION = os.environ.get("IOS_APP_VERSION", "1.0.0").strip()
KNOWLEDGE_MAX_PDF_BYTES = 50 * 1024 * 1024
KNOWLEDGE_MAX_EXTRACTED_TEXT = 500_000
SHARED_PHOTOS_DIR = os.environ.get("SHARED_PHOTOS_DIR", "/app/shared-photos")
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0").strip() or "0.0.0"
if APP_VERSION == "0.0.0":
    try:
        release_version = Path(BASE_DIR, "VERSION").read_text(encoding="utf-8").strip()
        if re.fullmatch(r"\d+\.\d+\.\d+", release_version):
            APP_VERSION = release_version
    except OSError:
        pass
PRODUCTION_DATA_DIRECTORY_IDENTITY = "invoice-tool-primary-volume1-20260816"
IS_RELEASE_BUILD = bool(re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)) and APP_VERSION != "0.0.0"
REQUIRE_DATA_DIRECTORY_IDENTITY = (
    os.environ.get("REQUIRE_DATA_DIRECTORY_IDENTITY", "1" if IS_RELEASE_BUILD else "0") == "1"
)
DATA_DIRECTORY_IDENTITY = os.environ.get(
    "DATA_DIRECTORY_IDENTITY",
    PRODUCTION_DATA_DIRECTORY_IDENTITY if REQUIRE_DATA_DIRECTORY_IDENTITY else "",
).strip()
DATA_IDENTITY_FILE = os.path.join(DATA_DIR, ".invoice-tool-data-id")
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "gif", "doc", "docx", "xls", "xlsx"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "heic", "heif"}
REPORT_PHOTO_FOLDERS = {
    "arrival": "ç°åœºåˆ°è¾¾æ—¶é—´ç…§ç‰‡",
    "departure": "ç¦»å¼€ç°åœºæ—¶é—´ç…§ç‰‡",
    "self_check": "è‡ªæ£€ç…§ç‰‡",
    "site": "ç°åœºæœåŠ¡ç…§ç‰‡",
    "mileage_proof": "é‡Œç¨‹ä½è¯",
}

register_heif_opener()
ALLOWED_ATTACHMENT_LABEL = "Wordã€Excelã€PDFã€PNGã€JPGã€JPEGã€WEBPã€GIF"

DEFAULT_COMPANY_PROFILE = {
    "name": os.environ.get("COMPANY_NAME", "").strip(),
    "address": os.environ.get("COMPANY_ADDRESS", "").strip(),
    "email": os.environ.get("COMPANY_EMAIL", "").strip(),
    "phone": os.environ.get("COMPANY_PHONE", "").strip(),
    "registration_number": os.environ.get("COMPANY_REGISTRATION_NUMBER", "").strip(),
    "ein": os.environ.get("COMPANY_EIN", "").strip(),
    "tax_note": os.environ.get("COMPANY_TAX_NOTE", "").strip(),
}

DEFAULT_PAYMENT_INSTRUCTIONS = {
    "method": os.environ.get("PAYMENT_METHOD", "").strip(),
    "beneficiary": os.environ.get("PAYMENT_BENEFICIARY", "").strip(),
    "bank_name": os.environ.get("PAYMENT_BANK_NAME", "").strip(),
    "account_number": os.environ.get("PAYMENT_ACCOUNT_NUMBER", "").strip(),
    "routing_number": os.environ.get("PAYMENT_ROUTING_NUMBER", "").strip(),
    "swift_bic": os.environ.get("PAYMENT_SWIFT_BIC", "").strip(),
}

DEFAULT_INVOICE_TERMS = os.environ.get("INVOICE_TERMS", "").strip()

DEFAULT_SMTP_SETTINGS = {
    "host": os.environ.get("SMTP_HOST", "").strip(),
    "port": os.environ.get("SMTP_PORT", "").strip(),
    "user": os.environ.get("SMTP_USER", "").strip(),
    "password": os.environ.get("SMTP_PASSWORD", ""),
    "from": os.environ.get("SMTP_FROM", "").strip(),
    "tls": os.environ.get("SMTP_TLS", "").strip(),
}
DEFAULT_TIMEZONE = "America/Chicago"
HEADQUARTERS_LATITUDE = os.environ.get("HEADQUARTERS_LATITUDE", "").strip()
HEADQUARTERS_LONGITUDE = os.environ.get("HEADQUARTERS_LONGITUDE", "").strip()
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
CENSUS_GEOCODER_URL = os.environ.get(
    "CENSUS_GEOCODER_URL",
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
)
GOOGLE_MAPS_BROWSER_API_KEY_ENV = os.environ.get("GOOGLE_MAPS_BROWSER_API_KEY", "").strip()
GOOGLE_GEOCODING_API_KEY_ENV = os.environ.get("GOOGLE_GEOCODING_API_KEY", "").strip()
GOOGLE_ROUTES_API_KEY_ENV = os.environ.get("GOOGLE_ROUTES_API_KEY", "").strip()
GOOGLE_STATIC_MAPS_API_KEY_ENV = os.environ.get("GOOGLE_STATIC_MAPS_API_KEY", "").strip()
DEEPSEEK_API_KEY_ENV = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_VISION_MODEL_ENV = os.environ.get("DEEPSEEK_VISION_MODEL", "deepseek-flash").strip()
VISION_EXTERNAL_API_ENABLED_ENV = os.environ.get("VISION_EXTERNAL_API_ENABLED", "false").strip().lower() == "true"
VISION_MAX_IMAGE_BYTES_ENV = int(os.environ.get("VISION_MAX_IMAGE_BYTES", "2097152"))
GOOGLE_GEOCODING_URL = os.environ.get("GOOGLE_GEOCODING_URL", "https://maps.googleapis.com/maps/api/geocode/json")
NOMINATIM_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "InvoiceTool/1.0",
)
NOMINATIM_COUNTRY_CODES = os.environ.get("NOMINATIM_COUNTRY_CODES", "us,ca").strip()
GEOCODING_ENABLED = os.environ.get("GEOCODING_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
GEOCODER_VERSION = "5"
GOOGLE_PHOTOS_ALLOWED_HOSTS = {"photos.app.goo.gl", "photos.google.com", "www.photos.google.com"}
GOOGLE_PHOTOS_MAX_IMPORT = 1000
GOOGLE_PHOTOS_MAX_IMAGE_BYTES = 30 * 1024 * 1024
_geocode_lock = threading.Lock()
_last_geocode_request_at = 0.0
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}

STATUS_LABELS = {
    "draft": "ä¿å­˜æœªæäº¤",
    "submitted": "å¾…ç»ç†å®¡æ ¸",
    "returned": "å·²é€€å›",
    "completed": "å·²å®Œæˆ",
    "void": "ä½œåºŸ",
}

EXPENSE_STATUS_LABELS = {
    "draft": "ä¿å­˜æœªæäº¤",
    "submitted": "å¾…ç»ç†å®¡æ ¸",
    "returned": "å·²é€€å›",
    "approved": "å·²é€šè¿‡",
}

EXPENSE_PAYOUT_LABELS = {
    "pending": "å¾…æŠ¥é”€",
    "paid": "å·²æŠ¥é”€",
}

CUSTOMER_REIMBURSEMENT_STATUS_LABELS = {
    "draft": "ä¿å­˜æœªæäº¤",
    "submitted": "å¾…ç»ç†å®¡æ ¸",
    "returned": "å·²é€€å›",
    "approved": "å·²é€šè¿‡",
}

ROLE_OPTIONS = {
    "admin": "ç®¡ç†å‘˜",
    "manager": "ç»ç†",
    "finance": "è´¢åŠ¡",
    "employee": "å‘˜å·¥",
    "external_manager": "å¤–éƒ¨ç®¡ç†å‘˜",
    "external_employee": "å¤–éƒ¨å‘˜å·¥",
}

MENU_PERMISSION_GROUPS = [
    {
        "label": "ä¸»èœå•",
        "items": [
            {"key": "dashboard", "label": "æ¦‚è§ˆ", "roles": {"admin", "manager", "finance"}},
            {"key": "contracts", "label": "åˆåŒ", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "service_orders", "label": "å·¥å•", "roles": set(ROLE_OPTIONS)},
            {"key": "field_work", "label": "ç°åœºå·¥ä½œ", "roles": set(ROLE_OPTIONS)},
            {"key": "service_order_map", "label": "ç«™ç‚¹åœ°å›¾", "roles": {"admin", "manager", "finance", "employee", "external_manager"}},
            {"key": "service_order_calendar", "label": "å·¥å•æ—¥å†", "roles": set(ROLE_OPTIONS)},
            {"key": "expense_processing", "label": "æŠ¥é”€å¤„ç†", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "new_invoice", "label": "æ–°å»ºå‘ç¥¨", "roles": {"manager", "finance"}},
            {"key": "messages", "label": "æ¶ˆæ¯", "roles": set(ROLE_OPTIONS)},
            {"key": "knowledge_base", "label": "çŸ¥è¯†åº“", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "ai_assistant", "label": "æ™ºèƒ½åŠ©æ‰‹", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "ai_daily_report", "label": "AI æ—¥æŠ¥", "roles": {"admin", "manager", "finance", "employee"}},
        ],
    },
    {
        "label": "æŠ¥è¡¨",
        "items": [
            {"key": "invoices", "label": "å‘ç¥¨æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "invoice_query", "label": "å‘ç¥¨æ˜ç»†æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "customer_reimbursement_query", "label": "å·¥å•ç»“ç®—æŠ¥è¡¨", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "profitability", "label": "é¡¹ç›®åˆ©æ¶¦", "roles": {"admin", "manager", "finance"}},
            {"key": "buyer_query", "label": "ç«™ç‚¹æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "service_order_query", "label": "å·¥å•æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "service_report_query", "label": "æ—¥æŠ¥æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "expense_query", "label": "æŠ¥é”€æ˜ç»†æŸ¥è¯¢", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "user_certificate_query", "label": "å‘˜å·¥è¯ä¹¦æŸ¥è¯¢", "roles": set(ROLE_OPTIONS)},
            {"key": "audit_log_report", "label": "æ“ä½œæ—¥å¿—", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "è–ªé…¬ç®¡ç†",
        "items": [
            {"key": "labor_hours_report", "label": "å·¥æ—¶ç»Ÿè®¡", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "payroll_report", "label": "è–ªé…¬ç»Ÿè®¡", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "payroll_calendar", "label": "è–ªé…¬æ—¥å†", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "employee_grades", "label": "å‘˜å·¥ç­‰çº§", "roles": {"admin", "manager", "finance"}},
            {"key": "payroll_subsidies", "label": "è¡¥è´´å‚æ•°", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "åŸºç¡€æ•°æ®",
        "items": [
            {"key": "clients", "label": "å®¢æˆ·", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "owners", "label": "ä¸šä¸»", "roles": {"admin", "manager", "finance"}},
            {"key": "manufacturers", "label": "å‚å®¶", "roles": {"admin", "manager", "finance"}},
            {"key": "buyers", "label": "ç«™ç‚¹", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "work_order_types", "label": "å·¥å•ç±»å‹", "roles": {"admin", "manager"}},
            {"key": "projects", "label": "é¡¹ç›®", "roles": {"admin", "manager"}},
            {"key": "countries", "label": "å›½å®¶", "roles": {"admin", "manager"}},
            {"key": "company_info", "label": "å…¬å¸ä¿¡æ¯", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "users", "label": "ç”¨æˆ·/æˆ‘çš„èµ„æ–™", "roles": set(ROLE_OPTIONS)},
        ],
    },
    {
        "label": "ç³»ç»Ÿé…ç½®",
        "items": [
            {"key": "payment_terms", "label": "è´¦æœŸç®¡ç†", "roles": {"admin", "finance"}},
            {"key": "system_settings", "label": "ç³»ç»Ÿè®¾ç½®", "roles": {"admin"}},
            {"key": "database_console", "label": "æ•°æ®åº“å·¥å…·", "roles": {"admin"}},
        ],
    },
]

DEFAULT_MENU_ROLES = {
    item["key"]: set(item["roles"])
    for group in MENU_PERMISSION_GROUPS
    for item in group["items"]
}

ACTION_LABELS = {
    "view": "æŸ¥çœ‹",
    "create": "æ–°å¢",
    "edit": "ç¼–è¾‘",
    "delete": "åˆ é™¤",
    "approve": "å®¡æ ¸",
    "reset": "ä¿®æ”¹çŠ¶æ€",
    "export": "å¯¼å‡º",
    "send": "å‘é€",
    "pay": "æ ¸é”€",
    "execute": "æ‰§è¡Œ",
}

ROLE_ACTION_PERMISSION_GROUPS = [
    {
        "label": "ä¸»ä¸šåŠ¡",
        "items": [
            {"key": "contracts", "label": "åˆåŒ", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "service_orders", "label": "å·¥å•", "actions": {"view": set(ROLE_OPTIONS), "create": {"manager", "finance", "employee"}, "edit": {"admin", "manager", "finance", "employee", "external_manager", "external_employee"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "service_order_calendar", "label": "å·¥å•æ—¥å†", "actions": {"view": set(ROLE_OPTIONS)}},
            {"key": "service_reports", "label": "å·¥ä½œæ—¥æŠ¥", "actions": {"view": set(ROLE_OPTIONS), "create": {"admin", "manager", "finance", "employee", "external_employee"}, "edit": {"admin", "manager", "finance", "employee", "external_employee"}, "delete": {"admin", "manager"}, "export": {"admin", "manager", "finance", "employee", "external_employee", "external_manager"}}},
            {"key": "invoices", "label": "å‘ç¥¨", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}, "export": {"admin", "manager", "finance", "external_manager"}, "send": {"manager", "finance"}, "pay": {"admin", "manager", "finance"}}},
            {"key": "expenses", "label": "å‘˜å·¥æŠ¥é”€", "actions": {"view": {"admin", "manager", "finance", "employee"}, "create": {"manager", "finance", "employee"}, "edit": {"admin", "manager", "finance", "employee"}, "delete": {"admin", "manager", "finance", "employee"}, "approve": {"manager", "finance"}}},
            {"key": "customer_reimbursements", "label": "å·¥å•ç»“ç®—", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}, "approve": {"admin", "manager"}, "reset": {"admin", "manager", "finance"}, "export": {"admin", "manager", "finance", "external_manager"}, "send": {"manager", "finance"}}},
            {"key": "profitability", "label": "é¡¹ç›®åˆ©æ¶¦", "actions": {"view": {"admin", "manager", "finance"}}},
            {"key": "knowledge_base", "label": "çŸ¥è¯†åº“", "actions": {"view": {"admin", "manager", "finance", "employee"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager"}}},
            {"key": "ai_assistant", "label": "æ™ºèƒ½åŠ©æ‰‹", "actions": {"view": {"admin", "manager", "finance", "employee"}}},
        ],
    },
    {
        "label": "åŸºç¡€æ•°æ®",
        "items": [
            {"key": "clients", "label": "å®¢æˆ·", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "owners", "label": "ä¸šä¸»", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "manufacturers", "label": "å‚å®¶", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "buyers", "label": "ç«™ç‚¹", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance", "external_manager"}, "edit": {"admin", "manager", "finance", "external_manager"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "work_order_types", "label": "å·¥å•ç±»å‹", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}, "delete": {"admin", "manager"}}},
            {"key": "projects", "label": "é¡¹ç›®", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}, "delete": {"admin", "manager"}}},
            {"key": "countries", "label": "å›½å®¶", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}}},
            {"key": "users", "label": "ç”¨æˆ·", "actions": {"view": set(ROLE_OPTIONS), "create": {"admin", "external_manager"}, "edit": {"admin", "external_manager"}, "delete": {"admin"}, "approve": {"admin", "manager"}}},
        ],
    },
    {
        "label": "è–ªé…¬ä¸ç³»ç»Ÿ",
        "items": [
            {"key": "labor_hours_report", "label": "å·¥æ—¶ç»Ÿè®¡", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "payroll_report", "label": "è–ªé…¬ç»Ÿè®¡", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "payroll_calendar", "label": "è–ªé…¬æ—¥å†", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "employee_grades", "label": "å‘˜å·¥ç­‰çº§", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "payroll_subsidies", "label": "è¡¥è´´å‚æ•°", "actions": {"view": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}}},
            {"key": "company_info", "label": "å…¬å¸ä¿¡æ¯", "actions": {"view": {"admin", "manager", "finance", "employee"}, "edit": {"admin"}}},
            {"key": "system_settings", "label": "ç³»ç»Ÿè®¾ç½®", "actions": {"view": {"admin"}, "edit": {"admin"}}},
            {"key": "database_console", "label": "æ•°æ®åº“å·¥å…·", "actions": {"view": {"admin"}, "execute": {"admin"}}},
            {"key": "payment_terms", "label": "è´¦æœŸç®¡ç†", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "audit_logs", "label": "æ“ä½œæ—¥å¿—", "actions": {"view": {"admin", "manager", "finance"}}},
        ],
    },
]

DEFAULT_ACTION_ROLES = {
    (item["key"], action): set(roles)
    for group in ROLE_ACTION_PERMISSION_GROUPS
    for item in group["items"]
    for action, roles in item["actions"].items()
}

def permission_tree_groups():
    menu_labels = {
        item["key"]: item["label"]
        for group in MENU_PERMISSION_GROUPS
        for item in group["items"]
    }
    seen_keys = set()
    groups = []
    for group in ROLE_ACTION_PERMISSION_GROUPS:
        items = []
        for item in group["items"]:
            seen_keys.add(item["key"])
            items.append(
                {
                    "key": item["key"],
                    "label": item["label"],
                    "menu_key": item["key"] if item["key"] in DEFAULT_MENU_ROLES else "",
                    "actions": list(item["actions"].keys()),
                }
            )
        groups.append({"label": group["label"], "items": items})
    menu_only_items = [
        {"key": key, "label": label, "menu_key": key, "actions": []}
        for key, label in menu_labels.items()
        if key not in seen_keys
    ]
    if menu_only_items:
        groups.insert(0, {"label": "èœå•å…¥å£", "items": menu_only_items})
    return groups

SUPPORTED_LANGUAGES = {
    "zh-CN": {"label": "ç®€ä½“ä¸­æ–‡", "flag": "ğŸ‡¨ğŸ‡³"},
    "en": {"label": "English", "flag": "ğŸ‡ºğŸ‡¸"},
    "nl": {"label": "Nederlands", "flag": "ğŸ‡³ğŸ‡±"},
    "de": {"label": "Deutsch", "flag": "ğŸ‡©ğŸ‡ª"},
    "es": {"label": "EspaÃ±ol", "flag": "ğŸ‡ªğŸ‡¸"},
}
DEFAULT_LANGUAGE = "zh-CN"

PROJECT_TYPE_LABELS = {
    "invoice": "å‘ç¥¨é¡¹ç›®",
    "expense": "å‘˜å·¥æŠ¥é”€",
    "customer_expense": "å·¥å•ç»“ç®—",
}

CONTRACT_TYPE_LABELS = {
    "framework": "æ¡†æ¶åˆåŒ",
    "project": "é¡¹ç›®åˆåŒ",
}

CONTRACT_STATUS_LABELS = {
    "draft": "è‰ç¨¿",
    "review": "å®¡æ ¸ä¸­",
    "signed": "å·²ç­¾ç½²",
    "active": "æ‰§è¡Œä¸­",
    "completed": "å·²å®Œæˆ",
    "terminated": "å·²ç»ˆæ­¢",
}

CUSTOMER_REIMBURSEMENT_PROJECTS = {
    "æ ‡å‡†å·¥æ—¶": 70,
    "äº¤é€šå·¥æ—¶": 35,
    "åŠ ç­å·¥æ—¶": 105,
    "èŠ‚å‡æ—¥å·¥æ—¶": 140,
    "é‡Œç¨‹è´¹": 1,
}

CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS = {
    "Technical Services": "labor_total",
    "Travel Expenses Reimbursement": "travel_total",
    "Mileage Reimbursement": "mileage_total",
    "MRO Suppliesé…ä»¶åŠè€—æè´¹": "mro_supplies_total",
}

DEFAULT_OWNER_NAMES = [
    "æœªçŸ¥",
    "Qcells",
    "Stella",
    "Stem",
    "SunGrid",
    "Adapture Renewables",
    "Aypa",
    "spearmint",
    "plus power",
]

DEFAULT_MANUFACTURER_NAMES = [
    "é˜³å…‰",
    "LG",
    "æ¯”äºšè¿ª",
]

DEFAULT_SITE_OWNER_NAMES = {
    "edinburg": "Stella",
    "revolution": "spearmint",
    "ebony": "plus power",
    "anemoi": "plus power",
}


def normalized_project_name(value):
    value = str(value or "").translate({ord(char): None for char in "\u200b\u200c\u200d\ufeff"})
    return " ".join(value.strip().split())


def project_name_key(value):
    return normalized_project_name(value).casefold()


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# Persistent login session for mobile PWA: without session.permanent the
# cookie is a browser-session cookie and iOS Safari discards it every time
# the PWA is closed, forcing a re-login.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def now():
    return datetime.now(app_timezone()).replace(microsecond=0).isoformat()


def db():
    if "db" not in g:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
        os.makedirs(CONTRACT_ATTACHMENTS_DIR, exist_ok=True)
        os.makedirs(REPORT_ATTACHMENTS_DIR, exist_ok=True)
        os.makedirs(EXPENSE_ATTACHMENTS_DIR, exist_ok=True)
        os.makedirs(CUSTOMER_REIMBURSEMENT_DIR, exist_ok=True)
        os.makedirs(COMPANY_ATTACHMENT_DIR, exist_ok=True)
        os.makedirs(USER_ATTACHMENT_DIR, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # SQLite does not enforce declared foreign keys unless every connection
        # enables them.  Without this, a parent work order can disappear while
        # daily reports and expenses remain as inaccessible orphan records.
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def verify_data_directory_identity():
    if not REQUIRE_DATA_DIRECTORY_IDENTITY:
        return
    if not DATA_DIRECTORY_IDENTITY:
        raise RuntimeError("DATA_DIRECTORY_IDENTITY is required in protected deployments.")
    try:
        with open(DATA_IDENTITY_FILE, "r", encoding="utf-8") as identity_file:
            actual_identity = identity_file.read().strip()
    except FileNotFoundError as error:
        raise RuntimeError(
            "Refusing to start: the mounted data directory has no identity marker."
        ) from error
    if actual_identity != DATA_DIRECTORY_IDENTITY:
        raise RuntimeError(
            "Refusing to start: the mounted data directory identity does not match."
        )
    if not os.path.isfile(DB_PATH) or os.path.getsize(DB_PATH) < 4096:
        raise RuntimeError("Refusing to start: the protected production database is missing or empty.")
    try:
        identity_db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        identity_row = identity_db.execute(
            "select value from settings where key = 'production_database_identity'"
        ).fetchone()
        identity_db.close()
    except sqlite3.Error as error:
        raise RuntimeError(
            "Refusing to start: the protected production database identity cannot be read."
        ) from error
    if not identity_row or identity_row[0] != DATA_DIRECTORY_IDENTITY:
        raise RuntimeError("Refusing to start: the protected production database identity is incorrect.")


def merge_mro_project_aliases(connection):
    canonical_name = "MRO Suppliesé…ä»¶åŠè€—æè´¹"
    def is_alias(value):
        return "".join(normalized_project_name(value).casefold().split()) == "mrosupplies"
    rows = connection.execute("select * from projects order by id").fetchall()
    for old in rows:
        if not is_alias(old["name"]):
            continue
        target = connection.execute("select id from projects where project_type = ? and name = ? order by id limit 1", (old["project_type"], canonical_name)).fetchone()
        if target:
            target_id = target["id"]
            connection.execute("update invoice_items set project_id = ? where project_id = ?", (target_id, old["id"]))
            for table in ("expense_items", "expenses"):
                connection.execute(f"update {table} set project_id = ?, project = ? where project_id = ?", (target_id, canonical_name, old["id"]))
            connection.execute("delete from projects where id = ?", (old["id"],))
        else:
            connection.execute("update projects set name = ?, name_key = ? where id = ?", (canonical_name, project_name_key(canonical_name), old["id"]))
    for table, column in (("expense_items", "project"), ("expenses", "project"), ("invoice_items", "description")):
        for row in connection.execute(f"select id, {column} as name from {table}").fetchall():
            if is_alias(row["name"]):
                connection.execute(f"update {table} set {column} = ? where id = ?", (canonical_name, row["id"]))


def merge_duplicate_projects(connection):
    grouped = {}
    for row in connection.execute("select * from projects order by is_active desc, id asc").fetchall():
        normalized_name = normalized_project_name(row["name"])
        key = project_name_key(normalized_name)
        if row["name"] != normalized_name or row["name_key"] != key:
            connection.execute(
                "update projects set name = ?, name_key = ? where id = ?",
                (normalized_name, key, row["id"]),
            )
        grouped.setdefault((row["project_type"], key), []).append(row)
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        canonical = rows[0]
        canonical_name = normalized_project_name(canonical["name"])
        duplicate_ids = [row["id"] for row in rows[1:]]
        placeholders = ",".join("?" for _ in duplicate_ids)
        connection.execute(
            f"update invoice_items set project_id = ? where project_id in ({placeholders})",
            [canonical["id"], *duplicate_ids],
        )
        connection.execute(
            f"update expense_items set project_id = ?, project = ? where project_id in ({placeholders})",
            [canonical["id"], canonical_name, *duplicate_ids],
        )
        connection.execute(
            f"update expenses set project_id = ?, project = ? where project_id in ({placeholders})",
            [canonical["id"], canonical_name, *duplicate_ids],
        )
        connection.execute(f"delete from projects where id in ({placeholders})", duplicate_ids)


def project_name_exists(project_type, name, excluded_project_id=None):
    normalized_name_key = project_name_key(name)
    for row in db().execute("select id, name, name_key from projects where project_type = ?", (project_type,)).fetchall():
        if excluded_project_id and row["id"] == excluded_project_id:
            continue
        if (row["name_key"] or project_name_key(row["name"])) == normalized_name_key:
            return True
    return False


@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def ensure_column(connection, table, column, definition):
    columns = {row["name"] for row in connection.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"alter table {table} add column {column} {definition}")


def init_db():
    with app.app_context():
        connection = db()
        connection.executescript(
            """
            create table if not exists users (
                id integer primary key autoincrement,
                name text not null,
                email text not null unique,
                password_hash text not null,
                role text not null default 'user',
                address text not null default '',
                phone text not null default '',
                phone_verified integer not null default 0,
                phone_verified_at text,
                default_language text not null default 'zh-CN',
                preferred_communication_language text not null default 'zh-CN',
                communication_languages text not null default 'zh-CN',
                employee_grade_id integer,
                client_id integer,
                created_at text not null,
                foreign key(employee_grade_id) references employee_grades(id),
                foreign key(client_id) references clients(id)
            );

            create table if not exists employee_grades (
                id integer primary key autoincrement,
                grade_name text not null unique,
                description text not null default '',
                base_salary real not null default 0,
                meal_daily_amount real not null default 0,
                car_allowance_method text not null default 'mileage',
                car_mileage_rate real not null default 0.5,
                car_hourly_rate real not null default 10,
                rental_driving_hourly_rate real not null default 15,
                standard_hourly_rate real not null default 0,
                transport_hourly_rate real not null default 0,
                overtime_hourly_rate real not null default 0,
                holiday_hourly_rate real not null default 0,
                is_active integer not null default 1,
                created_at text not null
            );

            create table if not exists clients (
                id integer primary key autoincrement,
                client_number text not null unique,
                name text not null,
                short_name text not null,
                contact_name text,
                email text,
                address text,
                country text not null default 'China',
                payment_term_id integer,
                created_at text not null
            );

            create table if not exists payment_terms (
                id integer primary key autoincrement,
                name text not null unique,
                rule_type text not null default 'fixed_days',
                fixed_days integer not null default 30,
                cutoff_day integer,
                due_day integer,
                before_due_months integer not null default 1,
                after_due_months integer not null default 2,
                notes text,
                is_active integer not null default 1,
                created_at text not null,
                updated_at text not null
            );

            create table if not exists owners (
                id integer primary key autoincrement,
                owner_number text not null unique,
                name text not null,
                created_at text not null
            );

            create table if not exists manufacturers (
                id integer primary key autoincrement,
                manufacturer_number text not null unique,
                name text not null,
                created_at text not null
            );

            create table if not exists projects (
                id integer primary key autoincrement,
                name text not null,
                name_key text,
                project_type text not null default 'invoice',
                default_amount real not null default 0,
                unit_price real not null default 0,
                tax_rate real not null default 0,
                is_active integer not null default 1,
                created_at text not null
            );

            create table if not exists contracts (
                id integer primary key autoincrement,
                contract_number text not null unique,
                client_id integer not null,
                contract_type text not null,
                title text not null,
                status text not null default 'draft',
                signed_date text,
                start_date text,
                end_date text,
                currency text not null default 'USD',
                amount real,
                payment_terms text,
                rate_card text,
                project_name text,
                notes text,
                created_by integer not null,
                created_at text not null,
                updated_at text not null,
                foreign key(client_id) references clients(id),
                foreign key(created_by) references users(id)
            );

            create table if not exists contract_attachments (
                id integer primary key autoincrement,
                contract_id integer not null,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(contract_id) references contracts(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists invoices (
                id integer primary key autoincrement,
                invoice_number text not null unique,
                client_id integer not null,
                issue_date text not null,
                due_date text not null,
                currency text not null default 'USD',
                notes text,
                status text not null default 'submitted',
                return_reason text,
                sent_at text,
                paid_at text,
                payment_amount real,
                payment_note text,
                created_by integer not null,
                created_at text not null,
                foreign key(client_id) references clients(id),
                foreign key(created_by) references users(id)
            );

            create table if not exists invoice_items (
                id integer primary key autoincrement,
                invoice_id integer not null,
                project_id integer not null,
                description text not null,
                amount real not null,
                tax_rate real not null default 0,
                foreign key(invoice_id) references invoices(id) on delete cascade,
                foreign key(project_id) references projects(id)
            );

            create table if not exists invoice_attachments (
                id integer primary key autoincrement,
                invoice_id integer not null,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(invoice_id) references invoices(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists invoice_save_tokens (
                token text primary key,
                invoice_id integer,
                created_at text not null,
                foreign key(invoice_id) references invoices(id) on delete cascade
            );

            create table if not exists messages (
                id integer primary key autoincrement,
                user_id integer not null,
                title text not null,
                body text not null,
                link text,
                is_read integer not null default 0,
                created_at text not null,
                foreign key(user_id) references users(id)
            );

            create table if not exists settings (
                key text primary key,
                value text not null
            );

            create table if not exists knowledge_documents (
                id integer primary key autoincrement,
                title text not null,
                category text not null default 'å…¶ä»–',
                description text not null default '',
                original_filename text not null,
                stored_filename text not null unique,
                content_type text not null default 'application/pdf',
                file_size integer not null default 0,
                uploaded_by integer,
                uploaded_at text not null,
                updated_at text not null,
                foreign key(uploaded_by) references users(id) on delete set null
            );

            create table if not exists knowledge_document_versions (
                id integer primary key autoincrement,
                document_id integer not null,
                version_number integer not null,
                original_filename text not null,
                stored_filename text not null unique,
                file_size integer not null default 0,
                extracted_text text not null default '',
                change_note text not null default '',
                uploaded_by integer,
                uploaded_at text not null,
                unique(document_id, version_number),
                foreign key(document_id) references knowledge_documents(id) on delete cascade,
                foreign key(uploaded_by) references users(id) on delete set null
            );

            create table if not exists role_menu_permissions (
                role text not null,
                menu_key text not null,
                is_enabled integer not null default 1,
                updated_by integer,
                updated_at text not null,
                primary key(role, menu_key),
                foreign key(updated_by) references users(id) on delete set null
            );

            create table if not exists role_action_permissions (
                role text not null,
                resource_key text not null,
                action_key text not null,
                is_enabled integer not null default 1,
                updated_by integer,
                updated_at text not null,
                primary key(role, resource_key, action_key),
                foreign key(updated_by) references users(id) on delete set null
            );

            create table if not exists countries (
                code text primary key,
                region_code text not null,
                is_active integer not null default 1,
                sort_order integer not null default 0,
                created_at text not null
            );

            create table if not exists country_translations (
                country_code text not null,
                language_code text not null,
                name text not null,
                region_name text not null,
                primary key(country_code, language_code),
                foreign key(country_code) references countries(code) on delete cascade
            );

            create table if not exists company_attachments (
                id integer primary key autoincrement,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists user_attachments (
                id integer primary key autoincrement,
                user_id integer not null,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(user_id) references users(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists user_service_orders (
                user_id integer not null,
                service_order_id integer not null,
                assigned_by integer not null,
                assigned_at text not null,
                primary key(user_id, service_order_id),
                foreign key(user_id) references users(id) on delete cascade,
                foreign key(service_order_id) references service_orders(id) on delete cascade,
                foreign key(assigned_by) references users(id)
            );

            create table if not exists buyers (
                id integer primary key autoincrement,
                buyer_number text not null unique,
                country text,
                country_code text not null default 'US',
                name text not null,
                owner text,
                manufacturer_id integer,
                contact_name text,
                contact_details text,
                email text,
                site_size text,
                detailed_address text,
                equipment_manufacturer text,
                latitude real,
                longitude real,
                geocode_address text,
                geocode_status text not null default 'pending',
                geocode_attempted_at text,
                geocode_version text,
                manual_coordinates integer not null default 0,
                created_at text not null
            );

            create table if not exists clock_in_photos (
                id integer primary key autoincrement,
                buyer_id integer not null,
                user_id integer not null,
                captured_at text not null,
                server_received_at text not null,
                time_offset_minutes integer not null default 0,
                latitude real not null,
                longitude real not null,
                location_accuracy real,
                relative_path text not null unique,
                original_filename text not null,
                foreign key(buyer_id) references buyers(id),
                foreign key(user_id) references users(id)
            );

            create table if not exists work_order_types (
                id integer primary key autoincrement,
                code text not null unique,
                name text not null,
                description text,
                is_active integer not null default 1,
                created_at text not null
            );

            create table if not exists service_orders (
                id integer primary key autoincrement,
                order_number text not null unique,
                client_id integer,
                manufacturer_id integer,
                client_name text not null,
                site_address text not null,
                client_order_number text not null,
                status text not null default 'open',
                created_by integer not null,
                created_at text not null,
                foreign key(client_id) references clients(id),
                foreign key(created_by) references users(id)
            );

            create table if not exists service_reports (
                id integer primary key autoincrement,
                service_order_id integer not null,
                report_date text not null,
                actual_work_date text not null,
                total_service_hours real not null default 0,
                travel_hours real not null default 0,
                public_transport_hours real not null default 0,
                driving_miles real not null default 0,
                mileage_billing_method text not null default 'per_person',
                departure_address text,
                site_address text,
                total_time text,
                cabinet_number text,
                arrival_time text,
                departure_time text,
                service_description text,
                report_writer_id integer,
                created_by integer not null,
                created_at text not null,
                updated_at text not null,
                foreign key(service_order_id) references service_orders(id) on delete cascade,
                foreign key(created_by) references users(id),
                foreign key(report_writer_id) references users(id)
            );

            create table if not exists service_report_workers (
                id integer primary key autoincrement,
                report_id integer not null,
                user_id integer not null,
                driving_miles real not null default 0,
                travel_mode text not null default 'legacy',
                travel_hours real,
                public_transport_hours real,
                work_description text not null default '',
                foreign key(report_id) references service_reports(id) on delete cascade,
                foreign key(user_id) references users(id)
            );

            create table if not exists email_delivery_logs (
                id integer primary key autoincrement,
                entity_type text not null,
                entity_id integer not null,
                recipient text not null default '',
                subject text not null default '',
                sent_by integer,
                sent_by_name text not null default '',
                sent_at text not null,
                is_legacy integer not null default 0,
                source_audit_log_id integer unique,
                foreign key(sent_by) references users(id) on delete set null,
                foreign key(source_audit_log_id) references audit_logs(id) on delete set null
            );

            create index if not exists idx_email_delivery_entity
            on email_delivery_logs(entity_type, entity_id, sent_at);

            create table if not exists audit_logs (
                id integer primary key autoincrement,
                user_id integer,
                user_name text not null,
                action text not null,
                entity_type text not null,
                entity_id integer,
                entity_label text not null,
                summary text,
                created_at text not null,
                foreign key(user_id) references users(id) on delete set null
            );

            create table if not exists service_report_saved_parts (
                id integer primary key autoincrement,
                report_id integer not null,
                part_number text,
                part_name text,
                quantity text,
                status text,
                sort_order integer not null default 0,
                foreign key(report_id) references service_reports(id) on delete cascade
            );

            create table if not exists service_report_replaced_parts (
                id integer primary key autoincrement,
                report_id integer not null,
                part_number text,
                part_name text,
                old_serial_number text,
                new_serial_number text,
                quantity text,
                sort_order integer not null default 0,
                foreign key(report_id) references service_reports(id) on delete cascade
            );

            create table if not exists service_report_attachments (
                id integer primary key autoincrement,
                report_id integer not null,
                category text not null,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(report_id) references service_reports(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists service_report_save_tokens (
                token text primary key,
                report_id integer,
                created_at text not null,
                foreign key(report_id) references service_reports(id) on delete cascade
            );

            create table if not exists expenses (
                id integer primary key autoincrement,
                service_order_id integer not null,
                expense_number text not null unique,
                project_id integer,
                project text not null,
                expense_date text not null,
                amount real not null default 0,
                currency text not null default 'USD',
                description text,
                status text not null default 'draft',
                return_reason text,
                reviewed_by integer,
                reviewed_at text,
                created_by integer not null,
                created_at text not null,
                updated_at text not null,
                foreign key(service_order_id) references service_orders(id) on delete cascade,
                foreign key(project_id) references projects(id),
                foreign key(created_by) references users(id),
                foreign key(reviewed_by) references users(id)
            );

            create table if not exists expense_items (
                id integer primary key autoincrement,
                expense_id integer not null,
                line_key text,
                project_id integer not null,
                project text not null,
                amount real not null default 0,
                description text,
                fuel_vehicle_type text,
                sort_order integer not null default 0,
                foreign key(expense_id) references expenses(id) on delete cascade,
                foreign key(project_id) references projects(id)
            );

            create table if not exists expense_save_tokens (
                token text primary key,
                expense_id integer,
                created_at text not null,
                foreign key(expense_id) references expenses(id) on delete cascade
            );

            create table if not exists expense_attachments (
                id integer primary key autoincrement,
                expense_id integer not null,
                expense_item_key text,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(expense_id) references expenses(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );

            create table if not exists expense_duplicate_checks (
                id integer primary key autoincrement,
                expense_id integer not null,
                attachment_id integer not null,
                matched_expense_id integer not null,
                matched_attachment_id integer not null,
                risk_level text not null,
                score integer not null default 0,
                reasons text not null default '',
                deepseek_analysis text,
                deepseek_error text,
                review_status text not null default 'pending',
                reviewed_by integer,
                reviewed_at text,
                created_at text not null,
                updated_at text not null,
                foreign key(expense_id) references expenses(id) on delete cascade,
                foreign key(attachment_id) references expense_attachments(id) on delete cascade,
                foreign key(matched_expense_id) references expenses(id) on delete cascade,
                foreign key(matched_attachment_id) references expense_attachments(id) on delete cascade,
                foreign key(reviewed_by) references users(id),
                unique(expense_id, attachment_id, matched_attachment_id)
            );

            create table if not exists customer_reimbursements (
                id integer primary key autoincrement,
                service_order_id integer not null,
                file_name text not null,
                stored_filename text not null,
                status text not null default 'draft',
                return_reason text,
                reviewed_by integer,
                reviewed_at text,
                labor_total real not null default 0,
                lodging_total real not null default 0,
                travel_total real not null default 0,
                mileage_total real not null default 0,
                mro_supplies_total real not null default 0,
                rental_fuel_total real not null default 0,
                expense_transfer_cutoff_at text,
                total_amount real not null default 0,
                invoice_id integer,
                created_by integer not null,
                created_at text not null,
                foreign key(service_order_id) references service_orders(id) on delete cascade,
                foreign key(invoice_id) references invoices(id) on delete set null,
                foreign key(created_by) references users(id),
                foreign key(reviewed_by) references users(id)
            );

            create table if not exists customer_reimbursement_items (
                id integer primary key autoincrement,
                customer_reimbursement_id integer not null,
                source_report_id integer,
                source_worker_user_id integer,
                worker_name text not null,
                project_date text not null,
                standard_hours real not null default 0,
                transport_hours real not null default 0,
                overtime_hours real not null default 0,
                holiday_hours real not null default 0,
                standard_rate real not null default 0,
                transport_rate real not null default 0,
                overtime_rate real not null default 0,
                holiday_rate real not null default 0,
                labor_total real not null default 0,
                lodging real not null default 0,
                airfare real not null default 0,
                baggage real not null default 0,
                rental_car real not null default 0,
                fuel real not null default 0,
                parking real not null default 0,
                taxi real not null default 0,
                auto_lodging real not null default 0,
                auto_airfare real not null default 0,
                auto_baggage real not null default 0,
                auto_rental_car real not null default 0,
                auto_fuel real not null default 0,
                auto_parking real not null default 0,
                auto_taxi real not null default 0,
                auto_other real not null default 0,
                miles real not null default 0,
                mileage_rate real not null default 0,
                mileage_total real not null default 0,
                other real not null default 0,
                total real not null default 0,
                sort_order integer not null default 0,
                foreign key(customer_reimbursement_id) references customer_reimbursements(id) on delete cascade
            );

            create table if not exists customer_reimbursement_attachments (
                id integer primary key autoincrement,
                customer_reimbursement_id integer not null,
                original_filename text not null,
                stored_filename text not null,
                content_type text,
                source_expense_attachment_id integer,
                uploaded_by integer not null,
                uploaded_at text not null,
                foreign key(customer_reimbursement_id) references customer_reimbursements(id) on delete cascade,
                foreign key(uploaded_by) references users(id)
            );
            """
        )
        ensure_column(connection, "expenses", "beneficiary_id", "integer references users(id)")
        init_field_schema(connection)
        init_rate_schema(connection)
        init_settlement_review_schema(connection)
        init_profitability_schema(connection)
        connection.execute("update expenses set beneficiary_id = created_by where beneficiary_id is null")
        connection.execute("create index if not exists idx_expenses_beneficiary on expenses(beneficiary_id)")
        ensure_column(connection, "invoices", "service_order_id", "integer")
        ensure_column(connection, "service_orders", "contract_id", "integer")
        ensure_column(connection, "users", "is_active", "integer not null default 1")
        ensure_column(connection, "users", "region_code", "text not null default 'americas'")
        ensure_column(connection, "users", "country_code", "text not null default 'US'")
        ensure_column(connection, "users", "employee_grade_id", "integer")
        ensure_column(connection, "users", "address", "text not null default ''")
        ensure_column(connection, "users", "phone", "text not null default ''")
        ensure_column(connection, "users", "phone_verified", "integer not null default 0")
        ensure_column(connection, "users", "phone_verified_at", "text")
        ensure_column(connection, "users", "default_language", "text not null default 'zh-CN'")
        ensure_column(connection, "users", "preferred_communication_language", "text not null default 'zh-CN'")
        ensure_column(connection, "users", "communication_languages", "text not null default 'zh-CN'")
        existing_grade_columns = {
            row["name"] for row in connection.execute("pragma table_info(employee_grades)").fetchall()
        }
        ensure_column(connection, "employee_grades", "description", "text not null default ''")
        ensure_column(connection, "employee_grades", "meal_daily_amount", "real not null default 0")
        ensure_column(connection, "employee_grades", "car_allowance_method", "text not null default 'mileage'")
        ensure_column(connection, "employee_grades", "car_mileage_rate", "real not null default 0.5")
        ensure_column(connection, "employee_grades", "car_hourly_rate", "real not null default 10")
        ensure_column(connection, "employee_grades", "rental_driving_hourly_rate", "real not null default 15")
        ensure_column(connection, "customer_reimbursement_items", "public_transport_hours", "real not null default 0")
        ensure_column(connection, "customer_reimbursement_items", "public_transport_rate", "real not null default 0")
        if "car_mileage_rate" not in existing_grade_columns:
            legacy_rate = connection.execute(
                "select value from settings where key = 'payroll_car_mileage_rate'"
            ).fetchone()
            connection.execute(
                "update employee_grades set car_allowance_method = 'mileage', car_mileage_rate = ?, car_hourly_rate = 10",
                (to_float(legacy_rate["value"]) if legacy_rate else 0.5,),
            )
        ensure_column(connection, "service_reports", "report_writer_id", "integer")
        ensure_column(connection, "service_reports", "mileage_billing_method", "text not null default 'per_person'")
        ensure_column(connection, "service_report_workers", "travel_mode", "text not null default 'legacy'")
        ensure_column(connection, "service_report_workers", "travel_hours", "real")
        ensure_column(connection, "service_report_workers", "public_transport_hours", "real")
        ensure_column(connection, "service_report_workers", "work_description", "text not null default ''")
        ensure_column(connection, "knowledge_documents", "is_pinned", "integer not null default 0")
        ensure_column(connection, "knowledge_documents", "expires_on", "text")
        ensure_column(connection, "knowledge_documents", "view_count", "integer not null default 0")
        ensure_column(connection, "knowledge_documents", "download_count", "integer not null default 0")
        ensure_column(connection, "knowledge_documents", "search_text", "text not null default ''")
        ensure_column(connection, "knowledge_documents", "text_indexed_at", "text")
        ensure_column(connection, "knowledge_documents", "current_version", "integer not null default 1")
        connection.execute(
            """
            insert or ignore into knowledge_document_versions (
                document_id, version_number, original_filename, stored_filename,
                file_size, extracted_text, change_note, uploaded_by, uploaded_at
            )
            select id, current_version, original_filename, stored_filename,
                   file_size, search_text, 'åˆå§‹ç‰ˆæœ¬', uploaded_by, uploaded_at
            from knowledge_documents
            """
        )
        unindexed_documents = connection.execute(
            "select id, stored_filename, current_version from knowledge_documents where text_indexed_at is null"
        ).fetchall()
        for document in unindexed_documents:
            pdf_path = os.path.join(KNOWLEDGE_BASE_DIR, document["stored_filename"])
            extracted_text = extract_knowledge_pdf_text(pdf_path) if os.path.isfile(pdf_path) else ""
            indexed_at = now()
            connection.execute(
                "update knowledge_documents set search_text = ?, text_indexed_at = ? where id = ?",
                (extracted_text, indexed_at, document["id"]),
            )
            connection.execute(
                """
                update knowledge_document_versions set extracted_text = ?
                where document_id = ? and version_number = ?
                """,
                (extracted_text, document["id"], document["current_version"]),
            )
        ensure_column(connection, "service_reports", "actual_work_date", "text")
        ensure_column(connection, "service_report_workers", "driving_miles", "real not null default 0")
        connection.execute(
            "update service_reports set actual_work_date = report_date where trim(coalesce(actual_work_date, '')) = ''"
        )
        connection.execute(
            """
            update service_reports
            set actual_work_date = '2026-06-24'
            where report_date = '2026-07-01'
              and (trim(coalesce(actual_work_date, '')) = '' or actual_work_date = report_date)
            """
        )
        connection.execute(
            """
            update service_reports
            set actual_work_date = '2026-06-25'
            where report_date = '2026-07-02'
              and (trim(coalesce(actual_work_date, '')) = '' or actual_work_date = report_date)
            """
        )
        ensure_column(connection, "projects", "project_type", "text not null default 'invoice'")
        ensure_column(connection, "projects", "name_key", "text")
        ensure_column(connection, "projects", "unit_price", "real not null default 0")
        ensure_column(connection, "customer_reimbursements", "status", "text not null default 'draft'")
        ensure_column(connection, "customer_reimbursements", "return_reason", "text")
        ensure_column(connection, "customer_reimbursements", "reviewed_by", "integer")
        ensure_column(connection, "customer_reimbursements", "reviewed_at", "text")
        ensure_column(connection, "customer_reimbursements", "mro_supplies_total", "real not null default 0")
        ensure_column(connection, "customer_reimbursements", "rental_fuel_total", "real not null default 0")
        ensure_column(connection, "customer_reimbursements", "expense_transfer_cutoff_at", "text")
        for column_name in (
            "auto_lodging", "auto_airfare", "auto_baggage", "auto_rental_car",
            "auto_fuel", "auto_parking", "auto_taxi", "auto_other",
        ):
            ensure_column(connection, "customer_reimbursement_items", column_name, "real not null default 0")
        ensure_column(connection, "customer_reimbursement_items", "source_report_id", "integer")
        ensure_column(connection, "customer_reimbursement_items", "source_worker_user_id", "integer")
        ensure_column(connection, "expense_items", "fuel_vehicle_type", "text")
        ensure_column(connection, "customer_reimbursement_items", "auto_expense_sources", "text not null default '{}'")
        ensure_column(connection, "expense_items", "line_key", "text")
        ensure_column(connection, "expense_attachments", "expense_item_key", "text")
        ensure_column(connection, "expense_attachments", "file_sha256", "text")
        ensure_column(connection, "expense_attachments", "image_dhash", "text")
        connection.execute(
            "create index if not exists idx_expense_attachments_sha256 on expense_attachments(file_sha256)"
        )
        connection.execute(
            "create index if not exists idx_expense_duplicate_checks_expense on expense_duplicate_checks(expense_id)"
        )
        connection.execute(
            "update expense_items set line_key = 'item-' || id where coalesce(trim(line_key), '') = ''"
        )
        connection.execute(
            """
            update expense_items
            set fuel_vehicle_type = 'personal'
            where coalesce(trim(fuel_vehicle_type), '') = ''
              and (
                lower(project) like '%fuel%' or lower(project) like '%gas%'
                or project like '%æ²¹è´¹%' or project like '%åŠ æ²¹%'
              )
            """
        )
        ensure_column(connection, "customer_reimbursement_attachments", "source_expense_attachment_id", "integer")
        connection.execute(
            """
            create unique index if not exists idx_customer_reimbursement_attachment_expense_source
            on customer_reimbursement_attachments(source_expense_attachment_id)
            where source_expense_attachment_id is not null
            """
        )
        ensure_column(connection, "expenses", "project_id", "integer")
        ensure_column(connection, "expenses", "payout_status", "text not null default 'pending'")
        ensure_column(connection, "expenses", "reimbursed_by", "integer")
        ensure_column(connection, "expenses", "reimbursed_at", "text")
        ensure_column(connection, "service_orders", "latitude", "real")
        ensure_column(connection, "service_orders", "longitude", "real")
        ensure_column(connection, "service_orders", "geocode_address", "text")
        ensure_column(connection, "service_orders", "geocode_status", "text not null default 'pending'")
        ensure_column(connection, "service_orders", "geocode_attempted_at", "text")
        ensure_column(connection, "service_orders", "geocode_version", "text")
        ensure_column(connection, "buyers", "manual_coordinates", "integer not null default 0")
        ensure_column(connection, "service_orders", "buyer_id", "integer")
        ensure_column(connection, "service_orders", "client_id", "integer")
        ensure_column(connection, "service_orders", "manufacturer_id", "integer")
        ensure_column(connection, "buyers", "manufacturer_id", "integer")
        migrate_historical_customer_reports(connection, now(), app.logger)
        ensure_column(connection, "service_orders", "buyer_contact_name", "text")
        ensure_column(connection, "service_orders", "buyer_contact_details", "text")
        ensure_column(connection, "service_orders", "start_date", "text")
        ensure_column(connection, "service_orders", "work_order_type_id", "integer")
        ensure_column(connection, "service_orders", "region_code", "text not null default 'americas'")
        ensure_column(connection, "service_orders", "country_code", "text not null default 'US'")
        ensure_column(connection, "buyers", "client_id", "integer")
        ensure_column(connection, "clients", "payment_term_id", "integer")
        # â”€â”€â”€ AI Daily Report (Phase 1+) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # FK strategy (Phase 1 decision):
        #   ai_daily_report_actions.draft_id  -> ai_daily_report_drafts.id  (Phase 2)
        #   ai_daily_report_actions.executed_by -> users.id                   (Phase 2)
        #   ai_daily_report_drafts.saved_report_id -> service_reports.id      (Phase 9, Confirm & Save)
        # Reason for deferral: SQLite cannot add FK constraints via ALTER TABLE;
        #   adding them now requires table rebuild. Application layer currently
        #   guarantees integrity via: (1) get_draft() existence check before
        #   recording actions; (2) executed_by always set from session user_id;
        #   (3) saved_report_id only written by formal save flow in Phase 9.
        connection.execute(
            """
            create table if not exists ai_daily_report_drafts (
                id integer primary key autoincrement,
                service_order_id integer not null,
                report_date text not null,
                draft_data text not null,
                status text not null default 'draft',
                draft_version integer not null default 1,
                conversation_context text,  -- Phase 2: max 6 rounds, 2000 chars/round, not sent to DeepSeek
                ai_model text,
                ai_confidence real,
                verification_required integer not null default 0,
                verification_fields text,
                created_by integer not null,
                created_at text not null,
                updated_at text not null,
                saved_report_id integer,
                foreign key(service_order_id) references service_orders(id) on delete cascade,
                foreign key(created_by) references users(id)
            )
            """
        )
        ensure_column(connection, "ai_daily_report_drafts", "draft_version", "integer not null default 1")
        connection.execute(
            "create index if not exists idx_ai_draft_order_date on ai_daily_report_drafts(service_order_id, report_date)"
        )
        connection.execute(
            """
            create table if not exists ai_daily_report_actions (
                id integer primary key autoincrement,
                draft_id integer not null,
                action_id text not null,
                action_version integer not null,
                intent text not null,
                action_payload text not null,
                ai_generated integer not null default 1,
                executed_at text not null,
                executed_by integer not null,
                result text,
                unique(draft_id, action_id),
                foreign key(draft_id) references ai_daily_report_drafts(id) on delete cascade
            )
            """
        )
        connection.execute(
            "create index if not exists idx_ai_actions_draft on ai_daily_report_actions(draft_id)"
        )
        connection.execute(
            """
            create table if not exists ai_photo_analysis (
                id integer primary key autoincrement,
                photo_path text not null,
                photo_hash text not null,
                analysis_model text not null,
                analysis_version integer not null,
                classification text not null,
                sub_category text,
                confidence real not null default 0,
                description text,
                equipment_id text,
                capture_time text,
                analyzed_at text not null,
                unique(photo_path, photo_hash, analysis_model, analysis_version)
            )
            """
        )
        # service_reports audit fields for AI-generated arrivals/departures
        # â”€â”€â”€ Phase 8: Attachment Manifest (frozen preparation layer) â”€â”€â”€â”€â”€â”€
        # Four-layer model (sealed design):
        #   Manifest -> ManifestSource (provenance) -> PreparedAsset (physical)
        #            -> ManifestRole (business role)
        # Phase 8 never writes service_reports / service_report_workers /
        # service_report_attachments; Phase 9 consumes a ready manifest only.
        connection.execute(
            """
            create table if not exists ai_daily_report_attachment_manifests (
                id integer primary key autoincrement,
                manifest_id text not null unique,
                draft_id integer not null,
                draft_version integer not null,
                service_order_id integer not null,
                report_date text not null,
                manifest_version integer not null,
                validation_fingerprint text not null,
                draft_data_hash text not null,
                photo_set_fingerprint text,
                status text not null default 'preparing',
                manifest_fingerprint text not null,
                expected_plan text not null,
                created_by integer not null,
                created_at text not null,
                updated_at text not null,
                unique(draft_id, draft_version, validation_fingerprint, manifest_fingerprint),
                foreign key(draft_id) references ai_daily_report_drafts(id) on delete cascade
            )
            """
        )
        connection.execute(
            """
            create table if not exists ai_daily_report_prepared_assets (
                id integer primary key autoincrement,
                asset_id text not null unique,
                manifest_id text not null,
                prepared_relative_path text not null,
                prepared_sha256 text not null,
                content_type text not null,
                file_size integer not null,
                integrity_status text not null default 'verified',
                prepared_at text not null,
                unique(manifest_id, prepared_sha256),
                foreign key(manifest_id) references ai_daily_report_attachment_manifests(manifest_id) on delete cascade
            )
            """
        )
        connection.execute(
            """
            create table if not exists ai_daily_report_manifest_sources (
                id integer primary key autoincrement,
                source_id text not null unique,
                manifest_id text not null,
                source_type text not null,
                source_identity text not null,
                source_photo_id text,
                source_evidence_id text,
                source_relative_path text not null,
                source_sha256 text not null,
                asset_id text,
                provider text,
                provider_content text,
                compliance_review_required integer not null default 0,
                compliance_status text not null default 'na',
                unique(manifest_id, source_identity),
                foreign key(manifest_id) references ai_daily_report_attachment_manifests(manifest_id) on delete cascade
            )
            """
        )
        # Compliance review audit trail (Phase 9 Final Release Gate 6): who /
        # when reviewed each compliance-required source. Idempotent for the
        # already-deployed Phase 8 production schema.
        ensure_column(connection, "ai_daily_report_manifest_sources", "compliance_reviewed_by", "integer")
        ensure_column(connection, "ai_daily_report_manifest_sources", "compliance_reviewed_at", "text")
        connection.execute(
            """
            create table if not exists ai_daily_report_manifest_roles (
                id integer primary key autoincrement,
                manifest_id text not null,
                source_id text not null,
                role_type text not null,
                category text not null,
                visibility text not null,
                purpose text not null,
                materialization_required integer not null default 1,
                sort_order integer not null default 0,
                unique(manifest_id, role_type, source_id),
                foreign key(manifest_id) references ai_daily_report_attachment_manifests(manifest_id) on delete cascade,
                foreign key(source_id) references ai_daily_report_manifest_sources(source_id) on delete cascade
            )
            """
        )
        connection.execute(
            "create index if not exists idx_ai_manifest_draft on ai_daily_report_attachment_manifests(draft_id, status)"
        )
        connection.execute(
            "create index if not exists idx_ai_manifest_source on ai_daily_report_manifest_sources(manifest_id)"
        )
        connection.execute(
            "create index if not exists idx_ai_manifest_asset on ai_daily_report_prepared_assets(manifest_id)"
        )
        connection.execute(
            """
            create table if not exists ai_daily_report_formal_commits (
                id integer primary key autoincrement,
                commit_id text not null unique,
                draft_id integer not null unique,
                manifest_id text not null,
                draft_version integer not null,
                validation_fingerprint text not null,
                manifest_fingerprint text not null,
                manifest_snapshot text not null,
                fields_provenance text not null,
                service_report_id integer,
                status text not null default 'committing',
                failure_code text,
                formal_files text not null default '{}',
                created_by integer not null,
                started_at text not null,
                committed_at text,
                updated_at text,
                unique(draft_id, manifest_fingerprint),
                foreign key(draft_id) references ai_daily_report_drafts(id) on delete cascade
            )
            """
        )
        connection.execute(
            "create index if not exists idx_ai_formal_commit_draft on ai_daily_report_formal_commits(draft_id, status)"
        )
        connection.execute(
            "create index if not exists idx_ai_formal_commit_report on ai_daily_report_formal_commits(service_report_id)"
        )
        ensure_column(connection, "service_reports", "ai_generated", "integer not null default 0")
        ensure_column(connection, "service_reports", "arrival_time_source", "text")
        ensure_column(connection, "service_reports", "departure_time_source", "text")
        ensure_column(connection, "service_reports", "arrival_photo_relative_path", "text")
        ensure_column(connection, "service_reports", "arrival_photo_hash", "text")
        ensure_column(connection, "service_reports", "departure_photo_relative_path", "text")
        ensure_column(connection, "service_reports", "departure_photo_hash", "text")
        ensure_column(connection, "service_reports", "ai_draft_id", "integer")
        # v0.1.243 backfill: early AI-generated reports stored ISO photo-timeline
        # timestamps in arrival_time/departure_time; normalize them to 'HH:MM'
        # so the report form/view/Word export bind correctly.
        try:
            from ai_daily_report.formal_save import normalize_report_time
            for column in ("arrival_time", "departure_time"):
                rows = connection.execute(
                    f"select id, {column} as raw_time from service_reports "
                    f"where {column} is not null and instr({column}, 'T') > 0"
                ).fetchall()
                for row in rows:
                    normalized = normalize_report_time(row["raw_time"])
                    if normalized:
                        connection.execute(
                            f"update service_reports set {column} = ? where id = ?",
                            (normalized, row["id"]),
                        )
        except Exception:
            pass
        connection.execute(
            """
            create table if not exists payment_terms (
                id integer primary key autoincrement,
                name text not null unique,
                rule_type text not null default 'fixed_days',
                fixed_days integer not null default 30,
                cutoff_day integer,
                due_day integer,
                before_due_months integer not null default 1,
                after_due_months integer not null default 2,
                notes text,
                is_active integer not null default 1,
                created_at text not null,
                updated_at text not null
            )
            """
        )
        default_payment_term = connection.execute(
            "select id from payment_terms where name = ?",
            ("Net 30",),
        ).fetchone()
        if not default_payment_term:
            cursor = connection.execute(
                """
                insert into payment_terms (
                    name, rule_type, fixed_days, before_due_months, after_due_months,
                    notes, is_active, created_at, updated_at
                ) values (?, 'fixed_days', 30, 1, 2, ?, 1, ?, ?)
                """,
                ("Net 30", "å¼€ç¥¨æ—¥èµ·30å¤©åˆ°æœŸ", now(), now()),
            )
            default_payment_term_id = cursor.lastrowid
        else:
            default_payment_term_id = default_payment_term["id"]
        connection.execute(
            """
            insert or ignore into payment_terms (
                name, rule_type, fixed_days, cutoff_day, due_day,
                before_due_months, after_due_months, notes, is_active, created_at, updated_at
            ) values (?, 'monthly_cutoff', 30, 20, 19, 1, 2, ?, 1, ?, ?)
            """,
            (
                "20æ—¥æˆªæ­¢ã€æ¬¡æœˆ19æ—¥ä»˜æ¬¾",
                "æ¯æœˆ20æ—¥ä¸ºåŒ…å«å½“å¤©çš„æˆªæ­¢æ—¥ï¼›21æ—¥èµ·è¿›å…¥ä¸‹ä¸€è´¦æœŸ",
                now(),
                now(),
            ),
        )
        connection.execute(
            "update clients set payment_term_id = ? where payment_term_id is null",
            (default_payment_term_id,),
        )
        ensure_column(connection, "buyers", "owner", "text")
        ensure_column(connection, "buyers", "owner_id", "integer")
        ensure_column(connection, "buyers", "manufacturer_id", "integer")
        ensure_column(connection, "buyers", "email", "text")
        ensure_column(connection, "buyers", "site_size", "text")
        ensure_column(connection, "buyers", "country_code", "text not null default 'US'")
        site_country_migration = connection.execute(
            "select value from settings where key = ?",
            ("buyers_country_code_v1",),
        ).fetchone()
        if not site_country_migration:
            connection.execute("update buyers set country_code = 'US', country = 'US'")
            connection.execute(
                "update buyers set country_code = 'CA', country = 'CA' where buyer_number = 'BUY00017'"
            )
            connection.execute(
                "insert or replace into settings (key, value) values (?, ?)",
                ("buyers_country_code_v1", now()),
            )
        merge_mro_project_aliases(connection)
        try:
            merge_duplicate_projects(connection)
        except sqlite3.Error:
            app.logger.exception("Failed to merge duplicate projects during startup.")
        connection.execute("drop index if exists idx_projects_type_name_unique")
        existing_owner_names = [
            row["owner"]
            for row in connection.execute(
                """
                select distinct trim(owner) as owner
                from buyers
                where trim(coalesce(owner, '')) != ''
                  and owner_id is null
                order by trim(owner)
                """
            ).fetchall()
        ]
        next_owner_sequence = connection.execute("select coalesce(max(id), 0) + 1 as value from owners").fetchone()["value"]
        for owner_name in DEFAULT_OWNER_NAMES:
            if connection.execute(
                "select 1 from owners where lower(trim(name)) = lower(trim(?))",
                (owner_name,),
            ).fetchone():
                continue
            while True:
                owner_number = f"OWN{next_owner_sequence:05d}"
                next_owner_sequence += 1
                if not connection.execute("select id from owners where owner_number = ?", (owner_number,)).fetchone():
                    break
            connection.execute(
                "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
                (owner_number, owner_name, now()),
            )
        next_manufacturer_sequence = connection.execute(
            "select coalesce(max(id), 0) + 1 as value from manufacturers"
        ).fetchone()["value"]
        for manufacturer_name in DEFAULT_MANUFACTURER_NAMES:
            if connection.execute(
                "select 1 from manufacturers where lower(trim(name)) = lower(trim(?))",
                (manufacturer_name,),
            ).fetchone():
                continue
            while True:
                manufacturer_number = f"MFG{next_manufacturer_sequence:05d}"
                next_manufacturer_sequence += 1
                if not connection.execute(
                    "select id from manufacturers where manufacturer_number = ?",
                    (manufacturer_number,),
                ).fetchone():
                    break
            connection.execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values (?, ?, ?)",
                (manufacturer_number, manufacturer_name, now()),
            )
        connection.execute(
            """
            update buyers
            set manufacturer_id = (
                select manufacturers.id
                from manufacturers
                where lower(trim(manufacturers.name)) = lower(trim(buyers.equipment_manufacturer))
                limit 1
            )
            where manufacturer_id is null
              and trim(coalesce(equipment_manufacturer, '')) != ''
              and exists (
                select 1 from manufacturers
                where lower(trim(manufacturers.name)) = lower(trim(buyers.equipment_manufacturer))
              )
            """
        )
        connection.execute(
            """
            update service_orders
            set manufacturer_id = (
                select buyers.manufacturer_id
                from buyers
                where buyers.id = service_orders.buyer_id
            )
            where manufacturer_id is null
              and exists (
                select 1 from buyers
                where buyers.id = service_orders.buyer_id
                  and buyers.manufacturer_id is not null
              )
            """
        )
        for owner_name in existing_owner_names:
            existing_owner = connection.execute(
                "select id from owners where lower(trim(name)) = lower(trim(?))",
                (owner_name,),
            ).fetchone()
            if existing_owner:
                owner_id = existing_owner["id"]
            else:
                while True:
                    owner_number = f"OWN{next_owner_sequence:05d}"
                    next_owner_sequence += 1
                    if not connection.execute("select id from owners where owner_number = ?", (owner_number,)).fetchone():
                        break
                cursor = connection.execute(
                    "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
                    (owner_number, owner_name, now()),
                )
                owner_id = cursor.lastrowid
            connection.execute(
                "update buyers set owner_id = ? where owner_id is null and lower(trim(owner)) = lower(trim(?))",
                (owner_id, owner_name),
            )
        for site_name, owner_name in DEFAULT_SITE_OWNER_NAMES.items():
            owner = connection.execute(
                "select id, name from owners where lower(trim(name)) = lower(trim(?))",
                (owner_name,),
            ).fetchone()
            if owner:
                connection.execute(
                    """
                    update buyers
                    set owner_id = ?, owner = ?
                    where lower(trim(name)) = lower(trim(?))
                    """,
                    (owner["id"], owner["name"], site_name),
                )
        unknown_owner_row = connection.execute("select id, name from owners where name = ?", ("æœªçŸ¥",)).fetchone()
        if unknown_owner_row:
            connection.execute(
                """
                update buyers
                set owner_id = ?, owner = ?
                where owner_id is null
                   or trim(coalesce(owner, '')) = ''
                """,
                (unknown_owner_row["id"], unknown_owner_row["name"]),
            )
        connection.execute(
            "update users set region_code = 'americas', country_code = 'US' "
            "where trim(coalesce(region_code, '')) = '' or trim(coalesce(country_code, '')) = ''"
        )
        connection.execute(
            "update service_orders set region_code = 'americas', country_code = 'US' "
            "where trim(coalesce(region_code, '')) = '' or trim(coalesce(country_code, '')) = ''"
        )
        connection.execute(
            """
            update buyers
            set client_id = (select id from clients where client_number = '00001' limit 1)
            where client_id is null
              and exists (select 1 from clients where client_number = '00001')
            """
        )
        connection.execute(
            """
            update service_orders
            set client_id = (
                select clients.id
                from clients
                where lower(trim(clients.name)) = lower(trim(service_orders.client_name))
                   or lower(trim(clients.short_name)) = lower(trim(service_orders.client_name))
                order by clients.id
                limit 1
            )
            where client_id is null
            """
        )
        connection.execute(
            """
            update service_orders
            set client_id = (
                select id from clients where client_number = '00001' limit 1
            )
            where client_id is null
              and exists (select 1 from clients where client_number = '00001')
            """
        )
        existing_buyer_names = {
            row["name"].strip().casefold(): row["id"]
            for row in connection.execute("select id, name from buyers").fetchall()
            if row["name"] and row["name"].strip()
        }
        next_buyer_sequence = connection.execute("select coalesce(max(id), 0) + 1 as value from buyers").fetchone()["value"]
        legacy_orders = connection.execute(
            """
            select id, client_name, site_address
            from service_orders
            where buyer_id is null and trim(coalesce(client_name, '')) != ''
            order by id
            """
        ).fetchall()
        for legacy_order in legacy_orders:
            buyer_name = legacy_order["client_name"].strip()
            buyer_id = existing_buyer_names.get(buyer_name.casefold())
            if not buyer_id:
                while True:
                    buyer_number = f"BUY{next_buyer_sequence:05d}"
                    next_buyer_sequence += 1
                    if not connection.execute(
                        "select id from buyers where buyer_number = ?",
                        (buyer_number,),
                    ).fetchone():
                        break
                cursor = connection.execute(
                    """
                    insert into buyers (
                        buyer_number, country, country_code, name, detailed_address, created_at
                    ) values (?, 'US', 'US', ?, ?, ?)
                    """,
                    (buyer_number, buyer_name, legacy_order["site_address"], now()),
                )
                buyer_id = cursor.lastrowid
                existing_buyer_names[buyer_name.casefold()] = buyer_id
            connection.execute(
                "update service_orders set buyer_id = ? where id = ?",
                (buyer_id, legacy_order["id"]),
            )
        connection.execute(
            """
            insert into expense_items (expense_id, project_id, project, amount, description, sort_order)
            select expenses.id, expenses.project_id, expenses.project, expenses.amount, expenses.description, 0
            from expenses
            where expenses.project_id is not null
              and not exists (
                  select 1 from expense_items where expense_items.expense_id = expenses.id
              )
            """
        )
        connection.execute(
            """
            update customer_reimbursements
            set mro_supplies_total = coalesce((
                    select sum(expense_items.amount)
                    from expenses
                    join expense_items on expense_items.expense_id = expenses.id
                    left join projects on projects.id = expense_items.project_id
                    where expenses.service_order_id = customer_reimbursements.service_order_id
                      and expenses.status = 'approved'
                      and coalesce(projects.name_key, lower(trim(expense_items.project))) = 'mro supplies'
                ), 0)
            where not exists (
                select 1
                from customer_reimbursement_items
                where customer_reimbursement_items.customer_reimbursement_id = customer_reimbursements.id
                  and (
                    coalesce(auto_lodging, 0) + coalesce(auto_airfare, 0)
                    + coalesce(auto_baggage, 0) + coalesce(auto_rental_car, 0)
                    + coalesce(auto_fuel, 0) + coalesce(auto_parking, 0)
                    + coalesce(auto_taxi, 0) + coalesce(auto_other, 0)
                  ) > 0
            )
            """
        )
        connection.execute(
            """
            update customer_reimbursements
            set total_amount = coalesce(labor_total, 0) + coalesce(travel_total, 0)
                + coalesce(mileage_total, 0) + coalesce(mro_supplies_total, 0)
            """
        )
        connection.execute(
            """
            delete from messages
            where title = 'å‘ç¥¨å·²ç¡®è®¤å®Œæˆ'
              and exists (
                  select 1 from messages as reviewed
                  where reviewed.user_id = messages.user_id
                    and reviewed.link = messages.link
                    and reviewed.title = 'å‘ç¥¨å·²å®¡æ ¸å®Œæˆ'
              )
            """
        )
        connection.execute(
            """
            update messages
            set title = 'å‘ç¥¨å·²å®¡æ ¸å®Œæˆ',
                body = replace(body, 'å·²ç”±ç»ç†ç¡®è®¤å®Œæˆ', 'å·²å®¡æ ¸å®Œæˆ')
            where title = 'å‘ç¥¨å·²ç¡®è®¤å®Œæˆ'
            """
        )
        connection.execute(
            """
            update customer_reimbursements
            set status = 'approved'
            where invoice_id is not null and status != 'approved'
            """
        )
        connection.execute(
            """
            update invoices
            set status = 'completed', return_reason = null
            where status in ('submitted', 'returned')
            """
        )
        connection.execute("update users set role = 'external_manager' where role = 'external'")
        connection.execute(
            """
            update users
            set name = 'é™ˆæ°¸æ˜'
            where name like 'é™ˆæ°¸%' and instr(name, 'ï¿½') > 0
            """
        )
        connection.execute(
            """
            update customer_reimbursement_items
            set worker_name = 'é™ˆæ°¸æ˜'
            where worker_name like 'é™ˆæ°¸%' and instr(worker_name, 'ï¿½') > 0
            """
        )
        seed_customer_reimbursement_projects(connection)
        seed_countries(connection)
        seed_settings(connection)
        email_delivery_history_migration = connection.execute(
            "select value from settings where key = ?",
            ("email_delivery_history_v1",),
        ).fetchone()
        if not email_delivery_history_migration:
            connection.execute(
                """
                insert into email_delivery_logs (
                    entity_type, entity_id, recipient, subject, sent_by_name, sent_at, is_legacy
                )
                select 'invoice', invoices.id, coalesce(clients.email, ''),
                       'Invoice ' || invoices.invoice_number, 'å†å²è®°å½•', invoices.sent_at, 1
                from invoices
                left join clients on clients.id = invoices.client_id
                where trim(coalesce(invoices.sent_at, '')) != ''
                """
            )
            connection.execute(
                """
                insert or ignore into email_delivery_logs (
                    entity_type, entity_id, recipient, subject, sent_by, sent_by_name,
                    sent_at, source_audit_log_id
                )
                select 'customer_reimbursement', audit_logs.entity_id, coalesce(audit_logs.summary, ''),
                       audit_logs.entity_label, audit_logs.user_id, audit_logs.user_name,
                       audit_logs.created_at, audit_logs.id
                from audit_logs
                where audit_logs.action = 'send'
                  and audit_logs.entity_type = 'customer_reimbursement'
                  and audit_logs.entity_id is not null
                """
            )
            connection.execute(
                "insert into settings (key, value) values (?, ?)",
                ("email_delivery_history_v1", now()),
            )
        historical_paid_date_migration = connection.execute(
            "select value from settings where key = ?",
            ("payroll_historical_paid_date_20260705_v1",),
        ).fetchone()
        if not historical_paid_date_migration:
            connection.execute(
                "insert or replace into settings (key, value) values (?, ?)",
                ("payroll_historical_paid_date", "2026-07-05"),
            )
            connection.execute(
                "insert or replace into settings (key, value) values (?, ?)",
                ("payroll_cycle_start", "2026-07-06"),
            )
            connection.execute(
                "insert into settings (key, value) values (?, ?)",
                ("payroll_historical_paid_date_20260705_v1", now()),
            )
        seed_role_permissions(connection)
        if not connection.execute("select id from users where role = 'admin' limit 1").fetchone():
            admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
            admin_password = os.environ.get("ADMIN_PASSWORD", "")
            if not admin_email:
                raise RuntimeError(
                    "Refusing to initialize administrator: ADMIN_EMAIL is required."
                )
            if not admin_password:
                raise RuntimeError(
                    "Refusing to initialize administrator: ADMIN_PASSWORD is required."
                )
            if admin_password == "change-me-now":
                raise RuntimeError(
                    "Refusing to initialize administrator: a secure ADMIN_PASSWORD is required."
                )
            if connection.execute(
                "select id from users where lower(email) = ? limit 1",
                (admin_email,),
            ).fetchone():
                raise RuntimeError(
                    "Refusing to initialize administrator: ADMIN_EMAIL already belongs to an existing user."
                )
            connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values (?, ?, ?, 'admin', ?)
                """,
                ("Admin", admin_email, generate_password_hash(admin_password), now()),
            )
        connection.commit()


def seed_countries(connection):
    countries = [
        (
            "US",
            "americas",
            10,
            {
                "zh-CN": ("ç¾å›½", "ç¾æ´²"),
                "en": ("United States", "Americas"),
                "nl": ("Verenigde Staten", "Amerika"),
                "de": ("Vereinigte Staaten", "Amerika"),
                "es": ("Estados Unidos", "AmÃ©rica"),
            },
        ),
        (
            "CN",
            "asia",
            20,
            {
                "zh-CN": ("ä¸­å›½", "äºšæ´²"),
                "en": ("China", "Asia"),
                "nl": ("China", "AziÃ«"),
                "de": ("China", "Asien"),
                "es": ("China", "Asia"),
            },
        ),
        (
            "CA",
            "americas",
            25,
            {
                "zh-CN": ("åŠ æ‹¿å¤§", "ç¾æ´²"),
                "en": ("Canada", "Americas"),
                "nl": ("Canada", "Amerika"),
                "de": ("Kanada", "Amerika"),
                "es": ("CanadÃ¡", "AmÃ©rica"),
            },
        ),
        (
            "NL",
            "europe",
            30,
            {
                "zh-CN": ("è·å…°", "æ¬§æ´²"),
                "en": ("Netherlands", "Europe"),
                "nl": ("Nederland", "Europa"),
                "de": ("Niederlande", "Europa"),
                "es": ("PaÃ­ses Bajos", "Europa"),
            },
        ),
    ]
    for code, region_code, sort_order, translations in countries:
        connection.execute(
            """
            insert or ignore into countries (code, region_code, is_active, sort_order, created_at)
            values (?, ?, 1, ?, ?)
            """,
            (code, region_code, sort_order, now()),
        )
        for language_code, (name, region_name) in translations.items():
            connection.execute(
                """
                insert or ignore into country_translations
                    (country_code, language_code, name, region_name)
                values (?, ?, ?, ?)
                """,
                (code, language_code, name, region_name),
            )


def current_language():
    language = session.get("language", DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_from_form(default=DEFAULT_LANGUAGE):
    language = request.form.get("default_language", default or DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


COMMUNICATION_LANGUAGES = {
    "zh-CN": "ä¸­æ–‡",
    "en": "English",
    "es": "EspaÃ±ol",
}


def communication_languages_from_form(default="zh-CN"):
    preferred = request.form.get("preferred_communication_language", default or "zh-CN")
    if preferred not in COMMUNICATION_LANGUAGES:
        preferred = "zh-CN"
    selected = [code for code in request.form.getlist("communication_language")
                if code in COMMUNICATION_LANGUAGES]
    return preferred, ",".join(dict.fromkeys([preferred, *selected]))


def normalize_phone(value, country_code="US"):
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if country_code == "US" and len(digits) == 10:
        digits = "1" + digits
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if country_code == "US" and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 8 <= len(digits) <= 15:
        return "+" + digits
    raise ValueError("è¯·å¡«å†™æœ‰æ•ˆçš„æ‰‹æœºå·ï¼Œå¹¶åŒ…å«å›½å®¶ä»£ç ã€‚")


def clear_session_preserving_language():
    language = current_language()
    session.clear()
    session["language"] = language


def country_rows(include_inactive=False):
    active_clause = "" if include_inactive else "where countries.is_active = 1"
    language = current_language()
    return db().execute(
        f"""
        select countries.*,
               coalesce(local.name, chinese.name, english.name, countries.code) as name,
               coalesce(local.region_name, chinese.region_name, english.region_name, countries.region_code) as region_name
        from countries
        left join country_translations local
          on local.country_code = countries.code and local.language_code = ?
        left join country_translations chinese
          on chinese.country_code = countries.code and chinese.language_code = 'zh-CN'
        left join country_translations english
          on english.country_code = countries.code and english.language_code = 'en'
        {active_clause}
        order by countries.sort_order, name, countries.code
        """,
        (language,),
    ).fetchall()


def country_by_code(code, include_inactive=False):
    clause = "" if include_inactive else "and is_active = 1"
    return db().execute(
        f"select * from countries where code = ? {clause}",
        ((code or "").strip().upper(),),
    ).fetchone()


def country_translations(country_code):
    return db().execute(
        """
        select * from country_translations
        where country_code = ?
        order by case language_code when 'zh-CN' then 0 when 'en' then 1 when 'nl' then 2 else 3 end,
                 language_code
        """,
        (country_code,),
    ).fetchall()


def country_from_form(default_code="US"):
    country = country_by_code(request.form.get("country_code") or default_code, include_inactive=True)
    if not country:
        country = country_by_code(default_code, include_inactive=True)
    return country


def report_location_filters(table_alias="service_orders"):
    region_code = request.args.get("region_code", "").strip().lower()
    country_code = request.args.get("country_code", "").strip().upper()
    countries = country_rows(include_inactive=True)
    valid_country_codes = {country["code"] for country in countries}
    valid_region_codes = {country["region_code"] for country in countries}
    if country_code not in valid_country_codes:
        country_code = ""
    if region_code not in valid_region_codes:
        region_code = ""
    clauses = []
    params = []
    if region_code:
        clauses.append(f"{table_alias}.region_code = ?")
        params.append(region_code)
    if country_code:
        clauses.append(f"{table_alias}.country_code = ?")
        params.append(country_code)
    regions = []
    seen_regions = set()
    for country in countries:
        if country["region_code"] in seen_regions:
            continue
        seen_regions.add(country["region_code"])
        regions.append({"code": country["region_code"], "name": country["region_name"]})
    return region_code, country_code, countries, regions, clauses, params


def seed_settings(connection):
    defaults = {}
    for key, value in DEFAULT_COMPANY_PROFILE.items():
        defaults[f"company_{key}"] = value
    for key, value in DEFAULT_PAYMENT_INSTRUCTIONS.items():
        defaults[f"payment_{key}"] = value
    for key, value in DEFAULT_SMTP_SETTINGS.items():
        defaults[f"smtp_{key}"] = value
    defaults["invoice_terms"] = DEFAULT_INVOICE_TERMS
    defaults["google_maps_browser_api_key"] = GOOGLE_MAPS_BROWSER_API_KEY_ENV
    defaults["google_geocoding_api_key"] = GOOGLE_GEOCODING_API_KEY_ENV
    defaults["google_routes_api_key"] = GOOGLE_ROUTES_API_KEY_ENV
    defaults["google_static_maps_api_key"] = GOOGLE_STATIC_MAPS_API_KEY_ENV
    defaults["deepseek_enabled"] = "false"
    defaults["deepseek_api_key"] = DEEPSEEK_API_KEY_ENV
    defaults["deepseek_model"] = "deepseek-chat"
    defaults["deepseek_vision_model"] = DEEPSEEK_VISION_MODEL_ENV
    defaults["vision_external_api_enabled"] = "true" if VISION_EXTERNAL_API_ENABLED_ENV else "false"
    defaults["vision_max_image_bytes"] = str(VISION_MAX_IMAGE_BYTES_ENV)
    defaults["payroll_cycle_start"] = "2026-07-06"
    defaults["payroll_car_allowance_method"] = "daily"
    defaults["payroll_car_daily_amount"] = "60"
    defaults["payroll_car_mileage_rate"] = "0.5"
    defaults["payroll_meal_allowance_method"] = "daily"
    defaults["payroll_meal_daily_amount"] = "50"
    defaults["payroll_report_writing_fee"] = "20"
    defaults["payroll_lodging_limit"] = "0"
    defaults["payroll_historical_paid_date"] = "2026-07-05"
    defaults["inspection_warning_days"] = "150"
    defaults["inspection_cycle_days"] = "180"
    for key, value in defaults.items():
        connection.execute(
            "insert or ignore into settings (key, value) values (?, ?)",
            (key, value),
        )


def seed_role_permissions(connection):
    timestamp = now()
    for menu_key, roles in DEFAULT_MENU_ROLES.items():
        for role in ROLE_OPTIONS:
            connection.execute(
                """
                insert or ignore into role_menu_permissions (
                    role, menu_key, is_enabled, updated_by, updated_at
                ) values (?, ?, ?, null, ?)
                """,
                (role, menu_key, 1 if role in roles else 0, timestamp),
            )
    for (resource_key, action_key), roles in DEFAULT_ACTION_ROLES.items():
        for role in ROLE_OPTIONS:
            connection.execute(
                """
                insert or ignore into role_action_permissions (
                    role, resource_key, action_key, is_enabled, updated_by, updated_at
                ) values (?, ?, ?, ?, null, ?)
                """,
                (role, resource_key, action_key, 1 if role in roles else 0, timestamp),
            )


def get_setting(key, default=""):
    row = db().execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db().execute(
        """
        insert into settings (key, value) values (?, ?)
        on conflict(key) do update set value = excluded.value
        """,
        (key, value),
    )


def setting_float(key, default=0):
    try:
        return float(get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def setting_int(key, default=0):
    try:
        return int(float(get_setting(key, str(default)) or default))
    except (TypeError, ValueError):
        return int(default)


def inspection_cycle_days():
    return max(1, setting_int("inspection_cycle_days", 180))


def inspection_warning_days():
    return max(1, setting_int("inspection_warning_days", 150))


def positive_int(value, default=1):
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return max(1, int(default))


def payroll_subsidy_settings():
    car_method = get_setting("payroll_car_allowance_method", "daily")
    if car_method not in {"daily", "mileage"}:
        car_method = "daily"
    return {
        "cycle_start": get_setting("payroll_cycle_start", "2026-07-06"),
        "car_allowance_method": car_method,
        "car_daily_amount": setting_float("payroll_car_daily_amount", 60),
        "car_mileage_rate": setting_float("payroll_car_mileage_rate", 0.5),
        "report_writing_fee": setting_float("payroll_report_writing_fee", 20),
        "lodging_limit": setting_float("payroll_lodging_limit", 0),
    }


def lodging_reimbursement_limit():
    return payroll_subsidy_settings()["lodging_limit"]


def is_lodging_project_name(name):
    normalized = (name or "").strip().casefold()
    return "ä½å®¿" in normalized or "lodging" in normalized or "hotel" in normalized


def is_fuel_project_name(name):
    normalized = (name or "").strip().casefold()
    return "æ²¹è´¹" in normalized or "åŠ æ²¹" in normalized or "fuel" in normalized or "gas" in normalized


def validate_lodging_reimbursement(amount, label="ä½å®¿æŠ¥é”€"):
    limit = lodging_reimbursement_limit()
    if limit > 0 and amount > limit:
        raise ValueError(f"{label}ä¸èƒ½è¶…è¿‡ {limit:.2f}ã€‚")


def validate_fuel_reimbursement_allowed(amount, label="æ²¹è´¹æŠ¥é”€"):
    if amount > 0 and payroll_subsidy_settings()["car_allowance_method"] == "mileage":
        raise ValueError(f"å½“å‰è½¦è¡¥æŒ‰é‡Œç¨‹è®¡è´¹ï¼Œ{label}ä¸å¯æŠ¥é”€ã€‚")


def get_timezone_name():
    return get_setting("app_timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE


def app_timezone():
    try:
        return ZoneInfo(get_timezone_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def get_company_profile():
    return {key: get_setting(f"company_{key}", value) for key, value in DEFAULT_COMPANY_PROFILE.items()}


def get_payment_instructions():
    return {key: get_setting(f"payment_{key}", value) for key, value in DEFAULT_PAYMENT_INSTRUCTIONS.items()}


def get_invoice_terms():
    return get_setting("invoice_terms", DEFAULT_INVOICE_TERMS)


def get_smtp_settings():
    return {key: get_setting(f"smtp_{key}", value) for key, value in DEFAULT_SMTP_SETTINGS.items()}


def headquarters_coordinates():
    try:
        latitude = float(HEADQUARTERS_LATITUDE)
        longitude = float(HEADQUARTERS_LONGITUDE)
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None
    return {"latitude": latitude, "longitude": longitude}


def get_google_maps_browser_api_key():
    return get_setting("google_maps_browser_api_key", GOOGLE_MAPS_BROWSER_API_KEY_ENV).strip()


def get_google_geocoding_api_key():
    return get_setting("google_geocoding_api_key", GOOGLE_GEOCODING_API_KEY_ENV).strip()


def get_google_routes_api_key():
    """Server-side Google Routes API key. Never exposed to browser/JS/HTML."""
    return get_setting("google_routes_api_key", GOOGLE_ROUTES_API_KEY_ENV).strip()


def get_google_static_maps_api_key():
    """Server-side Google Static Maps API key. Never exposed to browser/JS/HTML."""
    return get_setting("google_static_maps_api_key", GOOGLE_STATIC_MAPS_API_KEY_ENV).strip()


def save_named_attachment(uploaded, directory, table, owner_column=None, owner_id=None):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"é™„ä»¶åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    os.makedirs(directory, exist_ok=True)
    uploaded.save(os.path.join(directory, stored_filename))
    if owner_column:
        db().execute(
            f"""
            insert into {table} (
                {owner_column}, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (owner_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
        )
    else:
        db().execute(
            f"""
            insert into {table} (
                original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?)
            """,
            (original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
        )


def get_company_attachments():
    return db().execute(
        """
        select company_attachments.*, users.name as uploader_name
        from company_attachments
        left join users on users.id = company_attachments.uploaded_by
        order by uploaded_at desc, id desc
        """
    ).fetchall()


def get_user_attachments(user_id):
    return db().execute(
        """
        select user_attachments.*, users.name as uploader_name
        from user_attachments
        left join users on users.id = user_attachments.uploaded_by
        where user_attachments.user_id = ?
        order by uploaded_at desc, user_attachments.id desc
        """,
        (user_id,),
    ).fetchall()


def assigned_service_order_ids(user_id):
    return {
        row["service_order_id"]
        for row in db().execute(
            "select service_order_id from user_service_orders where user_id = ?",
            (user_id,),
        ).fetchall()
    }


def save_user_service_order_assignments(user_id, role):
    db().execute("delete from user_service_orders where user_id = ?", (user_id,))
    if role != "external_employee":
        return
    order_ids = {int(value) for value in request.form.getlist("service_order_id") if value.isdigit()}
    for order_id in order_ids:
        order = db().execute("select client_id from service_orders where id = ?", (order_id,)).fetchone()
        if order and (not is_external_manager() or order["client_id"] == g.user["client_id"]):
            db().execute(
                """
                insert into user_service_orders (user_id, service_order_id, assigned_by, assigned_at)
                values (?, ?, ?, ?)
                """,
                (user_id, order_id, g.user["id"], now()),
            )


@app.template_filter("nl2br")
def nl2br_filter(value):
    """Escape untrusted text, then render newlines as <br>.

    Address fields are free-text form input, so they must be escaped *before*
    being marked safe. A bare ``|safe`` let a crafted address inject markup
    (stored XSS) into the invoice detail / export pages.
    """
    text = "" if value is None else str(value)
    # str() is required: Markup.replace() escapes its replacement argument,
    # which would turn the intended "<br>" into literal "&lt;br&gt;".
    return Markup(str(escape(text)).replace("\n", "<br>"))


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db().execute(
        """
        select users.*, employee_grades.grade_name as employee_grade_name
        from users
        left join employee_grades on employee_grades.id = users.employee_grade_id
        where users.id = ?
        """,
        (user_id,),
    ).fetchone()
    if user and not user["is_active"]:
        clear_session_preserving_language()
        return None
    return user


@app.after_request
def prevent_stale_business_pages(response):
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.before_request
def load_user():
    requested_language = request.args.get("lang")
    if requested_language in SUPPORTED_LANGUAGES:
        session["language"] = requested_language
    g.user = current_user()
    if g.user:
        permission_rule = required_action_for_request()
        if permission_rule and not has_action_permission(*permission_rule):
            abort(403)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def is_safe_redirect_target(target):
    if not isinstance(target, str):
        return False
    candidate = target.strip()
    if not candidate:
        return False
    for _ in range(3):
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
            return False
        if "\\" in candidate:
            return False
        parsed = urlsplit(candidate)
        if not candidate.startswith("/") or candidate.startswith("//"):
            return False
        if parsed.scheme or parsed.netloc:
            return False
        decoded = unquote(candidate)
        if decoded == candidate:
            return True
        candidate = decoded
    return False


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def is_external_user():
    return g.user and normalized_role() in {"external_manager", "external_employee"}


def is_external_manager():
    return g.user and normalized_role() == "external_manager"


def is_external_employee():
    return g.user and normalized_role() == "external_employee"


def normalized_role(role=None):
    role = role if role is not None else (g.user["role"] if g.user else "")
    if role == "internal":
        return "finance"
    if role == "user":
        return "employee"
    if role == "external":
        return "external_manager"
    return role


def requires_user_address(role):
    return normalized_role(role) not in {"external_manager", "external_employee"}


def is_internal_user():
    return g.user and normalized_role() in {"admin", "manager", "finance", "employee"}


# â”€â”€â”€ Phase 6: AI Daily Report CSRF Protection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def ai_daily_report_csrf_token():
    """Generate/retrieve CSRF token for AI Daily Report Review Center.

    Stored in session, returned to frontend, validated via X-CSRF-Token header.
    Separate from field_token to avoid scope confusion.
    """
    if "ai_daily_report_csrf" not in session:
        session["ai_daily_report_csrf"] = secrets.token_urlsafe(32)
    return session["ai_daily_report_csrf"]


def require_ai_daily_report_csrf():
    """Validate CSRF token for AI Daily Report mutation APIs.

    Checks X-CSRF-Token header against session token.
    Aborts 403 on mismatch.
    GET requests do not require CSRF.
    """
    token = request.headers.get("X-CSRF-Token", "")
    expected = session.get("ai_daily_report_csrf", "")
    if not expected or not secrets.compare_digest(token, expected):
        abort(403, description="CSRF token missing or invalid")


# â”€â”€â”€ Phase 6: AI Daily Report Authorization Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def can_view_ai_daily_report_draft(user, draft_row):
    """Check if user can view an AI Daily Report Draft.

    Rules:
    - admin/manager/finance: can view all drafts
    - employee: can view drafts where they are a worker, or drafts they created
    - external users: cannot view

    Args:
        user: current user (sqlite3.Row or dict)
        draft_row: draft database row (must include created_by, draft_data)

    Returns:
        bool: True if user can view
    """
    if not user or not draft_row:
        return False

    # Convert sqlite3.Row to dict if needed
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)

    role = user.get("role", "")
    user_id = user.get("id", "")

    # Admin/manager/finance can view all
    if role in {"admin", "manager", "finance"}:
        return True

    # Employee can view drafts they created
    if role == "employee" and draft_row.get("created_by") == user_id:
        return True

    # Employee can view drafts where they are listed as a worker
    if role == "employee":
        try:
            import json
            draft_data = json.loads(draft_row.get("draft_data", "{}") or "{}")
            workers = draft_data.get("workers", [])
            for w in workers:
                if w.get("user_id") == user_id:
                    return True
        except (json.JSONDecodeError, TypeError):
            pass

    return False


def can_edit_ai_daily_report_draft(user, draft_row):
    """Check if user can edit an AI Daily Report Draft.

    Rules:
    - admin/manager: can edit all drafts
    - finance: can view but NOT edit (read-only for finance)
    - employee: can edit drafts they created or where they are a worker
    - Draft must be in 'draft' status (confirmed/cancelled/saved are read-only)

    Args:
        user: current user (sqlite3.Row or dict)
        draft_row: draft database row

    Returns:
        bool: True if user can edit
    """
    if not user or not draft_row:
        return False

    # Convert sqlite3.Row to dict if needed
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)

    # Status check: only 'draft' status can be edited
    status = draft_row.get("status", "")
    if status != "draft":
        return False

    role = user.get("role", "")

    # Admin/manager can edit all
    if role in {"admin", "manager"}:
        return True

    # Finance is read-only
    if role == "finance":
        return False

    # Employee can edit drafts they created or where they are a worker
    if role == "employee":
        return can_view_ai_daily_report_draft(user, draft_row)

    return False


def can_prepare_attachment_manifest(user, draft_row):
    """Phase 8-specific authorization for Prepare Attachments (mutation).

    Does NOT reuse can_edit_ai_daily_report_draft (confirmed drafts are not
    editable there). Rules:
    - must satisfy can_view_ai_daily_report_draft (participant/admin/manager/
      external-admin client scope inherited from Phase 6)
    - finance is read-only: never allowed to prepare
    - draft must be status == 'confirmed' (chain B state machine)
    """
    if not user or not draft_row:
        return False
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)
    role = user.get("role", "")
    if role == "finance":
        return False
    if not can_view_ai_daily_report_draft(user, draft_row):
        return False
    if draft_row.get("status") != "confirmed":
        return False
    return True


def can_formal_save_draft(user, draft_row):
    """Phase 9-specific authorization for Formal Save (mutation).

    Rules (sealed design):
    - must satisfy can_view_ai_daily_report_draft (Phase 6 client scope)
    - finance read-only: never allowed to formal save
    - external_manager / external_employee: keep current Phase 6-8 boundary
    - draft must be status == 'confirmed'
    """
    if not user or not draft_row:
        return False
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)
    role = user.get("role", "")
    if role == "finance":
        return False
    if not can_view_ai_daily_report_draft(user, draft_row):
        return False
    if draft_row.get("status") != "confirmed":
        return False
    return True


def ai_daily_report_draft_list_filters(user):
    """Return SQL WHERE clauses and params for draft list, filtered by user permission.

    Does NOT query all then filter in Python - filters at SQL level.

    Permission rules (same as can_view_ai_daily_report_draft):
    - admin/manager/finance: see all drafts
    - employee: see drafts they created OR where they are listed as a worker (by user_id)
    - external users: see nothing

    Worker matching uses SQLite JSON1 json_each to check draft_data.workers[].user_id.
    This is stable identity matching (user_id), NOT name matching, to prevent
    same-name employees from gaining unauthorized access.
    """
    if not user:
        return ["1=0"], []

    # Convert sqlite3.Row to dict (sqlite3.Row does NOT support attribute access)
    if hasattr(user, "keys"):
        user = dict(user)

    role = user.get("role", "")
    user_id = user.get("id", "")

    # Admin/manager/finance see all
    if role in {"admin", "manager", "finance"}:
        return ["1=1"], []

    # Employee sees drafts they created OR where they are a worker (by stable user_id)
    if role == "employee":
        # Use SQLite JSON1 json_each to check workers[].user_id
        # This prevents same-name employees from gaining access (name-based matching is unsafe)
        worker_clause = """EXISTS (
            SELECT 1 FROM json_each(ai_daily_report_drafts.draft_data, '$.workers') w
            WHERE json_extract(w.value, '$.user_id') = ?
        )"""
        return [f"(created_by = ? OR {worker_clause})"], [user_id, user_id]

    return ["1=0"], []


def is_manager():
    return g.user and normalized_role() in {"admin", "manager"}


def can_approve_users():
    return g.user and has_action_permission("users", "approve")


def can_view_invoices():
    return g.user and has_action_permission("invoices", "view")


def can_view_contracts():
    return g.user and has_action_permission("contracts", "view")


def can_manage_contracts():
    return g.user and (
        has_action_permission("contracts", "create")
        or has_action_permission("contracts", "edit")
        or has_action_permission("contracts", "delete")
    )


def can_create_invoice():
    return g.user and has_action_permission("invoices", "create")


def can_create_service_order():
    return g.user and has_action_permission("service_orders", "create")


def can_delete_service_order():
    return g.user and has_action_permission("service_orders", "delete")


def can_create_expense():
    return g.user and has_action_permission("expenses", "create")


def can_manage_customer_reimbursement():
    return g.user and (
        has_action_permission("customer_reimbursements", "create")
        or has_action_permission("customer_reimbursements", "edit")
        or has_action_permission("customer_reimbursements", "delete")
        or has_action_permission("customer_reimbursements", "approve")
    )


# v0.1.239: å·¥å•ç»“ç®—çš„ã€Œä¿®æ”¹çŠ¶æ€ã€ã€Œå®¡æ ¸ã€ã€Œåˆ é™¤ã€æ”¹ä¸ºå®Œå…¨ç”±èœå•æƒé™é…ç½®å†³å®š
# ï¼ˆæƒé™ç®¡ç†é¡µé¢å¯å‹¾é€‰ï¼‰ï¼Œé»˜è®¤è´¢åŠ¡/ç»ç†/ç®¡ç†å‘˜å‡æœ‰æƒé™ã€‚
def can_approve_customer_reimbursement():
    return g.user and has_action_permission("customer_reimbursements", "approve")


def can_reset_customer_reimbursement():
    return g.user and has_action_permission("customer_reimbursements", "reset")


def can_delete_customer_reimbursement():
    return g.user and has_action_permission("customer_reimbursements", "delete")


def can_transfer_expense_attachment(expense):
    if not g.user:
        return False
    if has_action_permission("customer_reimbursements", "edit"):
        return True
    return (
        expense["status"] in {"draft", "returned"}
        and expense["created_by"] == g.user["id"]
        and has_action_permission("expenses", "edit")
    )


def can_manage_employee_grades():
    return g.user and (
        has_action_permission("employee_grades", "create")
        or has_action_permission("employee_grades", "edit")
        or has_action_permission("payroll_subsidies", "edit")
    )


def can_view_labor_payroll_reports():
    return g.user and (
        has_action_permission("labor_hours_report", "view")
        or has_action_permission("payroll_report", "view")
        or has_action_permission("payroll_calendar", "view")
    )


def can_view_customer_reimbursement():
    return g.user and has_action_permission("customer_reimbursements", "view")


def can_create_service_report(order=None):
    if not g.user:
        return False
    if not has_action_permission("service_reports", "create"):
        return False
    if not is_external_user():
        return True
    if is_external_employee() and order is not None:
        return can_access_service_order(order)
    return False


def can_manage_users():
    return g.user and not is_external_user() and (
        has_action_permission("users", "create")
        or has_action_permission("users", "edit")
        or has_action_permission("users", "delete")
    )


def can_manage_company_info():
    return g.user and has_action_permission("company_info", "edit")


def can_assign_external_employees():
    return can_manage_users() or is_external_manager()


def can_manage_user_record(user):
    if can_manage_users() or user["id"] == g.user["id"]:
        return True
    return (
        is_external_manager()
        and normalized_role(user["role"]) == "external_employee"
        and user["client_id"] == g.user["client_id"]
    )


def can_manage_buyers():
    return g.user and (
        has_action_permission("buyers", "view")
        or has_action_permission("buyers", "create")
        or has_action_permission("buyers", "edit")
        or has_action_permission("buyers", "delete")
    )


def can_manage_owners():
    return g.user and (
        has_action_permission("owners", "view")
        or has_action_permission("owners", "create")
        or has_action_permission("owners", "edit")
        or has_action_permission("owners", "delete")
    )


def can_manage_manufacturers():
    return g.user and (
        has_action_permission("manufacturers", "view")
        or has_action_permission("manufacturers", "create")
        or has_action_permission("manufacturers", "edit")
        or has_action_permission("manufacturers", "delete")
    )


def can_access_buyer(buyer):
    if g.user and can_manage_buyers() and not is_external_user():
        return True
    return is_external_manager() and bool(g.user["client_id"]) and buyer["client_id"] == g.user["client_id"]


def can_view_audit_logs():
    return g.user and has_action_permission("audit_logs", "view")


def can_use_database_console():
    return g.user and has_action_permission("database_console", "view")


def menu_permission_overrides():
    if not hasattr(g, "_menu_permission_overrides"):
        rows = db().execute("select role, menu_key, is_enabled from role_menu_permissions").fetchall()
        g._menu_permission_overrides = {
            (row["role"], row["menu_key"]): bool(row["is_enabled"])
            for row in rows
        }
    return g._menu_permission_overrides


def has_menu_permission(menu_key, role=None):
    if not g.user:
        return False
    role = normalized_role(role)
    if role not in ROLE_OPTIONS:
        return False
    overrides = menu_permission_overrides()
    override_key = (role, menu_key)
    action_overrides = action_permission_overrides()
    if any(
        enabled
        for (permission_role, resource_key, _action_key), enabled in action_overrides.items()
        if permission_role == role and resource_key == menu_key
    ):
        return True
    if override_key in overrides:
        return overrides[override_key]
    if any(
        role in roles
        for (resource_key, _action_key), roles in DEFAULT_ACTION_ROLES.items()
        if resource_key == menu_key
    ):
        return True
    return role in DEFAULT_MENU_ROLES.get(menu_key, set())


def action_permission_overrides():
    if not hasattr(g, "_action_permission_overrides"):
        rows = db().execute("select role, resource_key, action_key, is_enabled from role_action_permissions").fetchall()
        g._action_permission_overrides = {
            (row["role"], row["resource_key"], row["action_key"]): bool(row["is_enabled"])
            for row in rows
        }
    return g._action_permission_overrides


def has_action_permission(resource_key, action_key, role=None):
    if not g.user:
        return False
    role = normalized_role(role)
    if role not in ROLE_OPTIONS:
        return False
    overrides = action_permission_overrides()
    override_key = (role, resource_key, action_key)
    menu_overrides = menu_permission_overrides()
    if action_key == "view" and menu_overrides.get((role, resource_key), False):
        return True
    if action_key == "view" and any(
        enabled
        for (permission_role, permission_resource, permission_action), enabled in overrides.items()
        if permission_role == role
        and permission_resource == resource_key
        and permission_action != "view"
    ):
        return True
    if override_key in overrides:
        return overrides[override_key]
    if action_key == "view" and role in DEFAULT_MENU_ROLES.get(resource_key, set()):
        return True
    if action_key == "view" and any(
        role in roles
        for (permission_resource, permission_action), roles in DEFAULT_ACTION_ROLES.items()
        if permission_resource == resource_key and permission_action != "view"
    ):
        return True
    return role in DEFAULT_ACTION_ROLES.get((resource_key, action_key), set())


def required_action_for_request():
    endpoint = request.endpoint or ""
    method = request.method
    if endpoint == "edit_user" and request.view_args and request.view_args.get("user_id") == g.user["id"]:
        return None
    method_rules = {
        ("clients", "GET"): ("clients", "view"),
        ("clients", "POST"): ("clients", "create"),
        ("owners", "GET"): ("owners", "view"),
        ("owners", "POST"): ("owners", "create"),
        ("manufacturers", "GET"): ("manufacturers", "view"),
        ("manufacturers", "POST"): ("manufacturers", "create"),
        ("buyers", "GET"): ("buyers", "view"),
        ("buyers", "POST"): ("buyers", "create"),
        ("work_order_types", "GET"): ("work_order_types", "view"),
        ("work_order_types", "POST"): ("work_order_types", "create"),
        ("projects", "GET"): ("projects", "view"),
        ("projects", "POST"): ("projects", "create"),
        ("countries", "GET"): ("countries", "view"),
        ("countries", "POST"): ("countries", "edit"),
        ("employee_grades", "GET"): ("employee_grades", "view"),
        ("employee_grades", "POST"): ("employee_grades", "edit"),
        ("payroll_subsidies", "GET"): ("payroll_subsidies", "view"),
        ("payroll_subsidies", "POST"): ("payroll_subsidies", "edit"),
        ("payment_terms", "GET"): ("payment_terms", "view"),
        ("payment_terms", "POST"): ("payment_terms", "create"),
        ("users", "GET"): ("users", "view"),
        ("users", "POST"): ("users", "create"),
        ("system_settings", "GET"): ("system_settings", "view"),
        ("system_settings", "POST"): ("system_settings", "edit"),
        ("deepseek_connection_test", "POST"): ("system_settings", "edit"),
        ("database_console", "GET"): ("database_console", "view"),
        ("database_console", "POST"): ("database_console", "execute"),
        ("company_info", "GET"): ("company_info", "view"),
        ("company_info", "POST"): ("company_info", "edit"),
        ("knowledge_base", "GET"): ("knowledge_base", "view"),
        ("knowledge_base", "POST"): ("knowledge_base", "create"),
        ("ai_assistant", "GET"): ("ai_assistant", "view"),
        ("ai_assistant_chat", "POST"): ("ai_assistant", "view"),
        ("customer_reimbursement_form", "GET"): ("customer_reimbursements", "view"),
        ("customer_reimbursement_form", "POST"): ("customer_reimbursements", "edit"),
    }
    endpoint_rules = {
        "contracts": ("contracts", "view"),
        "new_contract": ("contracts", "create"),
        "contract_detail": ("contracts", "view"),
        "edit_contract": ("contracts", "edit"),
        "delete_contract": ("contracts", "delete"),
        "delete_contract_attachment": ("contracts", "delete"),
        "edit_client": ("clients", "edit"),
        "delete_client": ("clients", "delete"),
        "edit_owner": ("owners", "edit"),
        "delete_owner": ("owners", "delete"),
        "edit_manufacturer": ("manufacturers", "edit"),
        "delete_manufacturer": ("manufacturers", "delete"),
        "edit_buyer": ("buyers", "edit"),
        "delete_buyer": ("buyers", "delete"),
        "import_buyers": ("buyers", "create"),
        "edit_work_order_type": ("work_order_types", "edit"),
        "delete_work_order_type": ("work_order_types", "delete"),
        "edit_project": ("projects", "edit"),
        "delete_project": ("projects", "delete"),
        "edit_payment_term": ("payment_terms", "edit"),
        "toggle_payment_term": ("payment_terms", "edit"),
        "recalculate_payment_term_invoices": ("payment_terms", "edit"),
        "edit_user": ("users", "edit"),
        "update_user_status": ("users", "approve"),
        "delete_user": ("users", "delete"),
        "delete_user_attachment": ("users", "edit"),
        "delete_company_attachment": ("company_info", "edit"),
        "preview_knowledge_document": ("knowledge_base", "view"),
        "download_knowledge_document": ("knowledge_base", "view"),
        "edit_knowledge_document": ("knowledge_base", "edit"),
        "upload_knowledge_document_version": ("knowledge_base", "edit"),
        "preview_knowledge_document_version": ("knowledge_base", "view"),
        "download_knowledge_document_version": ("knowledge_base", "view"),
        "delete_knowledge_document": ("knowledge_base", "delete"),
        "service_orders": ("service_orders", "view"),
        "new_service_order": ("service_orders", "create"),
        "edit_service_order": ("service_orders", "edit"),
        "service_order_detail": ("service_orders", "view"),
        "delete_service_order": ("service_orders", "delete"),
        "service_order_map": ("service_orders", "view"),
        "service_order_calendar": ("service_order_calendar", "view"),
        "service_report_query": ("service_reports", "view"),
        "view_service_report": ("service_reports", "view"),
        "new_service_report": ("service_reports", "create"),
        "edit_service_report": ("service_reports", "edit"),
        "delete_service_report": ("service_reports", "delete"),
        "export_service_report": ("service_reports", "export"),
        "delete_report_attachment": ("service_reports", "edit"),
        "expense_processing": ("expenses", "view"),
        "expense_query": ("expenses", "view"),
        "new_expense": ("expenses", "create"),
        "edit_expense": ("expenses", "edit"),
        "expense_detail": ("expenses", "view"),
        "approve_expense": ("expenses", "approve"),
        "return_expense": ("expenses", "approve"),
        "delete_expense": ("expenses", "delete"),
        "delete_expense_attachment": ("expenses", "edit"),
        "invoices": ("invoices", "view"),
        "invoice_query": ("invoices", "view"),
        "new_invoice": ("invoices", "create"),
        "edit_invoice": ("invoices", "edit"),
        "invoice_detail": ("invoices", "view"),
        "delete_invoice": ("invoices", "delete"),
        "send_invoice": ("invoices", "send"),
        "mark_invoice_paid": ("invoices", "pay"),
        "unmark_invoice_paid": ("invoices", "pay"),
        "admin_update_invoice_status": ("invoices", "edit"),
        "void_invoice": ("invoices", "edit"),
        "export_invoice": ("invoices", "export"),
        "export_invoice_pdf": ("invoices", "export"),
        "delete_attachment": ("invoices", "edit"),
        "customer_reimbursement_query": ("customer_reimbursements", "view"),
        "download_customer_reimbursement": ("customer_reimbursements", "export"),
        "download_customer_reimbursement_excel": ("customer_reimbursements", "export"),
        "preview_customer_reimbursement": ("customer_reimbursements", "export"),
        "approve_customer_reimbursement": ("customer_reimbursements", "approve"),
        "return_customer_reimbursement": ("customer_reimbursements", "approve"),
        "reset_customer_reimbursement": ("customer_reimbursements", "reset"),
        "delete_customer_reimbursement": ("customer_reimbursements", "delete"),
        "delete_customer_reimbursement_attachment": ("customer_reimbursements", "edit"),
        "labor_hours_report": ("labor_hours_report", "view"),
        "payroll_report": ("payroll_report", "view"),
        "payroll_detail_report": ("payroll_report", "view"),
        "payroll_calendar": ("payroll_calendar", "view"),
        "payroll_calendar_batch": ("payroll_calendar", "view"),
        "payroll_calendar_export": ("payroll_calendar", "export"),
    }
    return method_rules.get((endpoint, method)) or endpoint_rules.get(endpoint)


def can_access_client(client_id):
    if not g.user:
        return False
    if is_internal_user():
        return True
    return is_external_manager() and g.user["client_id"] == client_id


def require_invoice_access(invoice_id):
    if not can_view_invoices():
        abort(403)
    invoice = db().execute("select * from invoices where id = ?", (invoice_id,)).fetchone()
    if not invoice:
        abort(404)
    if not can_access_client(invoice["client_id"]):
        abort(403)
    return invoice


def require_contract_access(contract_id):
    if not can_view_contracts():
        abort(403)
    contract = db().execute("select * from contracts where id = ?", (contract_id,)).fetchone()
    if not contract:
        abort(404)
    if not can_access_client(contract["client_id"]):
        abort(403)
    return contract


def client_filter_clause(alias="invoices"):
    if is_external_manager():
        if not g.user["client_id"]:
            return "1 = 0", []
        return f"{alias}.client_id = ?", [g.user["client_id"]]
    if is_external_employee():
        return (
            f"{alias}.service_order_id in (select service_order_id from user_service_orders where user_id = ?)",
            [g.user["id"]],
        )
    return "1 = 1", []


def role_label(role):
    labels = {
        "admin": "ç®¡ç†å‘˜",
        "manager": "ç»ç†",
        "finance": "è´¢åŠ¡",
        "employee": "å‘˜å·¥",
        "user": "å‘˜å·¥",
        "internal": "è´¢åŠ¡",
        "external": "å¤–éƒ¨ç®¡ç†å‘˜",
        "external_manager": "å¤–éƒ¨ç®¡ç†å‘˜",
        "external_employee": "å¤–éƒ¨å‘˜å·¥",
    }
    return labels.get(role, role)


def money(value, currency="USD"):
    symbols = {"USD": "$", "CNY": "Â¥", "EUR": "â‚¬", "GBP": "Â£", "JPY": "Â¥"}
    amount = float(value or 0)
    if currency == "JPY":
        return f"{symbols.get(currency, currency + ' ')}{amount:,.0f}"
    return f"{symbols.get(currency, currency + ' ')}{amount:,.2f}"


PROJECT_COLORS = ["#0f766e", "#175cd3", "#b42318", "#7a271a", "#6941c6", "#027a48", "#b54708", "#3538cd"]


def project_color(project_id):
    return PROJECT_COLORS[int(project_id or 0) % len(PROJECT_COLORS)]


def pdf_text(value):
    return "" if value is None else str(value)


def to_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


CENT = Decimal("0.01")


def decimal_value(value, default="0"):
    try:
        return Decimal(str(value if value is not None and value != "" else default))
    except (InvalidOperation, ValueError):
        return Decimal(str(default))


def money_decimal(value):
    amount = decimal_value(value)
    if abs(amount) < CENT:
        amount = Decimal("0")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money_float(value):
    return float(money_decimal(value))


def invoice_totals(invoice_id):
    rows = db().execute("select amount, tax_rate from invoice_items where invoice_id = ?", (invoice_id,)).fetchall()
    subtotal = sum(float(row["amount"] or 0) for row in rows)
    tax = sum(float(row["amount"] or 0) * float(row["tax_rate"] or 0) / 100 for row in rows)
    return {"subtotal": subtotal, "tax": tax, "total": max(subtotal + tax, 0)}


def payment_label(invoice):
    if invoice["status"] == "void":
        return "ä¸é€‚ç”¨"
    if invoice["paid_at"]:
        return "å·²æ ¸é”€"
    if invoice["status"] == "completed":
        return "å¾…æ ¸é”€"
    return "æµç¨‹ä¸­"


def local_datetime(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)[:16].replace("T", " ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(app_timezone()).strftime("%Y-%m-%d %H:%M")


def sql_statement_kind(sql):
    stripped = (sql or "").lstrip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def is_read_sql(sql):
    return sql_statement_kind(sql) in {"select", "with", "pragma", "explain"}


def client_order_number_warning(customer_name, client_order_number):
    """Return a warning for the current Sanhe Tongfei work-order number convention."""
    normalized_customer = (customer_name or "").strip().casefold()
    if "ä¸‰æ²³åŒé£" not in normalized_customer and "sanhe tongfei" not in normalized_customer:
        return ""
    number = (client_order_number or "").strip()
    if not number:
        return "æœªå¡«å†™æœåŠ¡è®¢å•å·ç ï¼Œæ— æ³•æŒ‰ä¸‰æ²³åŒé£è§„åˆ™è¯†åˆ«ã€‚"
    if not number.startswith("SHPG") and len(number) < 16:
        return "æœåŠ¡è®¢å•å·ç åº”ä»¥ SHPG å¼€å¤´ä¸”æ€»é•¿åº¦ä¸å°‘äº16ä½ï¼Œå½“å‰æ ¼å¼ä¸ç¬¦åˆä¸‰æ²³åŒé£è§„åˆ™ã€‚"
    if not number.startswith("SHPG"):
        return "æœåŠ¡è®¢å•å·ç åº”ä»¥ SHPG å¼€å¤´ï¼Œå½“å‰æ ¼å¼ä¸ç¬¦åˆä¸‰æ²³åŒé£è§„åˆ™ã€‚"
    if len(number) < 16:
        return f"æœåŠ¡è®¢å•å·ç æ€»é•¿åº¦åº”ä¸å°‘äº16ä½ï¼Œå½“å‰ä¸º{len(number)}ä½ã€‚"
    return ""


def is_image_attachment(content_type, filename):
    if (content_type or "").strip().casefold().startswith("image/"):
        return True
    name = (filename or "").strip().casefold()
    return "." in name and name.rsplit(".", 1)[1] in ALLOWED_IMAGE_EXTENSIONS


def is_customer_reimbursement_image_attachment(attachment):
    if is_image_attachment(attachment["content_type"], attachment["original_filename"]):
        return True
    return valid_image_file(customer_reimbursement_attachment_path(attachment))


app.jinja_env.filters["money"] = money
app.jinja_env.filters["role_label"] = role_label
app.jinja_env.filters["payment_label"] = payment_label
app.jinja_env.filters["local_datetime"] = local_datetime
app.jinja_env.globals["can_view_invoices"] = can_view_invoices
app.jinja_env.globals["can_create_invoice"] = can_create_invoice
app.jinja_env.globals["can_view_contracts"] = can_view_contracts
app.jinja_env.globals["can_manage_contracts"] = can_manage_contracts
app.jinja_env.globals["can_create_service_order"] = can_create_service_order
app.jinja_env.globals["can_delete_service_order"] = can_delete_service_order
app.jinja_env.globals["can_create_expense"] = can_create_expense
app.jinja_env.globals["can_manage_customer_reimbursement"] = can_manage_customer_reimbursement
app.jinja_env.globals["is_customer_reimbursement_image_attachment"] = is_customer_reimbursement_image_attachment
app.jinja_env.globals["can_manage_employee_grades"] = can_manage_employee_grades
app.jinja_env.globals["can_view_labor_payroll_reports"] = can_view_labor_payroll_reports
app.jinja_env.globals["can_view_customer_reimbursement"] = can_view_customer_reimbursement
app.jinja_env.globals["can_create_service_report"] = can_create_service_report
app.jinja_env.globals["is_external_manager"] = is_external_manager
app.jinja_env.globals["is_external_employee"] = is_external_employee
app.jinja_env.globals["is_internal_user"] = is_internal_user
app.jinja_env.globals["can_assign_external_employees"] = can_assign_external_employees
app.jinja_env.globals["can_approve_users"] = can_approve_users
app.jinja_env.globals["can_view_audit_logs"] = can_view_audit_logs
app.jinja_env.globals["can_use_database_console"] = can_use_database_console
app.jinja_env.globals["has_menu_permission"] = has_menu_permission
app.jinja_env.globals["has_action_permission"] = has_action_permission
app.jinja_env.globals["normalized_role"] = normalized_role
app.jinja_env.globals["requires_user_address"] = requires_user_address
app.jinja_env.globals["client_order_number_warning"] = client_order_number_warning
app.jinja_env.globals["is_image_attachment"] = is_image_attachment
app.jinja_env.globals["is_fuel_project_name"] = is_fuel_project_name
app.jinja_env.globals["is_lodging_project_name"] = is_lodging_project_name
app.jinja_env.globals["lodging_reimbursement_limit"] = lodging_reimbursement_limit
app.jinja_env.globals["expense_labels"] = EXPENSE_STATUS_LABELS
app.jinja_env.globals["expense_payout_labels"] = EXPENSE_PAYOUT_LABELS
app.jinja_env.globals["customer_reimbursement_labels"] = CUSTOMER_REIMBURSEMENT_STATUS_LABELS
app.jinja_env.globals["project_type_labels"] = PROJECT_TYPE_LABELS
app.jinja_env.globals["contract_type_labels"] = CONTRACT_TYPE_LABELS
app.jinja_env.globals["contract_status_labels"] = CONTRACT_STATUS_LABELS


def next_client_number():
    row = db().execute(
        """
        select client_number from clients
        where client_number glob '[0-9][0-9][0-9][0-9][0-9]'
        order by client_number desc limit 1
        """
    ).fetchone()
    return "00001" if not row else f"{int(row['client_number']) + 1:05d}"


def next_buyer_number():
    row = db().execute(
        """
        select buyer_number from buyers
        where buyer_number glob 'BUY[0-9][0-9][0-9][0-9][0-9]'
        order by buyer_number desc limit 1
        """
    ).fetchone()
    return "BUY00001" if not row else f"BUY{int(row['buyer_number'][3:]) + 1:05d}"


def next_owner_number():
    row = db().execute(
        """
        select owner_number from owners
        where owner_number glob 'OWN[0-9][0-9][0-9][0-9][0-9]'
        order by owner_number desc limit 1
        """
    ).fetchone()
    return "OWN00001" if not row else f"OWN{int(row['owner_number'][3:]) + 1:05d}"


def next_manufacturer_number():
    row = db().execute(
        """
        select manufacturer_number from manufacturers
        where manufacturer_number glob 'MFG[0-9][0-9][0-9][0-9][0-9]'
        order by manufacturer_number desc limit 1
        """
    ).fetchone()
    return "MFG00001" if not row else f"MFG{int(row['manufacturer_number'][3:]) + 1:05d}"


def unknown_owner():
    owner = db().execute("select * from owners where name = ?", ("æœªçŸ¥",)).fetchone()
    if owner:
        return owner
    cursor = db().execute(
        "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
        (next_owner_number(), "æœªçŸ¥", now()),
    )
    return db().execute("select * from owners where id = ?", (cursor.lastrowid,)).fetchone()


def owner_options():
    return db().execute(
        """
        select id, owner_number, name from owners
        order by case when name = 'æœªçŸ¥' then 0 else 1 end, owner_number
        """
    ).fetchall()


def manufacturer_options():
    return db().execute(
        """
        select id, manufacturer_number, name from manufacturers
        order by manufacturer_number
        """
    ).fetchall()


def manufacturer_from_form():
    manufacturer_id = (
        int(request.form["manufacturer_id"])
        if request.form.get("manufacturer_id", "").isdigit()
        else None
    )
    if not manufacturer_id:
        return None
    return db().execute("select * from manufacturers where id = ?", (manufacturer_id,)).fetchone()


def manufacturer_by_name(name):
    clean_name = " ".join(str(name or "").strip().split())
    if not clean_name:
        return None
    return db().execute(
        "select * from manufacturers where lower(trim(name)) = lower(trim(?))",
        (clean_name,),
    ).fetchone()


BUYER_IMPORT_FIELD_ALIASES = {
    "buyer_number": {"ç¼–å·", "ç«™ç‚¹ç¼–å·", "å®¢æˆ·ç¼–å·", "buyer_number", "site_number", "number"},
    "name": {"ç«™ç‚¹åç§°", "ç«™ç‚¹", "åç§°", "name", "site", "site_name"},
    "owner": {"ä¸šä¸»", "ä¸šä¸»åç§°", "owner", "owner_name"},
    "country_code": {"å›½å®¶", "å›½å®¶ä»£ç ", "country", "country_code"},
    "contact_name": {"è”ç³»äºº", "contact", "contact_name"},
    "contact_details": {"è”ç³»æ–¹å¼", "è”ç³»ç”µè¯", "ç”µè¯", "contact_details", "phone", "tel"},
    "email": {"ç”µå­é‚®ç®±", "ç”µå­é‚®ç®±åœ°å€", "é‚®ç®±", "email"},
    "site_size": {"è§„æ¨¡", "site_size", "scale", "size"},
    "equipment_manufacturer": {"é…å¥—æœºå‚å®¶", "é…å¥—å‚å®¶", "å‚å®¶", "equipment_manufacturer", "manufacturer"},
    "detailed_address": {"è¯¦ç»†åœ°å€", "åœ°å€", "ç«™ç‚¹åœ°å€", "detailed_address", "address", "site_address"},
}


def normalize_import_header(value):
    return re.sub(r"[\s_ï¼ˆï¼‰()ï¼š:.-]+", "", str(value or "").strip()).casefold()


BUYER_IMPORT_HEADER_LOOKUP = {
    normalize_import_header(alias): field
    for field, aliases in BUYER_IMPORT_FIELD_ALIASES.items()
    for alias in aliases
}


def get_or_create_owner_by_name(owner_name):
    name = " ".join(str(owner_name or "").strip().split()) or "æœªçŸ¥"
    row = db().execute("select * from owners where lower(trim(name)) = lower(trim(?))", (name,)).fetchone()
    if row:
        return row, False
    cursor = db().execute(
        "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
        (next_owner_number(), name, now()),
    )
    return db().execute("select * from owners where id = ?", (cursor.lastrowid,)).fetchone(), True


def country_from_import_value(value, default_code="US"):
    text = str(value or "").strip()
    if text:
        country = country_by_code(text, include_inactive=True)
        if country:
            return country
        country = db().execute(
            """
            select countries.*
            from countries
            left join country_translations on country_translations.country_code = countries.code
            where lower(country_translations.name) = lower(?)
            limit 1
            """,
            (text,),
        ).fetchone()
        if country:
            return country
    return country_by_code(default_code, include_inactive=True)


def imported_buyer_rows(uploaded_file):
    filename = secure_filename(uploaded_file.filename or "")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content = uploaded_file.read()
    if not content:
        raise ValueError("å¯¼å…¥æ–‡ä»¶ä¸ºç©ºã€‚")
    if extension == "csv":
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                text = ""
        if not text:
            raise ValueError("CSV æ–‡ä»¶ç¼–ç æ— æ³•è¯†åˆ«ï¼Œè¯·ä½¿ç”¨ UTF-8 æˆ– GB18030ã€‚")
        rows = list(csv.DictReader(text.splitlines()))
    elif extension == "xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise ValueError("æœåŠ¡å™¨æœªå®‰è£… Excel å¯¼å…¥ç»„ä»¶ï¼Œè¯·å…ˆå®‰è£… openpyxlã€‚") from error
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        worksheet = workbook.active
        values = list(worksheet.iter_rows(values_only=True))
        if not values:
            return []
        headers = [str(cell or "").strip() for cell in values[0]]
        rows = [
            {headers[index]: cell for index, cell in enumerate(row) if index < len(headers)}
            for row in values[1:]
        ]
    else:
        raise ValueError("ä»…æ”¯æŒå¯¼å…¥ .xlsx æˆ– .csv æ–‡ä»¶ã€‚")
    normalized_rows = []
    for source_row in rows:
        normalized = {}
        for header, value in source_row.items():
            field = BUYER_IMPORT_HEADER_LOOKUP.get(normalize_import_header(header))
            if field:
                normalized[field] = "" if value is None else str(value).strip()
        if any(normalized.values()):
            normalized_rows.append(normalized)
    return normalized_rows


def import_buyers_from_file(uploaded_file, client_id=None):
    rows = imported_buyer_rows(uploaded_file)
    result = {
        "total": len(rows),
        "created": 0,
        "skipped": 0,
        "errors": 0,
        "owners_created": 0,
        "details": [],
    }
    seen_keys = set()
    for index, row in enumerate(rows, start=2):
        name = row.get("name", "").strip()
        detailed_address = row.get("detailed_address", "").strip()
        buyer_number = row.get("buyer_number", "").strip().upper()
        if not name or not detailed_address:
            result["errors"] += 1
            result["details"].append(f"ç¬¬ {index} è¡Œï¼šç¼ºå°‘ç«™ç‚¹åç§°æˆ–è¯¦ç»†åœ°å€ï¼Œæœªå¯¼å…¥ã€‚")
            continue
        duplicate_key = (client_id or 0, normalized_address(name), normalized_address(detailed_address))
        if duplicate_key in seen_keys:
            result["skipped"] += 1
            result["details"].append(f"ç¬¬ {index} è¡Œï¼š{name} åœ¨æœ¬æ¬¡æ–‡ä»¶ä¸­é‡å¤ï¼Œå·²è·³è¿‡ã€‚")
            continue
        seen_keys.add(duplicate_key)
        existing_site = db().execute(
            """
            select buyer_number from buyers
            where coalesce(client_id, 0) = coalesce(?, 0)
              and lower(trim(name)) = lower(trim(?))
              and lower(trim(detailed_address)) = lower(trim(?))
            """,
            (client_id, name, detailed_address),
        ).fetchone()
        if existing_site:
            result["skipped"] += 1
            result["details"].append(f"ç¬¬ {index} è¡Œï¼š{name} å·²å­˜åœ¨ï¼ˆ{existing_site['buyer_number']}ï¼‰ï¼Œå·²è·³è¿‡ã€‚")
            continue
        if buyer_number and db().execute("select id from buyers where buyer_number = ?", (buyer_number,)).fetchone():
            result["skipped"] += 1
            result["details"].append(f"ç¬¬ {index} è¡Œï¼šç¼–å· {buyer_number} å·²å­˜åœ¨ï¼Œå·²è·³è¿‡ã€‚")
            continue
        owner, owner_created = get_or_create_owner_by_name(row.get("owner"))
        country = country_from_import_value(row.get("country_code"), "US")
        manufacturer = manufacturer_by_name(row.get("equipment_manufacturer"))
        db().execute(
            """
            insert into buyers (
                buyer_number, client_id, country, country_code, name, owner_id, owner, manufacturer_id, contact_name, contact_details,
                email, site_size, detailed_address, equipment_manufacturer, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                buyer_number or next_buyer_number(),
                client_id,
                country["code"],
                country["code"],
                name,
                owner["id"],
                owner["name"],
                manufacturer["id"] if manufacturer else None,
                row.get("contact_name", ""),
                row.get("contact_details", ""),
                row.get("email", ""),
                row.get("site_size", ""),
                detailed_address,
                row.get("equipment_manufacturer", ""),
                now(),
            ),
        )
        result["created"] += 1
        if owner_created:
            result["owners_created"] += 1
    return result


def next_invoice_number():
    prefix = f"PP-{date.today():%y%m}"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(30):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        invoice_number = f"{prefix}-{code}"
        if not db().execute("select id from invoices where invoice_number = ?", (invoice_number,)).fetchone():
            return invoice_number
    raise RuntimeError("Unable to generate a unique invoice number.")


def next_contract_number():
    prefix = f"CT{date.today():%y}"
    row = db().execute(
        """
        select contract_number from contracts
        where contract_number like ?
        order by contract_number desc limit 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    if not row:
        return f"{prefix}001"
    suffix = row["contract_number"][len(prefix):]
    try:
        return f"{prefix}{int(suffix) + 1:03d}"
    except ValueError:
        return f"{prefix}{secrets.randbelow(900) + 100}"


def contract_attachment_dir(contract_id):
    path = os.path.join(CONTRACT_ATTACHMENTS_DIR, str(contract_id))
    os.makedirs(path, exist_ok=True)
    return path


def contract_attachment_path(attachment):
    return os.path.join(
        CONTRACT_ATTACHMENTS_DIR,
        str(attachment["contract_id"]),
        attachment["stored_filename"],
    )


def save_contract_uploads(contract_id):
    existing = {
        row["original_filename"].strip().casefold()
        for row in db().execute(
            "select original_filename from contract_attachments where contract_id = ?",
            (contract_id,),
        ).fetchall()
    }
    for uploaded in request.files.getlist("attachments"):
        if not uploaded or not uploaded.filename:
            continue
        if not allowed_attachment(uploaded.filename):
            raise ValueError(f"é™„ä»¶åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚")
        original_filename = os.path.basename(uploaded.filename).strip()
        if original_filename.casefold() in existing:
            raise ValueError(f"é™„ä»¶â€œ{original_filename}â€å·²ç»å­˜åœ¨ã€‚")
        extension = original_filename.rsplit(".", 1)[1].lower()
        stored_filename = f"{secrets.token_hex(12)}.{extension}"
        uploaded.save(os.path.join(contract_attachment_dir(contract_id), stored_filename))
        db().execute(
            """
            insert into contract_attachments (
                contract_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                contract_id,
                original_filename,
                stored_filename,
                uploaded.content_type,
                g.user["id"],
                now(),
            ),
        )
        existing.add(original_filename.casefold())


def contract_form_values(contract=None):
    contract_type = request.form.get("contract_type", "framework")
    status = request.form.get("status", "draft")
    if contract_type not in CONTRACT_TYPE_LABELS:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„åˆåŒç±»å‹ã€‚")
    if status not in CONTRACT_STATUS_LABELS:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„åˆåŒçŠ¶æ€ã€‚")
    try:
        client_id = int(request.form.get("client_id", ""))
    except (TypeError, ValueError):
        raise ValueError("è¯·é€‰æ‹©å®¢æˆ·ã€‚")
    client = db().execute("select * from clients where id = ?", (client_id,)).fetchone()
    if not client or not can_access_client(client_id):
        raise ValueError("é€‰æ‹©çš„å®¢æˆ·ä¸å­˜åœ¨æˆ–æ— æƒè®¿é—®ã€‚")
    contract_number = request.form.get("contract_number", "").strip() or (
        contract["contract_number"] if contract else next_contract_number()
    )
    title = request.form.get("title", "").strip()
    if not title:
        raise ValueError("è¯·å¡«å†™åˆåŒåç§°ã€‚")
    start_date = request.form.get("start_date") or None
    end_date = request.form.get("end_date") or None
    if start_date and end_date and end_date < start_date:
        raise ValueError("åˆåŒç»“æŸæ—¥æœŸä¸èƒ½æ—©äºå¼€å§‹æ—¥æœŸã€‚")
    amount_text = request.form.get("amount", "").strip()
    amount = to_float(amount_text) if amount_text else None
    if amount is not None and amount < 0:
        raise ValueError("åˆåŒé‡‘é¢ä¸èƒ½å°äºé›¶ã€‚")
    project_name = request.form.get("project_name", "").strip()
    rate_card = request.form.get("rate_card", "").strip()
    if contract_type == "project":
        if not project_name:
            raise ValueError("é¡¹ç›®åˆåŒå¿…é¡»å¡«å†™é¡¹ç›®åç§°ã€‚")
        if amount is None:
            raise ValueError("é¡¹ç›®åˆåŒå¿…é¡»å¡«å†™åˆåŒé‡‘é¢ã€‚")
        rate_card = ""
    else:
        project_name = ""
    return {
        "contract_number": contract_number,
        "client_id": client_id,
        "contract_type": contract_type,
        "title": title,
        "status": status,
        "signed_date": request.form.get("signed_date") or None,
        "start_date": start_date,
        "end_date": end_date,
        "currency": request.form.get("currency", "USD"),
        "amount": amount,
        "payment_terms": request.form.get("payment_terms", "").strip(),
        "rate_card": rate_card,
        "project_name": project_name,
        "notes": request.form.get("notes", "").strip(),
    }


def next_service_order_number():
    prefix = f"SO{date.today():%y%m}"
    rows = db().execute(
        """
        select order_number from service_orders
        where order_number like ?
        order by order_number
        """,
        (f"{prefix}%",),
    ).fetchall()
    used = set()
    for row in rows:
        suffix = row["order_number"][len(prefix):]
        if suffix.isdigit() and int(suffix) > 0:
            used.add(int(suffix))
    sequence = 1
    while sequence in used:
        sequence += 1
    return f"{prefix}{sequence:03d}"


def next_expense_number():
    prefix = f"EX{date.today():%y%m}"
    row = db().execute(
        """
        select expense_number from expenses
        where expense_number like ?
        order by expense_number desc limit 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    if not row:
        return f"{prefix}001"
    suffix = row["expense_number"][len(prefix):]
    try:
        return f"{prefix}{int(suffix) + 1:03d}"
    except ValueError:
        return f"{prefix}{secrets.randbelow(900) + 100}"


def invoice_number_exists(invoice_number, exclude_invoice_id=None):
    if exclude_invoice_id:
        return db().execute(
            "select id from invoices where invoice_number = ? and id != ?",
            (invoice_number, exclude_invoice_id),
        ).fetchone() is not None
    return db().execute("select id from invoices where invoice_number = ?", (invoice_number,)).fetchone() is not None


def allowed_attachment(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def normalized_attachment_filename(filename):
    return os.path.basename(filename or "").strip().casefold()


def invoice_attachment_dir(invoice_id):
    path = os.path.join(ATTACHMENTS_DIR, str(invoice_id))
    os.makedirs(path, exist_ok=True)
    return path


def attachment_file_path(attachment):
    return os.path.join(ATTACHMENTS_DIR, str(attachment["invoice_id"]), attachment["stored_filename"])


def invoice_attachment_path(invoice_id):
    return os.path.join(ATTACHMENTS_DIR, str(invoice_id))


def existing_attachment_names(invoice_id):
    rows = db().execute(
        "select original_filename from invoice_attachments where invoice_id = ?",
        (invoice_id,),
    ).fetchall()
    return {normalized_attachment_filename(row["original_filename"]) for row in rows}


def duplicate_attachment_names(uploads, invoice_id=None):
    seen = existing_attachment_names(invoice_id) if invoice_id else set()
    duplicates = []
    for uploaded in uploads:
        original_name = os.path.basename(uploaded.filename or "").strip()
        normalized_name = normalized_attachment_filename(original_name)
        if not normalized_name:
            continue
        if normalized_name in seen:
            duplicates.append(original_name)
            continue
        seen.add(normalized_name)
    return duplicates


def validate_attachment_uploads(uploads, invoice_id=None):
    for uploaded in uploads:
        if uploaded and uploaded.filename and not allowed_attachment(uploaded.filename):
            flash(f"é™„ä»¶åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚", "error")
            return False
    duplicates = duplicate_attachment_names(uploads, invoice_id)
    if duplicates:
        flash(f"é™„ä»¶é‡å¤ï¼š{', '.join(duplicates)}ã€‚è¯·åˆ é™¤é‡å¤é™„ä»¶åå†ä¸Šä¼ ã€‚", "error")
        return False
    return True


def save_uploaded_attachment(invoice_id, uploaded):
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    if "." not in original_filename:
        original_filename = f"{original_filename}.{extension}"
    if normalized_attachment_filename(original_filename) in existing_attachment_names(invoice_id):
        raise RuntimeError(f"é™„ä»¶å·²å­˜åœ¨ï¼š{original_filename}")
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    uploaded.save(os.path.join(invoice_attachment_dir(invoice_id), stored_filename))
    db().execute(
        """
        insert into invoice_attachments (
            invoice_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )


def unique_invoice_attachment_filename(invoice_id, original_filename):
    original_filename = os.path.basename(original_filename or "").strip() or "attachment"
    stem, extension = os.path.splitext(original_filename)
    stem = stem or "attachment"
    existing = existing_attachment_names(invoice_id)
    candidate = original_filename
    counter = 2
    while normalized_attachment_filename(candidate) in existing:
        candidate = f"{stem}-{counter}{extension}"
        counter += 1
    return candidate


def copy_file_to_invoice_attachment(invoice_id, source_path, original_filename, content_type=None, uploaded_by=None):
    if not os.path.isfile(source_path):
        return False
    original_filename = unique_invoice_attachment_filename(invoice_id, original_filename)
    extension = os.path.splitext(original_filename)[1].lstrip(".").lower() or "dat"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    shutil.copyfile(source_path, os.path.join(invoice_attachment_dir(invoice_id), stored_filename))
    db().execute(
        """
        insert into invoice_attachments (
            invoice_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, original_filename, stored_filename, content_type, uploaded_by or g.user["id"], now()),
    )
    return True


def copy_customer_reimbursement_attachments_to_invoice(invoice_id, reimbursement_id):
    rows = get_customer_reimbursement_attachments(reimbursement_id)
    copied = 0
    for row in rows:
        if copy_file_to_invoice_attachment(
            invoice_id,
            customer_reimbursement_attachment_path(row),
            row["original_filename"],
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
    return copied


def copy_mileage_proofs_to_invoice(invoice_id, service_order_id):
    existing_names = existing_attachment_names(invoice_id)
    rows = db().execute(
        """
        select service_report_attachments.*, service_reports.report_date
        from service_report_attachments
        join service_reports on service_reports.id = service_report_attachments.report_id
        where service_reports.service_order_id = ?
          and service_report_attachments.category = 'mileage_proof'
        order by service_reports.report_date asc, service_report_attachments.uploaded_at asc,
                 service_report_attachments.id asc
        """,
        (service_order_id,),
    ).fetchall()
    copied = 0
    for row in rows:
        original_name = row["original_filename"] or "mileage-proof"
        report_date = (row["report_date"] or "").strip()
        if report_date:
            original_name = f"é‡Œç¨‹ä½è¯-{report_date}-{original_name}"
        normalized_name = normalized_attachment_filename(original_name)
        if normalized_name in existing_names:
            continue
        if copy_file_to_invoice_attachment(
            invoice_id,
            report_attachment_path(row),
            original_name,
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
            existing_names.add(normalized_name)
    return copied


def uploaded_attachments_from_request():
    return [
        file
        for file in request.files.getlist("attachments") + request.files.getlist("attachment")
        if file and file.filename
    ]


def get_invoice_attachments(invoice_id):
    return db().execute(
        """
        select invoice_attachments.*, users.name as uploader_name
        from invoice_attachments left join users on users.id = invoice_attachments.uploaded_by
        where invoice_id = ?
        order by uploaded_at desc
        """,
        (invoice_id,),
    ).fetchall()


def can_access_service_order(order):
    if not g.user:
        return False
    if is_internal_user():
        return True
    if is_external_manager():
        return bool(g.user["client_id"]) and order["client_id"] == g.user["client_id"]
    if is_external_employee():
        return db().execute(
            "select 1 from user_service_orders where user_id = ? and service_order_id = ?",
            (g.user["id"], order["id"]),
        ).fetchone() is not None
    return False


def require_service_order(order_id):
    order = db().execute(
        """
        select service_orders.*, work_order_types.name as work_order_type_name,
               coalesce(buyer_country_local.name, buyer_country_zh.name, buyer_country_en.name, buyers.country, buyers.country_code) as buyer_country,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               buyers.email as buyer_email,
               buyers.site_size as buyer_site_size,
               coalesce(order_manufacturers.name, buyers.equipment_manufacturer) as buyer_equipment_manufacturer,
               clients.name as billing_client_name,
               clients.email as billing_client_email,
               contracts.contract_number,
               contracts.title as contract_title,
               contracts.contract_type,
               coalesce(country_local.name, country_zh.name, country_en.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, country_en.region_name, service_orders.region_code) as region_name
        from service_orders
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join manufacturers as order_manufacturers on order_manufacturers.id = service_orders.manufacturer_id
        left join owners on owners.id = buyers.owner_id
        left join clients on clients.id = service_orders.client_id
        left join contracts on contracts.id = service_orders.contract_id
        left join country_translations buyer_country_local
          on buyer_country_local.country_code = buyers.country_code and buyer_country_local.language_code = ?
        left join country_translations buyer_country_zh
          on buyer_country_zh.country_code = buyers.country_code and buyer_country_zh.language_code = 'zh-CN'
        left join country_translations buyer_country_en
          on buyer_country_en.country_code = buyers.country_code and buyer_country_en.language_code = 'en'
        left join country_translations country_local
          on country_local.country_code = service_orders.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
        left join country_translations country_en
          on country_en.country_code = service_orders.country_code and country_en.language_code = 'en'
        where service_orders.id = ?
        """,
        (current_language(), current_language(), order_id),
    ).fetchone()
    if not order:
        abort(404)
    if not can_access_service_order(order):
        abort(403)
    return order


def service_order_dependency_counts(order_id):
    return {
        "reports": db().execute(
            "select count(*) as count from service_reports where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
        "invoices": db().execute(
            "select count(*) as count from invoices where service_order_id = ? and status != 'void'",
            (order_id,),
        ).fetchone()["count"],
        "customer_reimbursements": db().execute(
            "select count(*) as count from customer_reimbursements where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
        "expenses": db().execute(
            "select count(*) as count from expenses where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
    }


def service_order_delete_blockers(order_id):
    counts = service_order_dependency_counts(order_id)
    labels = {
        "reports": "å·¥ä½œæ—¥æŠ¥",
        "invoices": "å‘ç¥¨",
        "customer_reimbursements": "å·¥å•ç»“ç®—",
        "expenses": "å‘˜å·¥æŠ¥é”€",
    }
    return [labels[key] for key, count in counts.items() if count]


def require_service_order_start_date(order):
    if order["start_date"]:
        return None
    flash("è¯·å…ˆç¼–è¾‘å·¥å•å¹¶ç»´æŠ¤å¼€å§‹æ—¥æœŸï¼Œå†æ–°å¢å‘ç¥¨ã€å·¥ä½œæ—¥æŠ¥æˆ–æŠ¥é”€ã€‚", "error")
    return redirect(url_for("edit_service_order", order_id=order["id"]))


def require_service_report(report_id):
    report = db().execute("select * from service_reports where id = ?", (report_id,)).fetchone()
    if not report:
        abort(404)
    order = require_service_order(report["service_order_id"])
    return report, order


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def report_storage_context(report_id):
    row = db().execute(
        """
        select service_reports.report_date, service_orders.order_number
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        where service_reports.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not row:
        abort(404)
    order_number = secure_filename(row["order_number"]) or f"SO-{report_id}"
    try:
        report_date = datetime.strptime(row["report_date"], "%Y-%m-%d").strftime("%Y%m%d")
    except (TypeError, ValueError):
        report_date = str(row["report_date"] or "").replace("-", "") or "unknown-date"
    return order_number, report_date


def report_attachment_relative_path(report_id, category, stored_filename):
    order_number, report_date = report_storage_context(report_id)
    category_folder = REPORT_PHOTO_FOLDERS.get(category)
    if not category_folder:
        raise ValueError("æœªçŸ¥çš„æ—¥æŠ¥é™„ä»¶ç±»åˆ«ã€‚")
    return Path(order_number, report_date, category_folder, os.path.basename(stored_filename)).as_posix()


def report_attachment_dir(report_id, category):
    relative = report_attachment_relative_path(report_id, category, "placeholder.jpg")
    path = os.path.join(REPORT_ATTACHMENTS_DIR, os.path.dirname(relative))
    os.makedirs(path, exist_ok=True)
    return path


def report_attachment_path(attachment):
    stored_filename = str(attachment["stored_filename"])
    stored_path = Path(stored_filename)
    if len(stored_path.parts) > 1:
        return os.path.join(REPORT_ATTACHMENTS_DIR, *stored_path.parts)
    return os.path.join(REPORT_ATTACHMENTS_DIR, str(attachment["report_id"]), stored_filename)


def prune_empty_report_folders(path):
    root = Path(REPORT_ATTACHMENTS_DIR).resolve()
    current = Path(path).resolve().parent
    while current != root:
        try:
            current.relative_to(root)
            current.rmdir()
        except (OSError, ValueError):
            break
        current = current.parent


def relocate_report_attachments(report_id):
    attachments = db().execute(
        "select * from service_report_attachments where report_id = ?",
        (report_id,),
    ).fetchall()
    for attachment in attachments:
        old_path = report_attachment_path(attachment)
        stored_filename = os.path.basename(attachment["stored_filename"])
        relative_path = report_attachment_relative_path(report_id, attachment["category"], stored_filename)
        new_path = os.path.join(REPORT_ATTACHMENTS_DIR, *Path(relative_path).parts)
        if os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(new_path)):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if os.path.isfile(old_path):
                shutil.move(old_path, new_path)
                prune_empty_report_folders(old_path)
            db().execute(
                "update service_report_attachments set stored_filename = ? where id = ?",
                (relative_path, attachment["id"]),
            )


def uploaded_report_files(field_name):
    return [file for file in request.files.getlist(field_name) if file and file.filename]


def compress_report_image(source, target_path):
    compress_image(source, target_path)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_report_photo_name(filename):
    name = os.path.basename(filename or "").strip()
    stem, _ = os.path.splitext(name)
    stem = re.sub(r"\s*\(\d+\)$", "", stem).strip()
    return stem.casefold()


def is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
    candidate_name = normalized_report_photo_name(original_filename)
    rows = db().execute(
        """
        select * from service_report_attachments
        where report_id = ? and category = ?
        """,
        (report_id, category),
    ).fetchall()
    for row in rows:
        existing_path = report_attachment_path(row)
        if os.path.isfile(existing_path):
            try:
                if file_sha256(existing_path) == candidate_hash:
                    return True
            except OSError:
                pass
        elif candidate_name and normalized_report_photo_name(row["original_filename"]) == candidate_name:
            return True
    return False


def save_report_attachment(report_id, uploaded, category):
    if not uploaded or not uploaded.filename:
        return False
    if not allowed_image(uploaded.filename):
        raise ValueError("æ—¥æŠ¥ç…§ç‰‡ä»…æ”¯æŒ PNGã€JPGã€JPEGã€WEBPã€GIFã€‚")
    source_filename = uploaded.filename or "photo"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"photo.{extension}"
    target_dir = report_attachment_dir(report_id, category)
    temporary_filename = f".{secrets.token_hex(12)}.jpg.tmp"
    temporary_path = os.path.join(target_dir, temporary_filename)
    compress_report_image(uploaded.stream, temporary_path)
    candidate_hash = file_sha256(temporary_path)
    if is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
        os.remove(temporary_path)
        return False
    image_filename = f"{secrets.token_hex(12)}.jpg"
    stored_filename = report_attachment_relative_path(report_id, category, image_filename)
    os.replace(temporary_path, os.path.join(target_dir, image_filename))
    content_type = "image/jpeg"
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, category, original_filename, stored_filename, content_type, g.user["id"], now()),
    )
    return True


def save_report_file_attachment(report_id, uploaded, category):
    if not uploaded or not uploaded.filename:
        return False
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"é‡Œç¨‹ä½è¯åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    if "." not in original_filename:
        original_filename = f"{original_filename}.{extension}"
    target_dir = report_attachment_dir(report_id, category)
    temporary_filename = f".{secrets.token_hex(12)}.{extension}.tmp"
    temporary_path = os.path.join(target_dir, temporary_filename)
    uploaded.save(temporary_path)
    candidate_hash = file_sha256(temporary_path)
    if is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
        os.remove(temporary_path)
        return False
    stored_basename = f"{secrets.token_hex(12)}.{extension}"
    stored_filename = report_attachment_relative_path(report_id, category, stored_basename)
    os.replace(temporary_path, os.path.join(target_dir, stored_basename))
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, category, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )
    return True


def shared_photos_root():
    return Path(SHARED_PHOTOS_DIR).resolve()


def service_order_photo_folder(order_number):
    root = shared_photos_root()
    folder = (root / secure_filename(order_number)).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("å·¥å•ç…§ç‰‡ç›®å½•æ— æ•ˆã€‚")
    return folder


def service_order_photo_summary(order_number):
    folder = service_order_photo_folder(order_number)
    if not folder.exists():
        return {"exists": False, "nonempty": False, "file_count": 0, "bytes": 0}
    files = [path for path in folder.rglob("*") if path.is_file()]
    return {
        "exists": True,
        "nonempty": any(folder.iterdir()),
        "file_count": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def ensure_service_order_picture_folder(order_number):
    folder = service_order_photo_folder(order_number) / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def service_order_incoming_folder(order_number, source_name="uploads"):
    folder = shared_photos_root() / secure_filename(order_number) / "incoming" / secure_filename(source_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_google_photos_share_url(value):
    parsed = urlsplit((value or "").strip())
    host = parsed.netloc.lower().split(":")[0]
    return parsed.scheme in {"http", "https"} and host in GOOGLE_PHOTOS_ALLOWED_HOSTS


def google_photos_page_html(share_url):
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PrasinosPowerInvoiceTool/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with urlopen(Request(share_url, headers=request_headers), timeout=30) as response:
            final_host = urlsplit(response.geturl()).netloc.lower().split(":")[0]
            if final_host == "accounts.google.com":
                raise ValueError("è¿™ä¸ª Google Photos åˆ†äº«é“¾æ¥éœ€è¦ç™»å½•æˆ–æ²¡æœ‰å…¬å¼€è®¿é—®æƒé™ï¼ŒæœåŠ¡å™¨æ— æ³•ç›´æ¥å¯¼å…¥ã€‚")
            content_type = response.headers.get_content_type()
            if content_type and "html" not in content_type:
                raise ValueError("é“¾æ¥è¿”å›çš„ä¸æ˜¯ Google Photos åˆ†äº«é¡µé¢ã€‚")
            return response.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ValueError("è¿™ä¸ª Google Photos åˆ†äº«é“¾æ¥éœ€è¦ç™»å½•æˆ–æ²¡æœ‰è®¿é—®æƒé™ï¼ŒæœåŠ¡å™¨æ— æ³•ç›´æ¥å¯¼å…¥ã€‚") from error
        raise ValueError(f"è¯»å– Google Photos åˆ†äº«é“¾æ¥å¤±è´¥ï¼šHTTP {error.code}") from error
    except URLError as error:
        raise ValueError(f"è¯»å– Google Photos åˆ†äº«é“¾æ¥å¤±è´¥ï¼š{error.reason}") from error


def normalize_embedded_google_url(value):
    text = html.unescape(value or "")
    replacements = {
        "\\/": "/",
        "\\u003d": "=",
        "\\u0026": "&",
        "\\u003f": "?",
        "\\u0025": "%",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def google_image_score(url):
    size_match = re.search(r"=w(\d+)-h(\d+)", url)
    if size_match:
        return int(size_match.group(1)) * int(size_match.group(2))
    square_match = re.search(r"=s(\d+)", url)
    if square_match:
        size = int(square_match.group(1))
        return size * size
    return len(url)


def google_image_dimensions(url):
    size_match = re.search(r"=w(\d+)-h(\d+)", url)
    if size_match:
        return int(size_match.group(1)), int(size_match.group(2))
    square_match = re.search(r"=s(\d+)", url)
    if square_match:
        size = int(square_match.group(1))
        return size, size
    return None


def is_likely_google_account_image(url):
    parsed = urlsplit(url)
    lower = url.casefold()
    account_keywords = (
        "avatar",
        "profile",
        "userphoto",
        "photo.jpg",
        "account",
        "person",
        "people",
        "anonymous",
        "googleusercontent.com/a/",
    )
    if any(keyword in lower for keyword in account_keywords):
        return True
    dimensions = google_image_dimensions(url)
    if dimensions:
        width, height = dimensions
        if width <= 300 and height <= 300:
            return True
    return False


def google_photo_url_key(url):
    return url.split("=", 1)[0]


def google_photo_download_candidates(url):
    base = google_photo_url_key(url)
    candidates = [
        f"{base}=d",
        f"{base}=s0",
        f"{base}=s0-d",
        url,
    ]
    return list(dict.fromkeys(candidates))


def extract_google_photo_urls(page_html):
    normalized = normalize_embedded_google_url(page_html)
    matches = re.findall(r"https://lh3\.googleusercontent\.com/[^\s\"'<>\\\])]+", normalized)
    by_photo = {}
    for raw_url in matches:
        url = raw_url.rstrip(".,;")
        if is_likely_google_account_image(url):
            continue
        photo_key = google_photo_url_key(url)
        if not photo_key:
            continue
        if photo_key not in by_photo or google_image_score(url) > google_image_score(by_photo[photo_key]):
            by_photo[photo_key] = url
    urls = list(by_photo.values())
    return urls[:GOOGLE_PHOTOS_MAX_IMPORT], len(urls)


def downloaded_image_extension(content_type, url):
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix.lstrip(".") in ALLOWED_IMAGE_EXTENSIONS:
        return suffix
    return ".jpg"


def unique_download_path(folder, filename):
    candidate = folder / secure_filename(filename)
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        next_candidate = candidate.with_name(f"{candidate.stem}-{counter}{candidate.suffix}")
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def download_google_photo_image(url, target_dir, index):
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PrasinosPowerInvoiceTool/1.0)",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    last_error = None
    for candidate_url in google_photo_download_candidates(url):
        try:
            with urlopen(Request(candidate_url, headers=request_headers), timeout=45) as response:
                content_type = response.headers.get_content_type()
                if not (content_type or "").startswith("image/"):
                    raise ValueError(f"ä¸æ˜¯å›¾ç‰‡å†…å®¹ï¼š{content_type or 'unknown'}")
                extension = downloaded_image_extension(content_type, candidate_url)
                target_path = unique_download_path(target_dir, f"google-photo-{index:03d}{extension}")
                total = 0
                digest = hashlib.sha256()
                with open(target_path, "wb") as file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > GOOGLE_PHOTOS_MAX_IMAGE_BYTES:
                            file.close()
                            try:
                                target_path.unlink()
                            except FileNotFoundError:
                                pass
                            raise ValueError("å›¾ç‰‡è¶…è¿‡ 30MBï¼Œå·²è·³è¿‡ã€‚")
                        digest.update(chunk)
                        file.write(chunk)
                return target_path, digest.hexdigest(), total
        except ValueError as error:
            if "è¶…è¿‡ 30MB" in str(error):
                raise
            last_error = error
        except (HTTPError, URLError, OSError) as error:
            last_error = error
    raise ValueError(f"æ— æ³•ä¸‹è½½åŸå›¾ï¼š{last_error}")


def import_google_photos_share_to_incoming(order, share_url):
    if not is_google_photos_share_url(share_url):
        raise ValueError("è¯·è¾“å…¥ Google Photos åˆ†äº«é“¾æ¥ã€‚")
    page_html = google_photos_page_html(share_url)
    image_urls, total_found = extract_google_photo_urls(page_html)
    if not image_urls:
        raise ValueError("æ²¡æœ‰ä»è¿™ä¸ªåˆ†äº«é“¾æ¥ä¸­è¯†åˆ«åˆ°å¯ä¸‹è½½ç…§ç‰‡ã€‚é“¾æ¥å¯èƒ½éœ€è¦ç™»å½•ï¼Œæˆ– Google é¡µé¢ç»“æ„å‘ç”Ÿå˜åŒ–ã€‚")
    target_dir = service_order_incoming_folder(order["order_number"], "google-photos")
    imported = []
    skipped = []
    seen_hashes = set()
    for index, image_url in enumerate(image_urls, start=1):
        try:
            path, digest, size = download_google_photo_image(image_url, target_dir, index)
            if digest in seen_hashes:
                path.unlink(missing_ok=True)
                skipped.append(f"ç¬¬ {index} å¼ ï¼šé‡å¤ç…§ç‰‡")
                continue
            seen_hashes.add(digest)
            imported.append({"path": path, "size": size})
        except Exception as error:
            skipped.append(f"ç¬¬ {index} å¼ ï¼š{error}")
    if not imported and skipped:
        raise ValueError("ç…§ç‰‡ä¸‹è½½å¤±è´¥ï¼š" + "ï¼›".join(skipped[:5]))
    truncated_count = max(0, total_found - len(image_urls))
    return imported, skipped, target_dir, total_found, truncated_count


def send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found=0, truncated_count=0, error=None):
    success_count = len(imported or [])
    skipped_count = len(skipped or [])
    if error:
        title = f"Google Photos å¯¼å…¥å¤±è´¥ï¼š{order['order_number']}"
        lines = [
            f"å·¥å•ï¼š{order['order_number']}",
            f"é“¾æ¥ï¼š{share_url}",
            f"å¤±è´¥åŸå› ï¼š{error}",
        ]
    else:
        title = f"Google Photos å¯¼å…¥å®Œæˆï¼š{order['order_number']}"
        lines = [
            f"å·¥å•ï¼š{order['order_number']}",
            f"é“¾æ¥ï¼š{share_url}",
            f"è¯†åˆ«åˆ°ç…§ç‰‡ï¼š{total_found} å¼ ",
            f"å°è¯•å¯¼å…¥ï¼š{success_count + skipped_count} å¼ ",
            f"å¯¼å…¥ç…§ç‰‡ï¼š{success_count} å¼ ",
            f"è·³è¿‡/å¤±è´¥ï¼š{skipped_count} å¼ ",
            f"ä¿å­˜ä½ç½®ï¼š{target_dir}",
            "ç…§ç‰‡å·²æ”¾å…¥ incoming/google-photosï¼Œphoto_worker ä¼šè‡ªåŠ¨å¤„ç†åˆ° picturesã€‚",
        ]
        if truncated_count:
            lines.append(f"æ³¨æ„ï¼šè¯†åˆ«åˆ°çš„ç…§ç‰‡è¶…è¿‡ç³»ç»Ÿå•æ¬¡å¯¼å…¥ä¸Šé™ {GOOGLE_PHOTOS_MAX_IMPORT} å¼ ï¼Œä»æœ‰ {truncated_count} å¼ æœªå¯¼å…¥ã€‚")
        if skipped:
            lines.append("å‰å‡ æ¡è·³è¿‡/å¤±è´¥ä¿¡æ¯ï¼š")
            lines.extend(skipped[:10])
    body = "\n".join(lines)
    create_message(user["id"], title, body, f"/service-orders/{order['id']}")
    email = (user["email"] or "").strip()
    if email:
        try:
            send_email(
                to=email,
                subject=title,
                html=f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
            )
        except Exception:
            app.logger.exception("Unable to send Google Photos import result email to %s", email)


def run_google_photos_import_job(order_id, user_id, share_url):
    with app.app_context():
        order = db().execute("select * from service_orders where id = ?", (order_id,)).fetchone()
        user = db().execute("select * from users where id = ?", (user_id,)).fetchone()
        if not order or not user:
            return
        try:
            imported, skipped, target_dir, total_found, truncated_count = import_google_photos_share_to_incoming(order, share_url)
            send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found, truncated_count)
            db().commit()
        except Exception as error:
            db().rollback()
            try:
                send_google_photos_import_result(user, order, share_url, [], [], "", error=str(error))
                db().commit()
            except Exception:
                db().rollback()
                app.logger.exception("Unable to notify Google Photos import result for order %s", order["order_number"])


def start_google_photos_import_job(order_id, user_id, share_url):
    thread = threading.Thread(
        target=run_google_photos_import_job,
        args=(order_id, user_id, share_url),
        daemon=True,
    )
    thread.start()


def resolve_shared_photo(relative_path="", require_file=False, allow_missing=False):
    root = shared_photos_root()
    candidate = (root / str(relative_path or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        abort(403)
    if allow_missing and not candidate.exists():
        return candidate
    if require_file:
        if not candidate.is_file() or candidate.suffix.lower().lstrip(".") not in ALLOWED_IMAGE_EXTENSIONS:
            abort(404)
    elif not candidate.is_dir():
        abort(404)
    return candidate


def shared_photo_relative(path):
    return path.resolve().relative_to(shared_photos_root()).as_posix()


def resolve_shared_picture(relative_path):
    source_path = resolve_shared_photo(relative_path, require_file=True)
    relative_parts = source_path.relative_to(shared_photos_root()).parts
    if len(relative_parts) < 3 or relative_parts[1].casefold() != "pictures":
        abort(403)
    return source_path, relative_parts


def count_shared_images(path, excluded_dirs=None):
    if not path.is_dir():
        return 0
    excluded = {name.casefold() for name in (excluded_dirs or set())}
    return sum(
        1
        for entry in path.rglob("*")
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS
        and not ({part.casefold() for part in entry.relative_to(path).parts} & ({"@eadir"} | excluded))
        and valid_image_file(entry)
    )


def valid_image_file(path):
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def safe_attachment_response(path, original_filename):
    """Return an attachment response based only on server-side content checks."""
    try:
        with Image.open(path) as image:
            image.verify()
            image_format = image.format
        image_mimetype = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }.get(image_format)
        if image_mimetype:
            return send_file(
                path,
                as_attachment=False,
                download_name=original_filename,
                mimetype=image_mimetype,
                conditional=True,
            )
    except Exception:
        pass

    try:
        with open(path, "rb") as file:
            is_pdf = file.read(5) == b"%PDF-"
    except Exception:
        is_pdf = False
    if is_pdf:
        return send_file(
            path,
            as_attachment=False,
            download_name=original_filename,
            mimetype="application/pdf",
            conditional=True,
        )
    return send_file(
        path,
        as_attachment=True,
        download_name=original_filename,
        mimetype="application/octet-stream",
        conditional=True,
    )


def order_photo_status(order_dir):
    state_names = {"incoming", "pictures", "thumbnails", "processing", "failed", "original_backup"}
    waiting = count_shared_images(order_dir / "incoming")
    if order_dir.is_dir():
        waiting += sum(
            1
            for entry in order_dir.rglob("*")
            if entry.is_file()
            and not entry.name.startswith(".")
            and entry.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS
            and entry.relative_to(order_dir).parts[0].casefold() not in state_names
            and "@eadir" not in {part.casefold() for part in entry.relative_to(order_dir).parts}
        )
    return {
        "waiting": waiting,
        "processing": count_shared_images(order_dir / "processing"),
        "completed": count_shared_images(order_dir / "pictures"),
        "failed": count_shared_images(order_dir / "failed", excluded_dirs={"orphaned_pictures"}),
    }


def seed_customer_reimbursement_projects(connection):
    for name, unit_price in CUSTOMER_REIMBURSEMENT_PROJECTS.items():
        row = connection.execute(
            "select id, project_type from projects where name_key = ? and project_type = 'customer_expense'",
            (project_name_key(name),),
        ).fetchone()
        if row:
            connection.execute(
                """
                update projects
                set name = ?, name_key = ?, unit_price = ?, is_active = 1
                where id = ?
                """,
                (name, project_name_key(name), unit_price, row["id"]),
            )
            continue
        connection.execute(
            """
            insert into projects (name, name_key, project_type, default_amount, unit_price, tax_rate, is_active, created_at)
            values (?, ?, 'customer_expense', 0, ?, 0, 1, ?)
            """,
            (name, project_name_key(name), unit_price, now()),
        )
    for name in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS:
        row = connection.execute(
            "select id from projects where name_key = ? and project_type = 'invoice'",
            (project_name_key(name),),
        ).fetchone()
        if row:
            continue
        connection.execute(
            """
            insert into projects (name, name_key, project_type, default_amount, unit_price, tax_rate, is_active, created_at)
            values (?, ?, 'invoice', 0, 0, 0, 1, ?)
            """,
            (name, project_name_key(name), now()),
        )


def save_shared_report_photo(report_id, relative_path, category):
    source_path = resolve_shared_photo(relative_path, require_file=True)
    order_number, _ = report_storage_context(report_id)
    processed_root = (shared_photos_root() / order_number / "pictures").resolve()
    try:
        source_path.relative_to(processed_root)
    except ValueError:
        raise ValueError("åªèƒ½é€‰æ‹©å·²å®Œæˆå¤„ç†çš„ NAS ç…§ç‰‡ã€‚")
    if source_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("NAS ç…§ç‰‡å°šæœªå®Œæˆå¤„ç†ã€‚")
    if is_duplicate_report_photo(report_id, category, source_path.name, file_sha256(source_path)):
        return False
    image_filename = f"{secrets.token_hex(12)}.jpg"
    stored_filename = report_attachment_relative_path(report_id, category, image_filename)
    shutil.copyfile(source_path, os.path.join(report_attachment_dir(report_id, category), image_filename))
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, 'image/jpeg', ?, ?)
        """,
        (report_id, category, source_path.name, stored_filename, g.user["id"], now()),
    )
    return True


def save_report_uploads(report_id):
    for field_name, category in (
        ("self_check_photos", "self_check"),
        ("site_photos", "site"),
        ("arrival_photos", "arrival"),
        ("departure_photos", "departure"),
    ):
        for uploaded in uploaded_report_files(field_name):
            save_report_attachment(report_id, uploaded, category)
        for relative_path in request.form.getlist(f"shared_photo_{category}"):
            save_shared_report_photo(report_id, relative_path, category)
    for uploaded in uploaded_report_files("mileage_proof_attachments"):
        save_report_file_attachment(report_id, uploaded, "mileage_proof")


def claim_report_save_token(token, report_id=None):
    token = str(token or "").strip()
    if not token:
        raise ValueError("ä¿å­˜ä»¤ç‰Œæ— æ•ˆï¼Œè¯·åˆ·æ–°é¡µé¢åé‡è¯•ã€‚")
    cursor = db().execute(
        """
        insert or ignore into service_report_save_tokens (token, report_id, created_at)
        values (?, ?, ?)
        """,
        (token, report_id, now()),
    )
    return cursor.rowcount == 1


def finish_report_save_token(token, report_id):
    db().execute(
        "update service_report_save_tokens set report_id = ? where token = ?",
        (report_id, token),
    )


def claim_invoice_save_token(token, invoice_id=None):
    token = str(token or "").strip()
    if not token:
        raise ValueError("ä¿å­˜ä»¤ç‰Œæ— æ•ˆï¼Œè¯·åˆ·æ–°é¡µé¢åé‡è¯•ã€‚")
    cursor = db().execute(
        """
        insert or ignore into invoice_save_tokens (token, invoice_id, created_at)
        values (?, ?, ?)
        """,
        (token, invoice_id, now()),
    )
    return cursor.rowcount == 1


def finish_invoice_save_token(token, invoice_id):
    db().execute(
        "update invoice_save_tokens set invoice_id = ? where token = ?",
        (invoice_id, token),
    )


def claim_expense_save_token(token, expense_id=None):
    token = str(token or "").strip()
    if not token:
        raise ValueError("ä¿å­˜ä»¤ç‰Œæ— æ•ˆï¼Œè¯·åˆ·æ–°é¡µé¢åé‡è¯•ã€‚")
    cursor = db().execute(
        """
        insert or ignore into expense_save_tokens (token, expense_id, created_at)
        values (?, ?, ?)
        """,
        (token, expense_id, now()),
    )
    return cursor.rowcount == 1


def finish_expense_save_token(token, expense_id):
    db().execute(
        "update expense_save_tokens set expense_id = ? where token = ?",
        (expense_id, token),
    )


def posted_report_time(prefix):
    hour = request.form.get(f"{prefix}_hour", "").strip()
    minute = request.form.get(f"{prefix}_minute", "").strip()
    if not hour and not minute:
        return request.form.get(prefix, "").strip()
    try:
        hour_value = int(hour)
        minute_value = int(minute)
    except (TypeError, ValueError):
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„ç°åœºæ—¶é—´ã€‚")
    if not 0 <= hour_value <= 23 or not 0 <= minute_value <= 59:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„ç°åœºæ—¶é—´ã€‚")
    return f"{hour_value:02d}:{minute_value:02d}"


def report_worker_ids_from_form():
    return list(dict.fromkeys(value for value in request.form.getlist("worker_user_id") if value))


REPORT_TRAVEL_MODES = {"self_drive", "flight", "following", "rental_drive"}


def report_worker_rows_from_form():
    field_names = (
        "worker_user_id", "worker_travel_mode", "worker_driving_miles",
        "worker_travel_hours", "worker_public_transport_hours", "worker_work_description",
    )
    values = {name: request.form.getlist(name) for name in field_names}
    rows = []
    for index, raw_user_id in enumerate(values["worker_user_id"]):
        user_id = str(raw_user_id or "").strip()
        if not user_id:
            continue
        row = {
            name: (values[name][index].strip() if index < len(values[name]) else "")
            for name in field_names
        }
        row["worker_user_id"] = user_id
        mode = row["worker_travel_mode"]
        if mode not in REPORT_TRAVEL_MODES:
            raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„æœåŠ¡äººå‘˜å‡ºè¡Œæ–¹å¼ã€‚")
        miles = to_float(row["worker_driving_miles"])
        travel_hours = to_float(row["worker_travel_hours"])
        public_hours = to_float(row["worker_public_transport_hours"])
        # 0 è‹±é‡Œ / 0 å°æ—¶æ˜¯åˆæ³•å€¼ï¼ˆéšè¡Œã€çŸ­é€”ã€é¡ºè·¯ç­‰ï¼‰ï¼›åªæœ‰ç•™ç©ºæˆ–è´Ÿæ•°æ‰éæ³•ã€‚
        if mode in {"self_drive", "following", "rental_drive"}:
            if not row["worker_driving_miles"]:
                raise ValueError("è‡ªé©¾ã€éšè¡Œå’Œç§Ÿè½¦é©¾é©¶äººå‘˜å¿…é¡»å¡«å†™é‡Œç¨‹ã€‚")
            if miles < 0:
                raise ValueError("è‡ªé©¾ã€éšè¡Œå’Œç§Ÿè½¦é©¾é©¶äººå‘˜å¿…é¡»å¡«å†™é‡Œç¨‹ã€‚")
            if not row["worker_travel_hours"]:
                raise ValueError("è‡ªé©¾ã€éšè¡Œå’Œç§Ÿè½¦é©¾é©¶äººå‘˜å¿…é¡»å¡«å†™äº¤é€šæ—¶é•¿ã€‚")
            if travel_hours < 0:
                raise ValueError("è‡ªé©¾ã€éšè¡Œå’Œç§Ÿè½¦é©¾é©¶äººå‘˜å¿…é¡»å¡«å†™äº¤é€šæ—¶é•¿ã€‚")
        if mode == "flight":
            if not row["worker_public_transport_hours"]:
                raise ValueError("é£æœºå‡ºè¡Œå¿…é¡»å¡«å†™å…¬å…±äº¤é€šæ—¶é•¿ã€‚")
            if public_hours < 0:
                raise ValueError("é£æœºå‡ºè¡Œå¿…é¡»å¡«å†™å…¬å…±äº¤é€šæ—¶é•¿ã€‚")
        row.update(
            driving_miles=miles if mode != "flight" else 0,
            travel_hours=travel_hours if mode != "flight" else 0,
            public_transport_hours=public_hours if mode == "flight" else 0,
        )
        rows.append(row)
    if not rows:
        raise ValueError("æœåŠ¡äººå‘˜æ¸…å•è‡³å°‘éœ€è¦æ·»åŠ ä¸€äººã€‚")
    if len({row["worker_user_id"] for row in rows}) != len(rows):
        raise ValueError("åŒä¸€åæœåŠ¡äººå‘˜ä¸èƒ½é‡å¤æ·»åŠ ã€‚")
    return rows


def report_billing_mileage(worker_rows, method):
    if method == "per_vehicle":
        return round(sum(row["driving_miles"] for row in worker_rows if row["worker_travel_mode"] == "self_drive"), 2)
    return round(sum(row["driving_miles"] for row in worker_rows if row["worker_travel_mode"] in {"self_drive", "following"}), 2)


def get_report_attachments(report_id):
    rows = db().execute(
        """
        select service_report_attachments.*, users.name as uploader_name
        from service_report_attachments left join users on users.id = service_report_attachments.uploaded_by
        where report_id = ?
        order by category asc, uploaded_at asc, id asc
        """,
        (report_id,),
    ).fetchall()
    grouped = {"self_check": [], "site": [], "arrival": [], "departure": [], "mileage_proof": []}
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)
    return grouped


def customer_reimbursement_dir(reimbursement_id):
    path = os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(reimbursement_id))
    os.makedirs(path, exist_ok=True)
    return path


def customer_reimbursement_attachment_dir(reimbursement_id):
    path = os.path.join(customer_reimbursement_dir(reimbursement_id), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def customer_reimbursement_file_path(reimbursement):
    return os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(reimbursement["id"]), reimbursement["stored_filename"])


def customer_reimbursement_attachment_path(attachment):
    return os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(attachment["customer_reimbursement_id"]), "attachments", attachment["stored_filename"])


def require_customer_reimbursement(reimbursement_id):
    if not can_view_customer_reimbursement():
        abort(403)
    reimbursement = db().execute("select * from customer_reimbursements where id = ?", (reimbursement_id,)).fetchone()
    if not reimbursement:
        abort(404)
    order = require_service_order(reimbursement["service_order_id"])
    return reimbursement, order


def customer_reimbursement_rates():
    rows = db().execute(
        """
        select name, unit_price
        from projects
        where project_type = 'customer_expense' and is_active = 1
        """
    ).fetchall()
    rates = {name: 0.0 for name in CUSTOMER_REIMBURSEMENT_PROJECTS}
    rates.update({row["name"]: float(row["unit_price"] or 0) for row in rows})
    return rates


def employee_grade_options(include_inactive=False):
    active_clause = "" if include_inactive else "where is_active = 1"
    return db().execute(
        f"""
        select *
        from employee_grades
        {active_clause}
        order by is_active desc, grade_name
        """
    ).fetchall()


def parse_report_minutes(value):
    if not value or ":" not in str(value):
        return None
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except ValueError:
        return None


def rounded_report_service_hours(arrival_time, departure_time):
    arrival = parse_report_minutes(arrival_time)
    departure = parse_report_minutes(departure_time)
    if arrival is None or departure is None or departure <= arrival:
        return 0
    duration_minutes = departure - arrival
    rounded_minutes = (duration_minutes // 15) * 15
    if duration_minutes % 15 > 7:
        rounded_minutes += 15
    return round(rounded_minutes / 60, 2)


def format_report_hours(value):
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def calculated_report_total_time():
    rows = report_worker_rows_from_form()
    return format_report_hours(sum(row["travel_hours"] + row["public_transport_hours"] for row in rows))


def calculated_report_total_service_hours(arrival_time, departure_time):
    return rounded_report_service_hours(arrival_time, departure_time) * len(report_worker_rows_from_form())


def calculated_report_driving_miles():
    method = request.form.get("mileage_billing_method", "per_person")
    if method not in {"per_person", "per_vehicle"}:
        method = "per_person"
    return report_billing_mileage(report_worker_rows_from_form(), method)


def observed_us_holidays(year):
    def observed(day):
        if day.weekday() == 5:
            return day - timedelta(days=1)
        if day.weekday() == 6:
            return day + timedelta(days=1)
        return day

    def nth_weekday(month, weekday, nth):
        current = date(year, month, 1)
        while current.weekday() != weekday:
            current += timedelta(days=1)
        return current + timedelta(days=7 * (nth - 1))

    def last_weekday(month, weekday):
        current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        while current.weekday() != weekday:
            current -= timedelta(days=1)
        return current

    return {
        observed(date(year, 1, 1)),
        nth_weekday(1, 0, 3),
        nth_weekday(2, 0, 3),
        last_weekday(5, 0),
        observed(date(year, 6, 19)),
        observed(date(year, 7, 4)),
        nth_weekday(9, 0, 1),
        nth_weekday(10, 0, 2),
        observed(date(year, 11, 11)),
        nth_weekday(11, 3, 4),
        observed(date(year, 12, 25)),
    }


def is_us_weekend_or_holiday(day):
    return day.weekday() >= 5 or day in observed_us_holidays(day.year)


def report_duration_hours(report, worker_count=1):
    rounded_hours = rounded_report_service_hours(report["arrival_time"], report["departure_time"])
    if rounded_hours:
        return rounded_hours
    fallback_hours = float(report["total_service_hours"] or 0)
    return round(fallback_hours / max(worker_count, 1), 2)


def report_actual_date(report):
    return report["actual_work_date"] or report["report_date"]


def split_report_labor_hours(report, worker_count=1):
    work_hours = report_duration_hours(report, worker_count)
    if "worker_public_transport_hours" in report.keys():
        worker_travel_hours = (
            report["worker_travel_hours"]
            if "worker_travel_hours" in report.keys() else None
        )
        if (
            "worker_travel_mode" in report.keys()
            and (report["worker_travel_mode"] or "legacy") in {"self_drive", "legacy"}
        ):
            worker_travel_hours = 0
        worker_public_hours = report["worker_public_transport_hours"]
        if worker_travel_hours is not None or worker_public_hours is not None:
            travel_hours = float(worker_travel_hours or 0)
            public_transport_hours = float(worker_public_hours or 0)
        else:
            travel_hours = float(report["travel_hours"] or 0) / max(worker_count, 1)
            public_transport_hours = float(report["public_transport_hours"] or 0) / max(worker_count, 1)
    else:
        travel_hours = float(report["travel_hours"] or 0) / max(worker_count, 1)
        public_transport_hours = float(report["public_transport_hours"] or 0) / max(worker_count, 1)
    transport_hours = travel_hours + public_transport_hours
    try:
        work_day = date.fromisoformat(report_actual_date(report))
    except (TypeError, ValueError):
        work_day = None
    result = {
        "transport_hours": transport_hours,  # backwards-compatible payroll total
        "travel_hours": travel_hours,
        "public_transport_hours": public_transport_hours,
    }
    if work_day and is_us_weekend_or_holiday(work_day):
        result.update({"standard_hours": 0, "overtime_hours": 0, "holiday_hours": work_hours})
    else:
        result.update({
            "standard_hours": min(work_hours, 8),
            "overtime_hours": max(work_hours - 8, 0),
            "holiday_hours": 0,
        })
    return result


def customer_reimbursement_seed_rows(order_id):
    rates = customer_reimbursement_rates()
    reports = db().execute(
        """
        select service_reports.*
        from service_reports
        where service_reports.service_order_id = ?
        order by service_reports.report_date asc, service_reports.id asc
        """,
        (order_id,),
    ).fetchall()
    rows = []
    for report in reports:
        workers = db().execute(
            """
            select users.id as worker_user_id, users.name, service_report_workers.driving_miles,
                   service_report_workers.travel_mode, service_report_workers.travel_hours,
                   service_report_workers.public_transport_hours as worker_public_transport_hours
            from service_report_workers
            join users on users.id = service_report_workers.user_id
            where service_report_workers.report_id = ?
            order by users.name
            """,
            (report["id"],),
        ).fetchall()
        if not workers:
            continue
        for worker in workers:
            mode = worker["travel_mode"] or "legacy"
            worker_report = dict(report)
            worker_report["worker_travel_hours"] = (
                0 if mode in {"self_drive", "legacy"} else worker["travel_hours"]
            )
            worker_report["worker_public_transport_hours"] = worker["worker_public_transport_hours"]
            labor_hours = split_report_labor_hours(worker_report, len(workers))
            work_date = report_actual_date(report)
            standard_rate = contract_rate(db(), order_id, work_date, "regular_hours", legacy_rates=rates,
                                          lodging_fallback=lodging_reimbursement_limit())
            travel_rate = contract_rate(db(), order_id, work_date, "travel_hours", legacy_rates=rates,
                                        lodging_fallback=lodging_reimbursement_limit())
            public_rate = contract_rate(db(), order_id, work_date, "public_transport_hours", legacy_rates=rates,
                                        lodging_fallback=lodging_reimbursement_limit())
            overtime_rate = contract_rate(db(), order_id, work_date, "overtime_hours", legacy_rates=rates,
                                          lodging_fallback=lodging_reimbursement_limit())
            holiday_rate = contract_rate(db(), order_id, work_date, "holiday_hours", legacy_rates=rates,
                                         lodging_fallback=lodging_reimbursement_limit())
            mileage_rate = contract_rate(db(), order_id, work_date, "mileage", legacy_rates=rates,
                                         lodging_fallback=lodging_reimbursement_limit())
            if mode == "rental_drive":
                billing_miles = 0
            elif report["mileage_billing_method"] == "per_vehicle":
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "legacy"} else 0
            else:
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "following", "legacy"} else 0
            row = {
                "source_report_id": report["id"],
                "source_worker_user_id": worker["worker_user_id"],
                "worker_name": worker["name"],
                "project_date": work_date,
                "standard_hours": labor_hours["standard_hours"],
                "transport_hours": labor_hours["travel_hours"],
                "public_transport_hours": labor_hours["public_transport_hours"],
                "overtime_hours": labor_hours["overtime_hours"],
                "holiday_hours": labor_hours["holiday_hours"],
                "standard_rate": standard_rate["rate"],
                "transport_rate": travel_rate["rate"],
                "public_transport_rate": public_rate["rate"],
                "overtime_rate": overtime_rate["rate"],
                "holiday_rate": holiday_rate["rate"],
                "lodging": 0,
                "airfare": 0,
                "baggage": 0,
                "rental_car": 0,
                "fuel": 0,
                "parking": 0,
                "taxi": 0,
                "miles": billing_miles or 0,
                "mileage_rate": mileage_rate["rate"],
                "other": 0,
            }
            rows.append(calculate_customer_reimbursement_item(row, len(rows)))
    return rows


def merge_service_reports_into_customer_reimbursement(rows, order_id):
    seeded_rows = customer_reimbursement_seed_rows(order_id)
    existing_rows = [dict(row) for row in rows]
    used_indexes = set()
    source_lookup = {}
    for index, row in enumerate(existing_rows):
        if row.get("source_report_id") and row.get("source_worker_user_id"):
            source_lookup[(row["source_report_id"], row["source_worker_user_id"])] = index

    merged = []
    expense_fields = ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
    for seeded in seeded_rows:
        match_index = source_lookup.get((seeded["source_report_id"], seeded["source_worker_user_id"]))
        if match_index is None:
            seeded_name = normalized_project_name(seeded["worker_name"]).casefold()
            for index, existing in enumerate(existing_rows):
                if index in used_indexes or existing.get("source_report_id"):
                    continue
                if (
                    normalized_project_name(existing.get("worker_name")).casefold() == seeded_name
                    and str(existing.get("project_date") or "") == str(seeded["project_date"])
                ):
                    match_index = index
                    break
        if match_index is not None:
            used_indexes.add(match_index)
            existing = existing_rows[match_index]
            for field_name in expense_fields:
                seeded[field_name] = existing.get(field_name, 0)
        merged.append(calculate_customer_reimbursement_item(seeded, len(merged)))

    for index, existing in enumerate(existing_rows):
        if index in used_indexes or existing.get("source_report_id"):
            continue
        merged.append(calculate_customer_reimbursement_item(existing, len(merged)))
    return merged


def excessive_following_mileage_rows(order_id, threshold=500):
    return db().execute(
        """
        select service_reports.id as report_id, service_reports.report_date,
               users.name as worker_name, service_report_workers.driving_miles
        from service_report_workers
        join service_reports on service_reports.id = service_report_workers.report_id
        join users on users.id = service_report_workers.user_id
        where service_reports.service_order_id = ?
          and service_report_workers.travel_mode = 'following'
          and service_report_workers.driving_miles > ?
        order by service_reports.report_date, users.name
        """,
        (order_id, threshold),
    ).fetchall()


def customer_reimbursement_item_expense_amount(row, field_name):
    def value(key):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            return 0

    return money_decimal(value(field_name)) + money_decimal(value(f"auto_{field_name}"))


def calculate_customer_reimbursement_item(row, sort_order=0):
    labor_total = (
        decimal_value(row.get("standard_hours")) * money_decimal(row.get("standard_rate"))
        + decimal_value(row.get("transport_hours")) * money_decimal(row.get("transport_rate"))
        + decimal_value(row.get("public_transport_hours")) * money_decimal(row.get("public_transport_rate"))
        + decimal_value(row.get("overtime_hours")) * money_decimal(row.get("overtime_rate"))
        + decimal_value(row.get("holiday_hours")) * money_decimal(row.get("holiday_rate"))
    )
    mileage_total = decimal_value(row.get("miles")) * money_decimal(row.get("mileage_rate"))
    travel_fields = ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
    travel_total = sum(
        (
            customer_reimbursement_item_expense_amount(row, key)
            for key in travel_fields
        ),
        Decimal("0"),
    )
    row["labor_total"] = money_float(labor_total)
    row["mileage_total"] = money_float(mileage_total)
    row["total"] = money_float(money_decimal(labor_total) + money_decimal(mileage_total) + travel_total)
    row["sort_order"] = sort_order
    return row


CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS = {
    project_name_key("Accommodation/Lodging"): "lodging",
    project_name_key("Airfare"): "airfare",
    project_name_key("Car Rental Fee"): "rental_car",
    project_name_key("Checked Baggage Fee"): "baggage",
    project_name_key("Fuel Expenses"): "fuel",
    project_name_key("Parking Charge"): "parking",
    project_name_key("Taxi Fare / Ride-Hailing Fare"): "taxi",
    project_name_key("MRO Supplies"): "other",
}


def canonical_project_mapping_key(value, canonical_keys):
    key = project_name_key(value)
    if key in canonical_keys:
        return key
    for canonical_key in canonical_keys:
        if not key.startswith(canonical_key):
            continue
        suffix = key[len(canonical_key):].lstrip()
        if suffix and (not suffix[0].isascii() or not suffix[0].isalnum()):
            return canonical_key
    return key


def customer_reimbursement_expense_field(project_name, fuel_vehicle_type=None):
    project_key = canonical_project_mapping_key(
        project_name,
        CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS,
    )
    field_name = CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS.get(project_key)
    if field_name == "fuel" and fuel_vehicle_type != "rental":
        return None
    return field_name


def approved_customer_reimbursement_expense_rows(order_id, cutoff_at=None):
    return db().execute(
        """
        select expense_items.amount, expense_items.fuel_vehicle_type,
               expenses.id as expense_id, expenses.expense_number, expense_items.line_key,
               expense_items.description as item_description,
               (select count(*) from expense_items ordinal where ordinal.expense_id = expenses.id
                and (ordinal.sort_order < expense_items.sort_order or (ordinal.sort_order = expense_items.sort_order and ordinal.id <= expense_items.id))) as line_number,
               coalesce(projects.name, expense_items.project) as project_name,
               expenses.expense_date, users.name as worker_name
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        join users on users.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        left join projects on projects.id = expense_items.project_id
        where expenses.service_order_id = ? and expenses.status = 'approved'
          and (? is null or coalesce(expenses.reviewed_at, expenses.updated_at, expenses.created_at) <= ?)
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        (order_id, cutoff_at, cutoff_at),
    ).fetchall()


def merge_approved_expenses_into_customer_reimbursement(
    rows, order_id, cutoff_at=None, reimbursement_id=None, selection_mode="legacy"
):
    auto_fields = tuple(f"auto_{name}" for name in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"))
    merged = [dict(row) for row in rows]
    # Historical drafts keep the old auto-transfer behavior until someone opens
    # the new review pool and explicitly saves a selection.  New/manual-review
    # settlements are rebuilt only from the persisted selected expense lines.
    if selection_mode == "manual_review":
        expense_rows = linked_approved_expense_rows(globals(), reimbursement_id, order_id, cutoff_at)
    else:
        expense_rows = approved_customer_reimbursement_expense_rows(order_id, cutoff_at)

    for row in merged:
        row["auto_expense_sources"] = {}
        for field_name in auto_fields:
            row[field_name] = 0

    rates = customer_reimbursement_rates()
    row_lookup = {
        (normalized_project_name(row.get("worker_name")).casefold(), str(row.get("project_date") or "")): row
        for row in merged
    }
    for expense_item in expense_rows:
        field_name = customer_reimbursement_expense_field(
            expense_item["project_name"], expense_item["fuel_vehicle_type"]
        )
        if not field_name:
            continue
        key = (
            normalized_project_name(expense_item["worker_name"]).casefold(),
            str(expense_item["expense_date"] or ""),
        )
        row = row_lookup.get(key)
        if row is None:
            row = {
                "worker_name": expense_item["worker_name"],
                "project_date": expense_item["expense_date"],
                "standard_hours": 0, "transport_hours": 0, "overtime_hours": 0, "holiday_hours": 0,
                "standard_rate": rates["æ ‡å‡†å·¥æ—¶"], "transport_rate": rates["äº¤é€šå·¥æ—¶"],
                "overtime_rate": rates["åŠ ç­å·¥æ—¶"], "holiday_rate": rates["èŠ‚å‡æ—¥å·¥æ—¶"],
                "lodging": 0, "airfare": 0, "baggage": 0, "rental_car": 0,
                "fuel": 0, "parking": 0, "taxi": 0, "other": 0,
                "miles": 0, "mileage_rate": rates["é‡Œç¨‹è´¹"],
            }
            for auto_field in auto_fields:
                row[auto_field] = 0
            merged.append(row)
            row_lookup[key] = row
        row.setdefault("auto_expense_sources", {}).setdefault(field_name, []).append({
            "expense_id": expense_item["expense_id"], "expense_number": expense_item["expense_number"],
            "line_key": expense_item["line_key"], "line_number": expense_item["line_number"],
            "worker_name": expense_item["worker_name"], "project_name": expense_item["project_name"],
            "description": expense_item["item_description"], "amount": expense_item["amount"],
            "date": expense_item["expense_date"],
        })
        auto_field = f"auto_{field_name}"
        row[auto_field] = money_float(money_decimal(row.get(auto_field)) + money_decimal(expense_item["amount"]))

    return [calculate_customer_reimbursement_item(row, index) for index, row in enumerate(merged)]


def approved_mro_supplies_total(order_id, cutoff_at=None):
    mro_key = project_name_key("MRO Supplies")
    total = sum(
        (
            money_decimal(row["amount"])
            for row in approved_customer_reimbursement_expense_rows(order_id, cutoff_at)
            if canonical_project_mapping_key(
                row["project_name"],
                CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS,
            ) == mro_key
        ),
        Decimal("0"),
    )
    return money_float(total)


def approved_rental_vehicle_fuel_total(order_id):
    row = db().execute(
        """
        select coalesce(sum(expense_items.amount), 0) as total
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        left join projects on projects.id = expense_items.project_id
        where expenses.service_order_id = ?
          and expenses.status = 'approved'
          and expense_items.fuel_vehicle_type = 'rental'
          and (
            lower(coalesce(projects.name, expense_items.project)) like '%fuel%'
            or lower(coalesce(projects.name, expense_items.project)) like '%gas%'
            or coalesce(projects.name, expense_items.project) like '%æ²¹è´¹%'
            or coalesce(projects.name, expense_items.project) like '%åŠ æ²¹%'
          )
        """,
        (order_id,),
    ).fetchone()
    return money_float(row["total"] if row else 0)


def customer_reimbursement_totals(items, mro_supplies_total=0, rental_fuel_total=0):
    labor_total = sum((money_decimal(item["labor_total"]) for item in items), Decimal("0"))
    lodging_total = sum(
        (customer_reimbursement_item_expense_amount(item, "lodging") for item in items),
        Decimal("0"),
    )
    manual_travel_total = sum(
        customer_reimbursement_item_expense_amount(item, key)
        for item in items
        for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi")
    ) or Decimal("0")
    other_total = sum(
        (customer_reimbursement_item_expense_amount(item, "other") for item in items),
        Decimal("0"),
    )
    mileage_total = sum((money_decimal(item["mileage_total"]) for item in items), Decimal("0"))
    mro_supplies_total = money_decimal(mro_supplies_total)
    rental_fuel_total = money_decimal(rental_fuel_total)
    employee_expense_total = sum(
        customer_reimbursement_item_expense_amount(item, key) - money_decimal(item[key])
        for item in items
        for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
    ) or Decimal("0")
    travel_total = manual_travel_total + rental_fuel_total
    total_amount = (
        sum((money_decimal(item["total"]) for item in items), Decimal("0"))
        + rental_fuel_total
    )
    return {
        "labor_total": money_float(labor_total),
        "lodging_total": money_float(lodging_total),
        "travel_total": money_float(travel_total),
        "other_total": money_float(other_total),
        "mileage_total": money_float(mileage_total),
        "mro_supplies_total": money_float(mro_supplies_total),
        "rental_fuel_total": money_float(rental_fuel_total),
        "employee_expense_total": money_float(employee_expense_total),
        "total_amount": money_float(total_amount),
    }


def customer_reimbursement_person_days(order_id):
    row = db().execute(
        """
        select count(*) as count
        from (
            select service_report_workers.user_id,
                   date(coalesce(service_reports.actual_work_date, service_reports.report_date)) as work_date
            from service_reports
            join service_report_workers on service_report_workers.report_id = service_reports.id
            where service_reports.service_order_id = ?
            group by service_report_workers.user_id,
                     date(coalesce(service_reports.actual_work_date, service_reports.report_date))
        ) person_days
        """,
        (order_id,),
    ).fetchone()
    return int(row["count"] or 0)


def customer_reimbursement_column_totals(items):
    totals = {}
    for field in ("standard_hours", "transport_hours", "public_transport_hours", "overtime_hours", "holiday_hours", "miles"):
        totals[field] = sum(float(item[field] or 0) for item in items)
    for field in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"):
        totals[field] = money_float(sum(
            (customer_reimbursement_item_expense_amount(item, field) for item in items),
            Decimal("0"),
        ))
    for field in ("labor_total", "mileage_total", "total"):
        totals[field] = money_float(sum((money_decimal(item[field]) for item in items), Decimal("0")))
    return totals


def customer_reimbursement_items(reimbursement_id):
    return db().execute(
        """
        select *
        from customer_reimbursement_items
        where customer_reimbursement_id = ?
        order by sort_order, id
        """,
        (reimbursement_id,),
    ).fetchall()


def get_customer_reimbursement_attachments(reimbursement_id):
    return db().execute(
        """
        select customer_reimbursement_attachments.*, users.name as uploader_name
        from customer_reimbursement_attachments
        left join users on users.id = customer_reimbursement_attachments.uploaded_by
        where customer_reimbursement_id = ?
        order by uploaded_at desc, id desc
        """,
        (reimbursement_id,),
    ).fetchall()


def save_customer_reimbursement_attachment(reimbursement_id, uploaded):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"é™„ä»¶åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    uploaded.save(os.path.join(customer_reimbursement_attachment_dir(reimbursement_id), stored_filename))
    db().execute(
        """
        insert into customer_reimbursement_attachments (
            customer_reimbursement_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (reimbursement_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )


def save_customer_reimbursement_uploads(reimbursement_id):
    for uploaded in uploaded_attachments_from_request():
        save_customer_reimbursement_attachment(reimbursement_id, uploaded)


def unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename):
    original_filename = os.path.basename(original_filename or "attachment").strip() or "attachment"
    stem, extension = os.path.splitext(original_filename)
    stem = stem or "attachment"
    existing = {
        normalized_attachment_filename(row["original_filename"])
        for row in db().execute(
            "select original_filename from customer_reimbursement_attachments where customer_reimbursement_id = ?",
            (reimbursement_id,),
        ).fetchall()
    }
    candidate = original_filename
    counter = 2
    while normalized_attachment_filename(candidate) in existing:
        candidate = f"{stem}-{counter}{extension}"
        counter += 1
    return candidate


def copy_file_to_customer_reimbursement_attachment(
    reimbursement_id,
    source_path,
    original_filename,
    content_type=None,
    uploaded_by=None,
    source_expense_attachment_id=None,
):
    if not os.path.isfile(source_path):
        return False
    original_filename = unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename)
    extension = os.path.splitext(original_filename)[1].lstrip(".").lower() or "dat"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    destination_path = os.path.join(customer_reimbursement_attachment_dir(reimbursement_id), stored_filename)
    shutil.copyfile(source_path, destination_path)
    try:
        db().execute(
            """
            insert into customer_reimbursement_attachments (
                customer_reimbursement_id, original_filename, stored_filename, content_type,
                source_expense_attachment_id, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reimbursement_id,
                original_filename,
                stored_filename,
                content_type,
                source_expense_attachment_id,
                uploaded_by or g.user["id"],
                now(),
            ),
        )
    except sqlite3.Error:
        try:
            os.remove(destination_path)
        except FileNotFoundError:
            pass
        raise
    return True


def copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, service_order_id):
    rows = db().execute(
        """
        select service_report_attachments.*, service_reports.report_date
        from service_report_attachments
        join service_reports on service_reports.id = service_report_attachments.report_id
        where service_reports.service_order_id = ?
          and service_report_attachments.category = 'mileage_proof'
        order by service_reports.report_date asc, service_report_attachments.uploaded_at asc,
                 service_report_attachments.id asc
        """,
        (service_order_id,),
    ).fetchall()
    copied = 0
    for row in rows:
        original_name = row["original_filename"] or "mileage-proof"
        report_date = (row["report_date"] or "").strip()
        if report_date:
            original_name = f"é‡Œç¨‹ä½è¯-{report_date}-{original_name}"
        if copy_file_to_customer_reimbursement_attachment(
            reimbursement_id,
            report_attachment_path(row),
            original_name,
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
    return copied


def customer_reimbursement_items_from_form(order_id=None):
    field_names = [
        "worker_name", "project_date", "standard_hours", "transport_hours", "public_transport_hours", "overtime_hours", "holiday_hours",
        "lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "miles", "other",
    ]
    money_fields = {"lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"}
    rates = customer_reimbursement_rates()
    posted = {name: request.form.getlist(name) for name in field_names}
    source_report_ids = request.form.getlist("source_report_id")
    source_worker_user_ids = request.form.getlist("source_worker_user_id")
    count = max((len(values) for values in posted.values()), default=0)
    rows = []
    for index in range(count):
        worker_name = posted["worker_name"][index].strip() if index < len(posted["worker_name"]) else ""
        project_date = posted["project_date"][index].strip() if index < len(posted["project_date"]) else ""
        if not worker_name and not project_date:
            continue
        row = {
            "worker_name": worker_name,
            "project_date": project_date,
            "source_report_id": (
                int(source_report_ids[index])
                if index < len(source_report_ids) and source_report_ids[index].isdigit()
                else None
            ),
            "source_worker_user_id": (
                int(source_worker_user_ids[index])
                if index < len(source_worker_user_ids) and source_worker_user_ids[index].isdigit()
                else None
            ),
        }
        for name in field_names[2:]:
            value = posted[name][index] if index < len(posted[name]) else 0
            row[name] = money_float(value) if name in money_fields else to_float(value)
        if order_id is None:
            effective_rates = {
                "standard_rate": rates["æ ‡å‡†å·¥æ—¶"],
                "transport_rate": rates["äº¤é€šå·¥æ—¶"],
                "public_transport_rate": rates["äº¤é€šå·¥æ—¶"],
                "overtime_rate": rates["åŠ ç­å·¥æ—¶"],
                "holiday_rate": rates["èŠ‚å‡æ—¥å·¥æ—¶"],
                "mileage_rate": rates["é‡Œç¨‹è´¹"],
            }
        else:
            effective_rates = {
                "standard_rate": contract_rate(db(), order_id, project_date, "regular_hours", legacy_rates=rates,
                                                lodging_fallback=lodging_reimbursement_limit())["rate"],
                "transport_rate": contract_rate(db(), order_id, project_date, "travel_hours", legacy_rates=rates,
                                                 lodging_fallback=lodging_reimbursement_limit())["rate"],
                "public_transport_rate": contract_rate(db(), order_id, project_date, "public_transport_hours", legacy_rates=rates,
                                                        lodging_fallback=lodging_reimbursement_limit())["rate"],
                "overtime_rate": contract_rate(db(), order_id, project_date, "overtime_hours", legacy_rates=rates,
                                                lodging_fallback=lodging_reimbursement_limit())["rate"],
                "holiday_rate": contract_rate(db(), order_id, project_date, "holiday_hours", legacy_rates=rates,
                                               lodging_fallback=lodging_reimbursement_limit())["rate"],
                "mileage_rate": contract_rate(db(), order_id, project_date, "mileage", legacy_rates=rates,
                                               lodging_fallback=lodging_reimbursement_limit())["rate"],
            }
        row.update(effective_rates)
        if not row["worker_name"] or not row["project_date"]:
            raise ValueError("å·¥å•ç»“ç®—æ¯ä¸€è¡Œéƒ½å¿…é¡»æœ‰å§“åå’Œé¡¹ç›®æ—¶é—´ã€‚")
        validate_fuel_reimbursement_allowed(row["fuel"], "å·¥å•ç»“ç®—æ²¹è´¹")
        rows.append(calculate_customer_reimbursement_item(row, len(rows)))
    if not rows:
        raise ValueError("å·¥å•ç»“ç®—è‡³å°‘éœ€è¦ä¸€è¡Œæ˜ç»†ã€‚")
    return rows


def save_customer_reimbursement_items(reimbursement_id, rows):
    db().execute("delete from customer_reimbursement_items where customer_reimbursement_id = ?", (reimbursement_id,))
    for row in rows:
        db().execute(
            """
            insert into customer_reimbursement_items (
                customer_reimbursement_id, source_report_id, source_worker_user_id,
                worker_name, project_date, standard_hours, transport_hours, public_transport_hours,
                overtime_hours, holiday_hours, standard_rate, transport_rate, public_transport_rate, overtime_rate, holiday_rate,
                labor_total, lodging, airfare, baggage, rental_car, fuel, parking, taxi, miles, mileage_rate,
                mileage_total, other, auto_lodging, auto_airfare, auto_baggage, auto_rental_car,
                auto_fuel, auto_parking, auto_taxi, auto_other, total, sort_order
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reimbursement_id, row.get("source_report_id"), row.get("source_worker_user_id"),
                row["worker_name"], row["project_date"], row["standard_hours"], row["transport_hours"], row.get("public_transport_hours", 0),
                row["overtime_hours"], row["holiday_hours"], row["standard_rate"], row["transport_rate"], row.get("public_transport_rate", 0),
                row["overtime_rate"], row["holiday_rate"], row["labor_total"], row["lodging"], row["airfare"],
                row["baggage"], row["rental_car"], row["fuel"], row["parking"], row["taxi"], row["miles"],
                row["mileage_rate"], row["mileage_total"], row["other"],
                row.get("auto_lodging", 0), row.get("auto_airfare", 0), row.get("auto_baggage", 0),
                row.get("auto_rental_car", 0), row.get("auto_fuel", 0), row.get("auto_parking", 0),
                row.get("auto_taxi", 0), row.get("auto_other", 0), row["total"], row["sort_order"],
            ),
        )

        sources = row.get("auto_expense_sources", {})
        db().execute("update customer_reimbursement_items set auto_expense_sources = ? where customer_reimbursement_id = ? and sort_order = ?",
                     (sources if isinstance(sources, str) else json.dumps(sources, ensure_ascii=False), reimbursement_id, row["sort_order"]))


def latest_customer_reimbursement(order_id):
    return db().execute(
        """
        select customer_reimbursements.*, users.name as creator_name
        from customer_reimbursements
        left join users on users.id = customer_reimbursements.created_by
        where service_order_id = ?
        order by created_at desc, id desc
        limit 1
        """,
        (order_id,),
    ).fetchone()


def service_order_active_invoice(order_id):
    return db().execute(
        """
        select *
        from invoices
        where service_order_id = ? and status != 'void'
        order by issue_date desc, id desc
        limit 1
        """,
        (order_id,),
    ).fetchone()


def customer_reimbursement_linked_invoice(reimbursement, order_id):
    if reimbursement and reimbursement["invoice_id"]:
        invoice = db().execute(
            "select * from invoices where id = ?",
            (reimbursement["invoice_id"],),
        ).fetchone()
        if invoice:
            return invoice
    return service_order_active_invoice(order_id)


def customer_reimbursement_invoice_items(reimbursement):
    project_names = tuple(CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS.keys())
    projects = db().execute(
        """
        select *
        from projects
        where project_type = 'invoice'
        """
    ).fetchall()
    canonical_invoice_keys = tuple(project_name_key(name) for name in project_names)
    projects_by_name = {}
    for project in projects:
        canonical_key = canonical_project_mapping_key(project["name"], canonical_invoice_keys)
        if canonical_key not in canonical_invoice_keys:
            continue
        expected_name = project_names[canonical_invoice_keys.index(canonical_key)]
        current = projects_by_name.get(expected_name)
        if current is None or project_name_key(project["name"]) == canonical_key:
            projects_by_name[expected_name] = project
    missing = [name for name in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS if name not in projects_by_name]
    if missing:
        raise ValueError(f"è¯·å…ˆåœ¨é¡¹ç›®ç»´æŠ¤ä¸­åˆ›å»ºå‘ç¥¨é¡¹ç›®ï¼š{', '.join(missing)}ã€‚")
    items = []
    for project_name, amount_field in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS.items():
        amount = float(reimbursement[amount_field] or 0)
        if amount <= 0:
            continue
        project = projects_by_name[project_name]
        items.append(
            {
                "project_id": project["id"],
                "amount": round(amount, 2),
                "tax_rate": project["tax_rate"],
            }
        )
    if not items:
        raise ValueError("å·¥å•ç»“ç®—é‡‘é¢ä¸º 0ï¼Œæ— æ³•ç”Ÿæˆå‘ç¥¨ã€‚")
    return items


def create_customer_reimbursement(order, expense_selection_mode="legacy"):
    file_name = f"è´¹ç”¨æŠ¥é”€å•{order['client_order_number']}.pdf"
    cursor = db().execute(
        """
        insert into customer_reimbursements (
            service_order_id, file_name, stored_filename, expense_selection_mode, created_by, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (order["id"], file_name, f"{secrets.token_hex(12)}.pdf", expense_selection_mode, g.user["id"], now()),
    )
    reimbursement_id = cursor.lastrowid
    rows = customer_reimbursement_seed_rows(order["id"])
    if not rows:
        raise ValueError("è¿™ä¸ªå·¥å•è¿˜æ²¡æœ‰å¯ç”¨äºç”Ÿæˆå·¥å•ç»“ç®—çš„å·¥ä½œæ—¥æŠ¥ã€‚")
    save_customer_reimbursement_items(reimbursement_id, rows)
    copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, order["id"])
    update_customer_reimbursement_totals(reimbursement_id, rows)
    return db().execute("select * from customer_reimbursements where id = ?", (reimbursement_id,)).fetchone()


def sync_expense_attachments_to_settlement(order_id):
    reimbursement = latest_customer_reimbursement(order_id)
    if not reimbursement or reimbursement["status"] not in {"draft", "returned"}:
        return 0
    manual_review = reimbursement["expense_selection_mode"] == "manual_review"
    selected_ids = selected_expense_item_ids(db(), reimbursement["id"]) if manual_review else set()

    # Remove previously copied source attachments when their expense line is no
    # longer selected. Uploaded/manual settlement attachments are never touched.
    if manual_review:
        copied_sources = db().execute("""
            select ca.id as settlement_attachment_id, ca.stored_filename, ea.expense_id, ea.expense_item_key,
                   ei.id as expense_item_id
            from customer_reimbursement_attachments ca
            join expense_attachments ea on ea.id = ca.source_expense_attachment_id
            left join expense_items ei on ei.expense_id = ea.expense_id and ei.line_key = ea.expense_item_key
            where ca.customer_reimbursement_id = ? and ca.source_expense_attachment_id is not null
        """, (reimbursement["id"],)).fetchall()
        for copied in copied_sources:
            keep = copied["expense_item_id"] in selected_ids if copied["expense_item_id"] else db().execute(
                "select 1 from expense_items where expense_id = ? and id in ({}) limit 1".format(
                    ",".join("?" for _ in selected_ids) or "null"
                ),
                [copied["expense_id"], *selected_ids],
            ).fetchone() is not None
            if keep:
                continue
            path = os.path.join(customer_reimbursement_attachment_dir(reimbursement["id"]), copied["stored_filename"])
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            db().execute("delete from customer_reimbursement_attachments where id = ?", (copied["settlement_attachment_id"],))

    attachments = db().execute("""
        select ea.*, e.id as source_expense_id, ei.id as expense_item_id,
               coalesce(p.name, ei.project, e.project) as project_name, ei.fuel_vehicle_type
        from expense_attachments ea join expenses e on e.id = ea.expense_id
        left join expense_items ei on ei.expense_id = e.id and ei.line_key = ea.expense_item_key
        left join projects p on p.id = ei.project_id
        where e.service_order_id = ? and not exists
          (select 1 from customer_reimbursement_attachments ca where ca.source_expense_attachment_id = ea.id)
        order by ea.id""", (order_id,)).fetchall()
    copied = 0
    for attachment in attachments:
        if manual_review:
            if attachment["expense_item_id"]:
                if attachment["expense_item_id"] not in selected_ids:
                    continue
            else:
                if not selected_ids:
                    continue
                placeholders = ",".join("?" for _ in selected_ids)
                if not db().execute(
                    f"select 1 from expense_items where expense_id = ? and id in ({placeholders}) limit 1",
                    [attachment["source_expense_id"], *selected_ids],
                ).fetchone():
                    continue
        project_key = canonical_project_mapping_key(attachment["project_name"], CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS)
        if ((project_key == project_name_key("Fuel Expenses") and attachment["fuel_vehicle_type"] != "rental")
                or normalized_project_name(attachment["project_name"]) == "ä¸ªäººè‡ªé©¾æ²¹è´¹"):
            continue
        if copy_file_to_customer_reimbursement_attachment(reimbursement["id"], expense_attachment_path(attachment),
                attachment["original_filename"], attachment["content_type"], attachment["uploaded_by"],
                source_expense_attachment_id=attachment["id"]):
            copied += 1
    return copied


def update_customer_reimbursement_totals(reimbursement_id, rows=None):
    rows = rows if rows is not None else customer_reimbursement_items(reimbursement_id)
    reimbursement = db().execute(
        "select service_order_id, expense_transfer_cutoff_at, expense_selection_mode from customer_reimbursements where id = ?",
        (reimbursement_id,),
    ).fetchone()
    if not reimbursement:
        return customer_reimbursement_totals(rows)
    rows = merge_service_reports_into_customer_reimbursement(rows, reimbursement["service_order_id"])
    rows = merge_approved_expenses_into_customer_reimbursement(
        rows,
        reimbursement["service_order_id"],
        reimbursement["expense_transfer_cutoff_at"],
        reimbursement_id,
        reimbursement["expense_selection_mode"],
    )
    save_customer_reimbursement_items(reimbursement_id, rows)
    sync_expense_attachments_to_settlement(reimbursement["service_order_id"])
    mro_total = (
        selected_expense_total(globals(), reimbursement_id, "other")
        if reimbursement["expense_selection_mode"] == "manual_review"
        else approved_mro_supplies_total(
            reimbursement["service_order_id"], reimbursement["expense_transfer_cutoff_at"]
        )
    )
    totals = customer_reimbursement_totals(rows, mro_total)
    db().execute(
        """
        update customer_reimbursements
        set labor_total = ?, lodging_total = ?, travel_total = ?, mileage_total = ?,
            mro_supplies_total = ?, rental_fuel_total = ?, total_amount = ?
        where id = ?
        """,
        (
            totals["labor_total"], totals["lodging_total"], totals["travel_total"],
            totals["mileage_total"], totals["mro_supplies_total"], totals["rental_fuel_total"],
            totals["total_amount"], reimbursement_id,
        ),
    )
    return totals


def ensure_customer_reimbursement_pdf_record(reimbursement, order):
    if reimbursement["file_name"].lower().endswith(".pdf") and reimbursement["stored_filename"].lower().endswith(".pdf"):
        return reimbursement
    db().execute(
        """
        update customer_reimbursements
        set file_name = ?, stored_filename = ?
        where id = ?
        """,
        (
            f"è´¹ç”¨æŠ¥é”€å•{order['client_order_number']}.pdf",
            f"{secrets.token_hex(12)}.pdf",
            reimbursement["id"],
        ),
    )
    return db().execute("select * from customer_reimbursements where id = ?", (reimbursement["id"],)).fetchone()


def reimbursement_number(value):
    number = float(value or 0)
    return f"{number:,.2f}" if number else ""


def reimbursement_paragraph(value, style):
    text = "" if value is None else str(value)
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), style)


def build_customer_reimbursement_pdf(reimbursement, order, rows):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path = customer_reimbursement_file_path(reimbursement)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    title_style = ParagraphStyle(
        "CustomerReimbursementTitle",
        fontName="STSong-Light",
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#10233F"),
    )
    meta_style = ParagraphStyle(
        "CustomerReimbursementMeta",
        fontName="STSong-Light",
        fontSize=8,
        leading=11,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#475467"),
    )
    header_style = ParagraphStyle(
        "CustomerReimbursementHeader",
        fontName="STSong-Light",
        fontSize=6,
        leading=7,
        alignment=TA_CENTER,
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "CustomerReimbursementCell",
        fontName="STSong-Light",
        fontSize=6,
        leading=7,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#101828"),
    )
    total_style = ParagraphStyle(
        "CustomerReimbursementTotal",
        parent=cell_style,
        fontSize=6.5,
    )

    document = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=5 * mm,
        rightMargin=5 * mm,
        topMargin=7 * mm,
        bottomMargin=7 * mm,
        title=reimbursement["file_name"],
        author=get_company_profile()["name"],
    )

    header_group = [
        "å§“å", "é¡¹ç›®æ—¶é—´", "å·¥æ—¶", "", "", "", "", "", "", "", "", "", "", "ä½å®¿",
        "äº¤é€š", "", "", "", "", "", "é‡Œç¨‹", "", "", "å…¶ä»–", "åˆè®¡",
    ]
    header_detail = [
        "", "", "æ ‡å‡†\nå·¥æ—¶", "äº¤é€š\nå·¥æ—¶", "å…¬å…±äº¤é€š\nå·¥æ—¶", "åŠ ç­\nå·¥æ—¶", "èŠ‚å‡æ—¥\nå·¥æ—¶",
        "æ ‡å‡†å·¥æ—¶è´¹/å°æ—¶", "äº¤é€šå·¥æ—¶è´¹/å°æ—¶", "å…¬å…±äº¤é€šè´¹/å°æ—¶", "åŠ ç­å·¥æ—¶è´¹/å°æ—¶", "èŠ‚å‡æ—¥å·¥æ—¶è´¹/å°æ—¶",
        "å·¥æ—¶è´¹\nåˆè®¡", "", "æœºç¥¨", "è¡Œæ", "ç§Ÿè½¦", "åŠ æ²¹", "åœè½¦", "æ‰“è½¦",
        "è‹±é‡Œæ•°", "é‡Œç¨‹è´¹/è‹±é‡Œ", "é‡Œç¨‹è´¹", "", "",
    ]
    table_data = [
        [reimbursement_paragraph(value, header_style) for value in header_group],
        [reimbursement_paragraph(value, header_style) for value in header_detail],
    ]

    for item in rows:
        values = [
            item["worker_name"],
            item["project_date"],
            reimbursement_number(item["standard_hours"]),
            reimbursement_number(item["transport_hours"]),
            reimbursement_number(item["public_transport_hours"]),
            reimbursement_number(item["overtime_hours"]),
            reimbursement_number(item["holiday_hours"]),
            reimbursement_number(item["standard_rate"]),
            reimbursement_number(item["transport_rate"]),
            reimbursement_number(item["public_transport_rate"]),
            reimbursement_number(item["overtime_rate"]),
            reimbursement_number(item["holiday_rate"]),
            reimbursement_number(item["labor_total"]),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "lodging")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "airfare")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "baggage")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "rental_car")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "fuel")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "parking")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "taxi")),
            reimbursement_number(item["miles"]),
            reimbursement_number(item["mileage_rate"]),
            reimbursement_number(item["mileage_total"]),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "other")),
            reimbursement_number(item["total"]),
        ]
        table_data.append([reimbursement_paragraph(value, cell_style) for value in values])

    sum_fields = {
        2: "standard_hours", 3: "transport_hours", 4: "public_transport_hours", 5: "overtime_hours", 6: "holiday_hours",
        12: "labor_total", 13: "lodging", 14: "airfare", 15: "baggage", 16: "rental_car",
        17: "fuel", 18: "parking", 19: "taxi", 20: "miles", 22: "mileage_total",
        23: "other", 24: "total",
    }
    total_values = ["Total", ""] + [""] * 23
    for column_index, field_name in sum_fields.items():
        if field_name in {"lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"}:
            field_total = sum((customer_reimbursement_item_expense_amount(item, field_name) for item in rows), Decimal("0"))
        else:
            field_total = sum((money_decimal(item[field_name]) for item in rows), Decimal("0"))
        total_values[column_index] = reimbursement_number(field_total)
    table_data.append([reimbursement_paragraph(value, total_style) for value in total_values])

    column_widths = [
        width * 0.8 * mm
        for width in (
            20, 19, 10, 10, 11, 10, 11, 15, 15, 15, 15, 15, 15, 12,
            12, 12, 12, 12, 12, 12, 12, 14, 12, 12, 14,
        )
    ]
    table = LongTable(table_data, colWidths=column_widths, repeatRows=2, hAlign="CENTER")
    last_row = len(table_data) - 1
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (0, 1)),
                ("SPAN", (1, 0), (1, 1)),
                ("SPAN", (2, 0), (12, 0)),
                ("SPAN", (13, 0), (13, 1)),
                ("SPAN", (14, 0), (19, 0)),
                ("SPAN", (20, 0), (22, 0)),
                ("SPAN", (23, 0), (23, 1)),
                ("SPAN", (24, 0), (24, 1)),
                ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#0F766E")),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#166F68")),
                ("BACKGROUND", (0, last_row), (-1, last_row), colors.HexColor("#E7F6F2")),
                ("TEXTCOLOR", (0, last_row), (-1, last_row), colors.HexColor("#10233F")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#98A2B3")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#475467")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    pdf_totals = customer_reimbursement_totals(
        rows,
        reimbursement["mro_supplies_total"],
        reimbursement["rental_fuel_total"],
    )
    story = [
        Paragraph("è´¹ç”¨æŠ¥é”€å•", title_style),
        Spacer(1, 3 * mm),
        Paragraph(
            f"å·¥å•ç¼–å·ï¼š{order['order_number']}ã€€ã€€æœåŠ¡è®¢å•å·ç ï¼š{order['client_order_number']}ã€€ã€€ç«™ç‚¹ï¼š{order['client_name']}",
            meta_style,
        ),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 3 * mm),
        Paragraph(
            f"å…¶ä»–ï¼š{money(pdf_totals['other_total'])}ã€€ã€€"
            f"ç»“ç®—æ€»è®¡ï¼š{money(reimbursement['total_amount'])}",
            meta_style,
        ),
        Spacer(1, 4 * mm),
        Paragraph("å¤‡æ³¨ï¼š", meta_style),
    ]

    def draw_page_number(page_canvas, doc):
        page_canvas.saveState()
        page_canvas.setFont("STSong-Light", 7)
        page_canvas.setFillColor(colors.HexColor("#667085"))
        page_canvas.drawRightString(landscape(A4)[0] - 5 * mm, 3 * mm, f"ç¬¬ {doc.page} é¡µ")
        page_canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    return path


def remove_customer_reimbursement_pdf(reimbursement):
    path = customer_reimbursement_file_path(reimbursement)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def service_report_docx_filename(order, report, used_filenames=None):
    filename = secure_filename(
        f"{order['client_order_number']}-{report['report_date']}-report.docx"
    ) or f"service-report-{report['id']}.docx"
    if used_filenames is None or filename not in used_filenames:
        return filename
    stem, extension = os.path.splitext(filename)
    return f"{stem}-{report['id']}{extension or '.docx'}"


def service_order_report_word_attachments(order):
    reports = db().execute(
        """
        select * from service_reports
        where service_order_id = ?
        order by report_date, id
        """,
        (order["id"],),
    ).fetchall()
    attachments = []
    used_filenames = set()
    for report in reports:
        try:
            content = build_service_report_docx(report, order)
        except Exception as error:
            raise ValueError(f"å·¥ä½œæ—¥æŠ¥ {report['report_date']} Word æ–‡ä»¶ç”Ÿæˆå¤±è´¥ï¼Œè¯·æ£€æŸ¥æ—¥æŠ¥å†…å®¹æˆ–ç…§ç‰‡ã€‚") from error
        filename = service_report_docx_filename(order, report, used_filenames)
        used_filenames.add(filename)
        attachments.append(
            {
                "filename": filename,
                "content": content,
                "maintype": "application",
                "subtype": "vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        )
    return attachments


def customer_reimbursement_outgoing_attachments(reimbursement, order):
    reimbursement = ensure_customer_reimbursement_pdf_record(reimbursement, order)
    pdf_path = build_customer_reimbursement_pdf(
        reimbursement,
        order,
        customer_reimbursement_items(reimbursement["id"]),
    )
    attachments = []
    with open(pdf_path, "rb") as file:
        attachments.append(
            {
                "filename": reimbursement["file_name"],
                "content": file.read(),
                "maintype": "application",
                "subtype": "pdf",
            }
        )
    for attachment in get_customer_reimbursement_attachments(reimbursement["id"]):
        path = customer_reimbursement_attachment_path(attachment)
        if not os.path.exists(path):
            continue
        maintype, _, subtype = (attachment["content_type"] or "application/octet-stream").partition("/")
        with open(path, "rb") as file:
            attachments.append(
                {
                    "filename": attachment["original_filename"],
                    "content": file.read(),
                    "maintype": maintype or "application",
                    "subtype": subtype or "octet-stream",
                }
            )
    attachments.extend(service_order_report_word_attachments(order))
    return reimbursement, attachments


def deliver_customer_reimbursement_email(reimbursement, order):
    client = db().execute("select * from clients where id = ?", (order["client_id"],)).fetchone() if order["client_id"] else None
    if not client:
        raise ValueError("è¿™ä¸ªå·¥å•è¿˜æ²¡æœ‰å…³è”å®¢æˆ·ï¼Œè¯·å…ˆç¼–è¾‘å·¥å•é€‰æ‹©å®¢æˆ·ã€‚")
    recipient = (client["email"] or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient):
        raise ValueError(f"å®¢æˆ· {client['name']} æ²¡æœ‰æœ‰æ•ˆé‚®ç®±ï¼Œè¯·å…ˆç»´æŠ¤å®¢æˆ·ä¸»æ•°æ®ã€‚")
    subject = f"Customer Reimbursement Statement {order['client_order_number']}"
    body = (
        f"Dear {client['contact_name'] or client['name']},\n\n"
        "Please find the customer reimbursement statement and supporting documents attached."
    )
    reimbursement, attachments = customer_reimbursement_outgoing_attachments(reimbursement, order)
    send_email(
        to=recipient,
        subject=subject,
        html=f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
        attachments=attachments,
    )
    record_email_delivery("customer_reimbursement", reimbursement["id"], recipient, subject)
    log_action(
        "send",
        "customer_reimbursement",
        reimbursement["id"],
        reimbursement["file_name"],
        f"å‘é€è‡³ï¼š{recipient}ï¼›é™„ä»¶ï¼š{len(attachments)} ä¸ª",
    )
    return recipient


def expense_beneficiary_id(expense):
    return expense["beneficiary_id"] or expense["created_by"]


def expense_beneficiary_options(expense=None):
    # Keep a historical recipient selectable, even after their account is disabled.
    current_id = expense_beneficiary_id(expense) if expense is not None else g.user["id"]
    return db().execute(
        """select id, name, is_active from users
           where (is_active = 1 and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user'))
              or id = ?
           order by name, id""", (current_id,),
    ).fetchall()


def posted_expense_beneficiary(expense=None):
    default_id = expense_beneficiary_id(expense) if expense is not None else g.user["id"]
    raw = request.form.get("beneficiary_id", str(default_id)).strip()
    if not raw.isdigit():
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„æŠ¥é”€å½’å±å‘˜å·¥ã€‚")
    beneficiary = next((row for row in expense_beneficiary_options(expense) if row["id"] == int(raw)), None)
    if beneficiary is None:
        raise ValueError("åªèƒ½é€‰æ‹©åœ¨èŒçš„å†…éƒ¨å‘˜å·¥ï¼Œæˆ–ä¿ç•™æœ¬å•åŸæœ‰çš„æŠ¥é”€å½’å±å‘˜å·¥ã€‚")
    return beneficiary


def expense_access_filter():
    if normalized_role() in {"admin", "manager", "finance"}:
        return "1 = 1", []
    return "(expenses.created_by = ? or coalesce(expenses.beneficiary_id, expenses.created_by) = ?)", [g.user["id"], g.user["id"]]


def notify_expense_participants(expense, title, body, link):
    for user_id in {expense["created_by"], expense_beneficiary_id(expense)}:
        create_message(user_id, title, body, link)


def can_access_expense(expense):
    if not g.user:
        return False
    if normalized_role() in {"admin", "manager", "finance"}:
        return True
    return g.user["id"] in {expense["created_by"], expense_beneficiary_id(expense)}


def can_delete_expense(expense):
    if not g.user:
        return False
    if normalized_role() == "admin":
        return True
    if expense["status"] not in {"draft", "returned"}:
        return False
    if is_manager():
        return True
    return g.user["id"] in {expense["created_by"], expense_beneficiary_id(expense)}


def require_expense(expense_id):
    expense = db().execute("select * from expenses where id = ?", (expense_id,)).fetchone()
    if not expense:
        abort(404)
    if not can_access_expense(expense):
        abort(403)
    order = require_service_order(expense["service_order_id"])
    return expense, order


def expense_attachment_dir(expense_id):
    path = os.path.join(EXPENSE_ATTACHMENTS_DIR, str(expense_id))
    os.makedirs(path, exist_ok=True)
    return path


def expense_attachment_path(attachment):
    return os.path.join(EXPENSE_ATTACHMENTS_DIR, str(attachment["expense_id"]), attachment["stored_filename"])


def expense_attachment_fingerprints(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    image_hash = ""
    try:
        with Image.open(path) as image:
            pixels = list(ImageOps.grayscale(image).resize((9, 8)).getdata())
            bits = [pixels[row * 9 + column] > pixels[row * 9 + column + 1]
                    for row in range(8) for column in range(8)]
            image_hash = f"{sum(1 << index for index, bit in enumerate(bits) if bit):016x}"
    except (OSError, ValueError):
        pass
    return digest.hexdigest(), image_hash


def image_hash_distance(left, right):
    if not left or not right or len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def save_expense_attachment(expense_id, uploaded, expense_item_key=None):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"é™„ä»¶åªæ”¯æŒ {ALLOWED_ATTACHMENT_LABEL}ã€‚")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    stored_path = os.path.join(expense_attachment_dir(expense_id), stored_filename)
    uploaded.save(stored_path)
    file_sha256, image_dhash = expense_attachment_fingerprints(stored_path)
    return db().execute(
        """
        insert into expense_attachments (
            expense_id, expense_item_key, original_filename, stored_filename,
            content_type, uploaded_by, uploaded_at, file_sha256, image_dhash
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            expense_id, expense_item_key, original_filename, stored_filename,
            uploaded.content_type, g.user["id"], now(), file_sha256, image_dhash,
        ),
    ).lastrowid


def save_expense_uploads(expense_id, item_rows=None):
    for uploaded in uploaded_attachments_from_request():
        save_expense_attachment(expense_id, uploaded)
    for item in item_rows or []:
        line_key = item["line_key"]
        for uploaded in request.files.getlist(f"item_attachments_{line_key}"):
            save_expense_attachment(expense_id, uploaded, line_key)


def get_expense_attachments(expense_id):
    return db().execute(
        """
        select expense_attachments.*, users.name as uploader_name,
               transferred.id as transferred_attachment_id
        from expense_attachments left join users on users.id = expense_attachments.uploaded_by
        left join customer_reimbursement_attachments as transferred
          on transferred.source_expense_attachment_id = expense_attachments.id
        where expense_attachments.expense_id = ?
        order by expense_attachments.uploaded_at desc, expense_attachments.id desc
        """,
        (expense_id,),
    ).fetchall()


def expense_attachment_duplicate_context(attachment_id):
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


def ensure_expense_attachment_fingerprints(attachment):
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


def deepseek_duplicate_analysis(current, matched, reasons):
    settings = deepseek_assistant_settings()
    if not settings["enabled"] or not settings["api_key"]:
        return "", ""
    prompt = {
        "task": "åˆ¤æ–­ä¸¤æ¡å‘˜å·¥æŠ¥é”€è®°å½•æ˜¯å¦ç–‘ä¼¼ä¸ºåŒä¸€å¼ å•æ®ã€‚åªæ ¹æ®æä¾›çš„æ•°æ®åˆ¤æ–­ï¼Œä¸è¦è™šæ„å•æ®å·ç ã€‚",
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
        "response": "è¯·ç”¨ä¸­æ–‡åœ¨120å­—å†…è¯´æ˜é£é™©åŠéœ€è¦äººå·¥æ ¸å¯¹çš„å†…å®¹ã€‚",
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


def run_expense_duplicate_checks(expense_id, use_deepseek=False):
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
                reasons.append("æ–‡ä»¶å†…å®¹å®Œå…¨ç›¸åŒ")
                score = 100
            distance = image_hash_distance(current_dhash, matched_dhash)
            if score < 100 and distance is not None and distance <= 5:
                reasons.append("å›¾ç‰‡å†…å®¹é«˜åº¦ç›¸ä¼¼")
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
                reasons.append("æŠ¥é”€é‡‘é¢ç›¸åŒ")
                score += 15
            if days <= 3:
                reasons.append(f"è´¹ç”¨æ—¥æœŸç›¸è·{days}å¤©")
                score += 10
            if current["beneficiary_id"] == matched["beneficiary_id"]:
                reasons.append("æŠ¥é”€å½’å±å‘˜å·¥ç›¸åŒ")
                score += 10
            if (current["project_name"] or "").strip() == (matched["project_name"] or "").strip():
                reasons.append("è´¹ç”¨é¡¹ç›®ç›¸åŒ")
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
                 level, score, "ï¼›".join(reasons), analysis, analysis_error, timestamp, timestamp),
            )


def expense_duplicate_checks(expense_id):
    return db().execute(
        """
        select checks.*, matched.expense_number as matched_expense_number,
               current_attachment.original_filename as attachment_name,
               matched_attachment.original_filename as matched_attachment_name,
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


def service_report_workers(report_id):
    return db().execute(
        """
        select users.id, users.name, users.email, service_report_workers.driving_miles,
               service_report_workers.travel_mode, service_report_workers.travel_hours,
               service_report_workers.public_transport_hours, service_report_workers.work_description
        from service_report_workers
        join users on users.id = service_report_workers.user_id
        where service_report_workers.report_id = ?
        order by users.name
        """,
        (report_id,),
    ).fetchall()


def service_report_worker_options(order, report_id=None):
    params = [order["country_code"]]
    historical_clause = ""
    if report_id:
        historical_clause = """
          or users.id in (
              select user_id from service_report_workers where report_id = ?
          )
        """
        params.append(report_id)
    role_clause = (
        "users.id = ?"
        if is_external_employee()
        else "users.role in ('manager', 'finance', 'employee', 'external_employee')"
    )
    if is_external_employee():
        params.insert(0, g.user["id"])
    return db().execute(
        f"""
        select users.id, users.name, users.email, users.country_code
        from users
        where {role_clause}
          and users.is_active = 1
          and (
              users.country_code = ?
              {historical_clause}
          )
        order by users.name
        """,
        params,
    ).fetchall()


def report_writer_options():
    return db().execute(
        """
        select id, name, email from users
        where is_active = 1
          and role in ('manager', 'finance', 'employee', 'external_employee')
        order by name
        """
    ).fetchall()


def group_expense_attachments(attachments):
    common = []
    by_item = {}
    for attachment in attachments:
        line_key = (attachment["expense_item_key"] or "").strip()
        if line_key:
            by_item.setdefault(line_key, []).append(attachment)
        else:
            common.append(attachment)
    return common, by_item


def posted_report_writer_id():
    customer_report_choice, value = normalize_customer_report_choice(
        request.form.get("has_customer_report", ""),
        request.form.get("report_writer_id", ""),
    )
    if customer_report_choice == "no":
        return None
    if not value.isdigit():
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„å®¢æˆ·æ—¥æŠ¥å¡«å†™å‘˜å·¥ã€‚")
    row = db().execute(
        """select id from users where id = ? and is_active = 1
           and role in ('manager', 'finance', 'employee', 'external_employee')""",
        (int(value),),
    ).fetchone()
    if not row:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„å®¢æˆ·æ—¥æŠ¥å¡«å†™å‘˜å·¥ã€‚")
    return row["id"]


def report_parts(table, report_id):
    return db().execute(f"select * from {table} where report_id = ? order by sort_order, id", (report_id,)).fetchall()


def parse_part_rows(prefix, fields):
    values = [request.form.getlist(f"{prefix}_{field}") for field in fields]
    max_len = max([len(items) for items in values] + [0])
    rows = []
    for index in range(max_len):
        row = {field: values[pos][index].strip() if index < len(values[pos]) else "" for pos, field in enumerate(fields)}
        if any(row.values()):
            rows.append(row)
    return rows


def report_form_defaults(report=None, order=None):
    if report:
        data = dict(report)
        data["actual_work_date"] = data.get("actual_work_date") or data.get("report_date")
        # v0.1.243: early AI-generated reports stored ISO photo-timeline
        # timestamps (e.g. 2026-09-17T08:50:00) which broke the hour/minute
        # dropdowns. Normalize legacy values for rendering.
        try:
            from ai_daily_report.formal_save import normalize_report_time
            data["arrival_time"] = normalize_report_time(data.get("arrival_time")) or ""
            data["departure_time"] = normalize_report_time(data.get("departure_time")) or ""
        except Exception:
            pass
        return data
    return {
        "report_date": date.today().isoformat(),
        "actual_work_date": date.today().isoformat(),
        "total_service_hours": "",
        "travel_hours": "",
        "public_transport_hours": "",
        "driving_miles": "",
        "mileage_billing_method": "per_person",
        "departure_address": "",
        "site_address": order["site_address"] if order else "",
        "total_time": "",
        "cabinet_number": "",
        "arrival_time": "",
        "departure_time": "",
        "service_description": "",
        "report_writer_id": None,
    }


def save_report_detail_rows(report_id):
    worker_rows = report_worker_rows_from_form()
    worker_ids = [row["worker_user_id"] for row in worker_rows]
    placeholders = ",".join("?" for _ in worker_ids)
    report_order = db().execute(
        """
        select service_orders.country_code
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        where service_reports.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not report_order:
        raise ValueError("å·¥ä½œæ—¥æŠ¥å…³è”çš„å·¥å•ä¸å­˜åœ¨ã€‚")
    valid_workers = db().execute(
        f"""
        select id from users
        where role in ('manager', 'finance', 'employee', 'external_employee')
          and is_active = 1
          and (
              country_code = ?
              or id in (
                  select user_id from service_report_workers where report_id = ?
              )
          )
          and id in ({placeholders})
        """,
        (report_order["country_code"], report_id, *worker_ids),
    ).fetchall()
    valid_worker_ids = {str(row["id"]) for row in valid_workers}
    if valid_worker_ids != set(worker_ids):
        raise ValueError("æœåŠ¡äººå‘˜æ¸…å•åŒ…å«æ— æ•ˆç”¨æˆ·ï¼Œè¯·é‡æ–°é€‰æ‹©ã€‚")

    db().execute("delete from service_report_workers where report_id = ?", (report_id,))
    for worker in worker_rows:
        db().execute(
            """
            insert into service_report_workers (
                report_id, user_id, driving_miles, travel_mode, travel_hours,
                public_transport_hours, work_description
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, worker["worker_user_id"], worker["driving_miles"],
                worker["worker_travel_mode"], worker["travel_hours"],
                worker["public_transport_hours"], worker["worker_work_description"],
            ),
        )

    db().execute("delete from service_report_saved_parts where report_id = ?", (report_id,))
    for index, row in enumerate(parse_part_rows("saved", ["part_number", "part_name", "quantity", "status"])):
        db().execute(
            """
            insert into service_report_saved_parts (report_id, part_number, part_name, quantity, status, sort_order)
            values (?, ?, ?, ?, ?, ?)
            """,
            (report_id, row["part_number"], row["part_name"], row["quantity"], row["status"], index),
        )

    db().execute("delete from service_report_replaced_parts where report_id = ?", (report_id,))
    for index, row in enumerate(parse_part_rows("replaced", ["part_number", "part_name", "old_serial_number", "new_serial_number", "quantity"])):
        db().execute(
            """
            insert into service_report_replaced_parts (
                report_id, part_number, part_name, old_serial_number, new_serial_number, quantity, sort_order
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                row["part_number"],
                row["part_name"],
                row["old_serial_number"],
                row["new_serial_number"],
                row["quantity"],
                index,
            ),
        )


def load_invoice(invoice_id):
    invoice = require_invoice_access(invoice_id)
    client = db().execute("select * from clients where id = ?", (invoice["client_id"],)).fetchone()
    items = db().execute("select * from invoice_items where invoice_id = ?", (invoice_id,)).fetchall()
    return invoice, client, items


def invoice_service_order(invoice):
    if not invoice["service_order_id"]:
        return None
    return db().execute(
        "select id, order_number, client_order_number from service_orders where id = ?",
        (invoice["service_order_id"],),
    ).fetchone()


def create_message(user_id, title, body, link=None):
    db().execute(
        "insert into messages (user_id, title, body, link, created_at) values (?, ?, ?, ?, ?)",
        (user_id, title, body, link, now()),
    )


def log_action(action, entity_type, entity_id, entity_label, summary=""):
    if not g.user:
        return
    db().execute(
        """
        insert into audit_logs (
            user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            g.user["name"],
            action,
            entity_type,
            entity_id,
            str(entity_label or ""),
            str(summary or ""),
            now(),
        ),
    )


def record_email_delivery(entity_type, entity_id, recipient, subject):
    db().execute(
        """
        insert into email_delivery_logs (
            entity_type, entity_id, recipient, subject, sent_by, sent_by_name, sent_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            str(recipient or ""),
            str(subject or ""),
            g.user["id"] if g.user else None,
            g.user["name"] if g.user else "",
            now(),
        ),
    )


def email_delivery_summary(entity_type, entity_id):
    row = db().execute(
        """
        select count(*) as send_count,
               max(sent_at) as last_sent_at,
               max(is_legacy) as has_legacy
        from email_delivery_logs
        where entity_type = ? and entity_id = ?
        """,
        (entity_type, entity_id),
    ).fetchone()
    return {
        "count": int(row["send_count"] or 0),
        "last_sent_at": row["last_sent_at"] or "",
        "is_estimate": bool(row["has_legacy"]),
    }


def request_ip_address():
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded_for or request.headers.get("X-Real-IP", "").strip() or request.remote_addr or ""


def log_login_action(user):
    db().execute(
        """
        insert into audit_logs (
            user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            user["name"],
            "login",
            "user",
            user["id"],
            user["email"] or user["name"],
            f"è§’è‰²ï¼š{role_label(user['role'])}ï¼›IPï¼š{request_ip_address() or '-'}",
            now(),
        ),
    )


def normalized_address(value):
    return " ".join(str(value or "").strip().split()).casefold()


def parse_coordinate_pair(value):
    text = str(value or "").strip()
    if not text:
        return None
    parts = [part for part in re.split(r"[,ï¼Œ\s]+", text) if part]
    if len(parts) != 2:
        raise ValueError("è¯·ä¸€æ¬¡ç²˜è´´â€œçº¬åº¦, ç»åº¦â€ï¼Œä¾‹å¦‚ï¼š33.3252078, -112.7639562ã€‚")
    try:
        latitude, longitude = (float(part) for part in parts)
    except ValueError as error:
        raise ValueError("ç»çº¬åº¦åªèƒ½åŒ…å«æ•°å­—ï¼Œè¯·ä» Google åœ°å›¾å¤åˆ¶åç›´æ¥ç²˜è´´ã€‚") from error
    if not -90 <= latitude <= 90:
        raise ValueError("çº¬åº¦å¿…é¡»åœ¨ -90 åˆ° 90 ä¹‹é—´ã€‚")
    if not -180 <= longitude <= 180:
        raise ValueError("ç»åº¦å¿…é¡»åœ¨ -180 åˆ° 180 ä¹‹é—´ã€‚")
    return latitude, longitude


class TemporaryGeocodingError(Exception):
    pass


def fetch_geocoder_json(request_url):
    global _last_geocode_request_at
    with _geocode_lock:
        wait_seconds = 1.05 - (time.monotonic() - _last_geocode_request_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        request_object = Request(
            request_url,
            headers={
                "User-Agent": NOMINATIM_USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )
        try:
            with urlopen(request_object, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 429 or error.code >= 500:
                raise TemporaryGeocodingError(str(error)) from error
            raise
        except (URLError, TimeoutError, OSError) as error:
            raise TemporaryGeocodingError(str(error)) from error
        finally:
            _last_geocode_request_at = time.monotonic()
    return payload


def configured_country_codes():
    return {code.strip().casefold() for code in NOMINATIM_COUNTRY_CODES.split(",") if code.strip()}


def address_expectations(address):
    uppercase_address = str(address or "").upper()
    state_codes = [token for token in re.findall(r"\b[A-Z]{2}\b", uppercase_address) if token in US_STATE_CODES]
    zip_codes = re.findall(r"\b(\d{5})(?:-\d{4})?\b", uppercase_address)
    return state_codes[-1] if state_codes else None, zip_codes[-1] if zip_codes else None


def census_result_matches_address(match, address):
    expected_state, expected_zip = address_expectations(address)
    components = match.get("addressComponents", {})
    result_state = str(components.get("state", "")).upper()
    result_zip = str(components.get("zip", ""))[:5]
    if expected_state and result_state != expected_state:
        return False
    if expected_zip and result_zip and result_zip != expected_zip:
        return False
    return True


def geocode_with_google(address):
    api_key = get_google_geocoding_api_key()
    if not api_key:
        return None
    expected_state, expected_zip = address_expectations(address)
    query = {
        "address": address,
        "key": api_key,
        "region": "us",
    }
    try:
        payload = fetch_geocoder_json(f"{GOOGLE_GEOCODING_URL}?{urlencode(query)}")
    except TemporaryGeocodingError:
        raise
    except (HTTPError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if status in {"OVER_QUERY_LIMIT", "RESOURCE_EXHAUSTED", "UNKNOWN_ERROR"}:
        raise TemporaryGeocodingError(f"Google geocoding status: {status}")
    if status != "OK":
        app.logger.info("Google geocoding returned %s for address %s", status, address)
        return None
    for result in payload.get("results", []):
        components = result.get("address_components", [])
        result_state = ""
        result_zip = ""
        for component in components:
            types = set(component.get("types", []))
            if "administrative_area_level_1" in types:
                result_state = str(component.get("short_name", "")).upper()
            if "postal_code" in types:
                result_zip = str(component.get("long_name", ""))[:5]
        if expected_state and result_state and result_state != expected_state:
            continue
        if expected_zip and result_zip and result_zip != expected_zip:
            continue
        try:
            location = result["geometry"]["location"]
            return float(location["lat"]), float(location["lng"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def geocode_with_census(address):
    query = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    try:
        payload = fetch_geocoder_json(f"{CENSUS_GEOCODER_URL}?{urlencode(query)}")
    except TemporaryGeocodingError:
        raise
    except (HTTPError, ValueError, json.JSONDecodeError):
        return None
    matches = payload.get("result", {}).get("addressMatches", []) if isinstance(payload, dict) else []
    for match in matches:
        if not census_result_matches_address(match, address):
            continue
        try:
            coordinates = match["coordinates"]
            return float(coordinates["y"]), float(coordinates["x"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def nominatim_address_candidates(address):
    candidates = [address]
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 3:
        candidates.append(", ".join(parts[-3:]))
    zip_match = re.search(r"\b\d{5}(?:-\d{4})?\b", address)
    if zip_match:
        candidates.append(f"{zip_match.group(0)}, USA")
    unique_candidates = []
    seen = set()
    for candidate in candidates:
        key = normalized_address(candidate)
        if key and key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)
    return unique_candidates


def geocode_with_nominatim(address):
    country_codes = configured_country_codes()
    expected_state, expected_zip = address_expectations(address)
    for candidate in nominatim_address_candidates(address):
        query = {
            "q": candidate,
            "format": "jsonv2",
            "limit": "1",
            "addressdetails": "1",
        }
        if NOMINATIM_COUNTRY_CODES:
            query["countrycodes"] = NOMINATIM_COUNTRY_CODES
        try:
            payload = fetch_geocoder_json(f"{NOMINATIM_URL}?{urlencode(query)}")
        except TemporaryGeocodingError:
            raise
        except (HTTPError, ValueError, json.JSONDecodeError):
            continue
        if not payload:
            continue
        result = payload[0]
        result_address = result.get("address", {})
        result_country = str(result_address.get("country_code", "")).casefold()
        if country_codes and result_country and result_country not in country_codes:
            continue
        result_state = str(result_address.get("ISO3166-2-lvl4", "")).upper().removeprefix("US-")
        result_zip = str(result_address.get("postcode", ""))[:5]
        if expected_state and result_state and result_state != expected_state:
            continue
        if expected_zip and result_zip and result_zip != expected_zip:
            continue
        try:
            return float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return None


def geocode_address(address):
    if not GEOCODING_ENABLED:
        return None
    temporary_error = None
    try:
        coordinates = geocode_with_google(address)
        if coordinates:
            return coordinates
    except TemporaryGeocodingError as error:
        temporary_error = error
    if "us" in configured_country_codes():
        try:
            coordinates = geocode_with_census(address)
            if coordinates:
                return coordinates
        except TemporaryGeocodingError as error:
            temporary_error = error
    try:
        coordinates = geocode_with_nominatim(address)
        if coordinates:
            return coordinates
    except TemporaryGeocodingError as error:
        temporary_error = error
    if temporary_error:
        raise temporary_error
    return None


def geocode_service_order(order_id, force=False):
    order = db().execute("select * from service_orders where id = ?", (order_id,)).fetchone()
    if not order:
        return None
    address = str(order["site_address"] or "").strip()
    address_key = normalized_address(address)
    if not address_key:
        db().execute(
            """
            update service_orders
            set latitude = null, longitude = null, geocode_address = ?, geocode_status = 'failed',
                geocode_attempted_at = ?, geocode_version = ?
            where id = ?
            """,
            (address, now(), GEOCODER_VERSION, order_id),
        )
        return None
    if (
        not force
        and order["geocode_status"] == "success"
        and order["latitude"] is not None
        and order["longitude"] is not None
        and normalized_address(order["geocode_address"]) == address_key
        and order["geocode_version"] == GEOCODER_VERSION
    ):
        return float(order["latitude"]), float(order["longitude"])
    cached = db().execute(
        """
        select latitude, longitude
        from service_orders
        where id != ? and geocode_status = 'success'
          and latitude is not null and longitude is not null
          and lower(trim(geocode_address)) = lower(trim(?))
          and geocode_version = ?
        order by geocode_attempted_at desc, id desc
        limit 1
        """,
        (order_id, address, GEOCODER_VERSION),
    ).fetchone()
    if cached:
        coordinates = float(cached["latitude"]), float(cached["longitude"])
    else:
        try:
            coordinates = geocode_address(address)
        except TemporaryGeocodingError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            app.logger.warning("Unable to geocode service order %s: %s", order_id, error)
            coordinates = None
    if coordinates:
        db().execute(
            """
            update service_orders
            set latitude = ?, longitude = ?, geocode_address = ?, geocode_status = 'success',
                geocode_attempted_at = ?, geocode_version = ?
            where id = ?
            """,
            (coordinates[0], coordinates[1], address, now(), GEOCODER_VERSION, order_id),
        )
        return coordinates
    db().execute(
        """
        update service_orders
        set latitude = null, longitude = null, geocode_address = ?, geocode_status = 'failed',
            geocode_attempted_at = ?, geocode_version = ?
        where id = ?
        """,
        (address, now(), GEOCODER_VERSION, order_id),
    )
    return None


def geocode_buyer(buyer_id, force=False):
    buyer = db().execute("select * from buyers where id = ?", (buyer_id,)).fetchone()
    if not buyer:
        return None
    if buyer["manual_coordinates"] and buyer["latitude"] is not None and buyer["longitude"] is not None:
        return float(buyer["latitude"]), float(buyer["longitude"])
    address = str(buyer["detailed_address"] or "").strip()
    address_key = normalized_address(address)
    if not address_key:
        db().execute(
            """
            update buyers
            set latitude = null, longitude = null, geocode_address = ?, geocode_status = 'failed',
                geocode_attempted_at = ?, geocode_version = ?
            where id = ?
            """,
            (address, now(), GEOCODER_VERSION, buyer_id),
        )
        return None
    if (
        not force
        and buyer["geocode_status"] == "success"
        and buyer["latitude"] is not None
        and buyer["longitude"] is not None
        and normalized_address(buyer["geocode_address"]) == address_key
        and buyer["geocode_version"] == GEOCODER_VERSION
    ):
        return float(buyer["latitude"]), float(buyer["longitude"])
    cached = db().execute(
        """
        select latitude, longitude
        from buyers
        where id != ? and geocode_status = 'success'
          and latitude is not null and longitude is not null
          and lower(trim(geocode_address)) = lower(trim(?))
          and geocode_version = ?
        order by geocode_attempted_at desc, id desc
        limit 1
        """,
        (buyer_id, address, GEOCODER_VERSION),
    ).fetchone()
    if cached:
        coordinates = float(cached["latitude"]), float(cached["longitude"])
    else:
        try:
            coordinates = geocode_address(address)
        except TemporaryGeocodingError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            app.logger.warning("Unable to geocode buyer %s: %s", buyer_id, error)
            coordinates = None
    if coordinates:
        db().execute(
            """
            update buyers
            set latitude = ?, longitude = ?, geocode_address = ?, geocode_status = 'success',
                geocode_attempted_at = ?, geocode_version = ?
            where id = ?
            """,
            (coordinates[0], coordinates[1], address, now(), GEOCODER_VERSION, buyer_id),
        )
        return coordinates
    db().execute(
        """
        update buyers
        set latitude = null, longitude = null, geocode_address = ?, geocode_status = 'failed',
            geocode_attempted_at = ?, geocode_version = ?
        where id = ?
        """,
        (address, now(), GEOCODER_VERSION, buyer_id),
    )
    return None


def notify_role(roles, title, body, link=None, exclude_user_ids=None):
    excluded = {int(user_id) for user_id in (exclude_user_ids or []) if user_id is not None}
    placeholders = ",".join("?" for _ in roles)
    rows = db().execute(f"select id from users where role in ({placeholders})", list(roles)).fetchall()
    for row in rows:
        if row["id"] in excluded:
            continue
        create_message(row["id"], title, body, link)


def review_message_body(user_name, invoice, client, total, is_resubmission=False):
    action = "é‡æ–°æäº¤äº†å‘ç¥¨" if is_resubmission else "æäº¤äº†å‘ç¥¨"
    client_label = client["short_name"] or client["name"]
    return f"{user_name}{action} {invoice['invoice_number']} å®¢æˆ·:{client_label} é‡‘é¢:{money(total, invoice['currency'])} è¯·å®¡æ ¸ã€‚"


def return_message_body(invoice, client, total, reason):
    client_label = client["short_name"] or client["name"]
    return f"å‘ç¥¨ {invoice['invoice_number']} å·²è¢«ç»ç†é€€å›ã€‚å®¢æˆ·:{client_label} é‡‘é¢:{money(total, invoice['currency'])} åŸå› ï¼š{reason}"


def unread_message_count():
    if not g.user:
        return 0
    return db().execute(
        "select count(*) as count from messages where user_id = ? and is_read = 0",
        (g.user["id"],),
    ).fetchone()["count"]


@app.context_processor
def inject_globals():
    return {
        "unread_message_count": unread_message_count(),
        "current_language": current_language(),
        "supported_languages": SUPPORTED_LANGUAGES,
        "can_manage_company_info": can_manage_company_info,
        "app_version": APP_VERSION,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        language = request.form.get("language")
        if language in SUPPORTED_LANGUAGES:
            session["language"] = language
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db().execute("select * from users where email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                flash("è´¦å·å°šæœªå¯ç”¨ï¼Œè¯·ç­‰å¾…ç®¡ç†å‘˜æˆ–ç»ç†æ‰¹å‡†ã€‚", "error")
                return redirect(url_for("login"))
            clear_session_preserving_language()
            user_language = user["default_language"] if "default_language" in user.keys() else DEFAULT_LANGUAGE
            session["language"] = user_language if user_language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
            session["user_id"] = user["id"]
            session.permanent = True
            log_login_action(user)
            db().commit()
            next_target = request.args.get("next")
            if not is_safe_redirect_target(next_target):
                next_target = url_for("dashboard")
            return redirect(next_target)
        flash("é‚®ç®±æˆ–å¯†ç ä¸æ­£ç¡®ã€‚", "error")
    return render_template("login.html")


@app.get("/mobile-app")
def mobile_app_install():
    ipa_available = os.path.isfile(IOS_IPA_PATH) and os.path.getsize(IOS_IPA_PATH) > 0
    manifest_url = url_for("mobile_app_manifest", _external=True, _scheme="https")
    install_url = "itms-services://?" + urlencode({"action": "download-manifest", "url": manifest_url})
    return render_template(
        "mobile_app_install.html",
        ipa_available=ipa_available,
        install_url=install_url,
        ios_app_version=IOS_APP_VERSION,
    )


@app.get("/mobile-app/manifest.plist")
def mobile_app_manifest():
    if not os.path.isfile(IOS_IPA_PATH):
        abort(404)
    ipa_url = html.escape(url_for("mobile_app_ipa", _external=True, _scheme="https"), quote=True)
    bundle_id = html.escape(IOS_BUNDLE_ID, quote=True)
    version = html.escape(IOS_APP_VERSION, quote=True)
    manifest = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>items</key><array><dict>
<key>assets</key><array><dict><key>kind</key><string>software-package</string><key>url</key><string>{ipa_url}</string></dict></array>
<key>metadata</key><dict><key>bundle-identifier</key><string>{bundle_id}</string><key>bundle-version</key><string>{version}</string><key>kind</key><string>software</string><key>title</key><string>Prasinos Power</string></dict>
</dict></array></dict></plist>'''
    return app.response_class(manifest, mimetype="application/xml")


@app.get("/mobile-app/PrasinosPower.ipa")
def mobile_app_ipa():
    if not os.path.isfile(IOS_IPA_PATH):
        abort(404)
    return send_file(
        IOS_IPA_PATH,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name="PrasinosPower.ipa",
        conditional=True,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        email = request.form.get("registration_email", "").strip().lower()
        password = request.form.get("registration_password", "")
        password_confirm = request.form.get("registration_password_confirm", "")
        account_type = request.form.get("account_type", "external_employee")
        country = country_from_form()
        preferred_language, communication_languages = communication_languages_from_form(current_language())
        if account_type not in {"employee", "external_employee"}:
            account_type = "external_employee"
        if not address:
            flash("è¯·å¡«å†™åœ°å€ã€‚", "error")
            return redirect(url_for("register"))
        try:
            phone = normalize_phone(request.form.get("phone"), country["code"])
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("register"))
        if "ï¿½" in name:
            flash("å§“ååŒ…å«æŸåå­—ç¬¦ï¼Œè¯·é‡æ–°è¾“å…¥æ­£ç¡®å§“åã€‚", "error")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("å¯†ç è‡³å°‘éœ€è¦ 8 ä½ã€‚", "error")
            return redirect(url_for("register"))
        if password != password_confirm:
            flash("ä¸¤æ¬¡è¾“å…¥çš„å¯†ç ä¸ä¸€è‡´ã€‚", "error")
            return redirect(url_for("register"))
        try:
            cursor = db().execute(
                """
                insert into users (
                    name, email, password_hash, role, is_active, default_language, region_code, country_code,
                    created_at, address, phone, preferred_communication_language, communication_languages
                )
                values (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name or email,
                    email,
                    generate_password_hash(password),
                    account_type,
                    current_language(),
                    country["region_code"],
                    country["code"],
                    now(),
                    address,
                    phone,
                    preferred_language,
                    communication_languages,
                ),
            )
            user_id = cursor.lastrowid
            account_label = "å‘˜å·¥" if account_type == "employee" else "å¤–éƒ¨å‘˜å·¥"
            notify_role(
                ["admin", "manager"],
                f"æ–°{account_label}è´¦å·å¾…æ‰¹å‡†",
                f"{name or email} å·²æ³¨å†Œ{account_label}è´¦å·ï¼Œè¯·å®¡æ ¸å¹¶å¯ç”¨ã€‚",
                url_for("users"),
            )
            db().commit()
            flash("æ³¨å†ŒæˆåŠŸï¼Œè¯·ç­‰å¾…ç®¡ç†å‘˜æˆ–ç»ç†æ‰¹å‡†å¯ç”¨ã€‚", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("è¿™ä¸ªé‚®ç®±å·²ç»æ³¨å†Œã€‚", "error")
    return render_template("register.html", countries=country_rows())


@app.route("/logout")
def logout():
    clear_session_preserving_language()
    return redirect(url_for("login"))


@app.post("/language")
def switch_language():
    language = request.form.get("language", "")
    if language in SUPPORTED_LANGUAGES:
        session["language"] = language
    next_url = request.form.get("next", "").strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = url_for("dashboard") if g.user else url_for("login")
    else:
        parsed = urlsplit(next_url)
        cleaned_query = urlencode(
            [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "lang"],
            doseq=True,
        )
        next_url = urlunsplit(("", "", parsed.path, cleaned_query, parsed.fragment))
    return redirect(next_url)


@app.route("/messages")
@login_required
def messages():
    rows = db().execute(
        "select * from messages where user_id = ? order by is_read asc, datetime(created_at) desc, id desc",
        (g.user["id"],),
    ).fetchall()
    return render_template("messages.html", messages=rows)


@app.route("/messages/<int:message_id>")
@login_required
def message_detail(message_id):
    message = db().execute(
        "select * from messages where id = ? and user_id = ?",
        (message_id, g.user["id"]),
    ).fetchone()
    if not message:
        abort(404)
    db().execute("update messages set is_read = 1 where id = ?", (message_id,))
    db().commit()
    link = message["link"]
    if link:
        parts = link.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "invoices" and parts[1].isdigit():
            invoice_exists = db().execute("select id from invoices where id = ?", (int(parts[1]),)).fetchone()
            if not invoice_exists:
                flash("è¿™æ¡æ¶ˆæ¯å¯¹åº”çš„å‘ç¥¨å·²ç»è¢«åˆ é™¤ã€‚", "error")
                return redirect(url_for("messages"))
        return redirect(link)
    return redirect(url_for("messages"))


@app.get("/manifest.webmanifest")
def app_manifest():
    """Main-site PWA manifest (v0.1.244).

    With this manifest, "æ·»åŠ åˆ°ä¸»å±å¹• / å®‰è£…åº”ç”¨" creates a real standalone
    app: the phone keeps a single app window that RESUMES where the user left
    off, instead of stacking a new browser tab on every tap.
    """
    response = jsonify(
        name="Prasinos Power",
        short_name="Prasinos",
        id="/",
        start_url="/",
        scope="/",
        display="standalone",
        background_color="#0f766e",
        theme_color="#0f766e",
        icons=[
            dict(src=f"/static/field-icon-{size}.png", sizes=f"{size}x{size}", type="image/png")
            for size in (192, 512)
        ],
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/sw.js")
def app_service_worker():
    """Minimal passthrough service worker (v0.1.244).

    Deliberately NO response caching: the tool is dynamic and pages must stay
    live. The worker exists only so the browser treats the site as installable
    PWA; every request goes to the network exactly like before.
    """
    response = app.send_static_file("app-sw.js")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/workspace")
@login_required
def workspace():
    return render_template("workspace.html")


@app.route("/")
@login_required
def dashboard():
    if not can_view_invoices() or is_external_user():
        return redirect(url_for("service_orders"))
    metrics = get_metrics()
    selected_project_ids = request.args.getlist("project_id")
    project_options = dashboard_project_options()
    project_filter_submitted = "project_filter" in request.args
    available_project_ids = {str(project["id"]) for project in project_options}
    selected_project_ids = [project_id for project_id in selected_project_ids if project_id in available_project_ids]
    if not project_filter_submitted and not selected_project_ids:
        selected_project_ids = [str(project["id"]) for project in project_options]
    chart = monthly_project_chart(selected_project_ids)
    paid_chart = monthly_paid_chart()
    access_clause, access_params = client_filter_clause("invoices")
    recent = db().execute(
        f"""
        select invoices.*, clients.name as client_name
        from invoices join clients on clients.id = invoices.client_id
        where invoices.status = 'completed' and {access_clause}
        order by invoices.created_at desc limit 8
        """,
        access_params,
    ).fetchall()
    return render_template(
        "dashboard.html",
        metrics=metrics,
        chart=chart,
        paid_chart=paid_chart,
        recent=recent,
        labels=STATUS_LABELS,
        project_options=project_options,
        selected_project_ids=selected_project_ids,
    )


@app.route("/contracts")
@login_required
def contracts():
    if not can_view_contracts():
        abort(403)
    q = request.args.get("q", "").strip()
    contract_type = request.args.get("contract_type", "").strip()
    status = request.args.get("status", "").strip()
    clauses = ["1 = 1"]
    params = []
    if is_external_manager():
        clauses.append("contracts.client_id = ?")
        params.append(g.user["client_id"] or 0)
    if q:
        clauses.append(
            "(contracts.contract_number like ? or contracts.title like ? "
            "or clients.name like ? or contracts.project_name like ?)"
        )
        params.extend([f"%{q}%"] * 4)
    if contract_type in CONTRACT_TYPE_LABELS:
        clauses.append("contracts.contract_type = ?")
        params.append(contract_type)
    if status in CONTRACT_STATUS_LABELS:
        clauses.append("contracts.status = ?")
        params.append(status)
    rows = db().execute(
        f"""
        select contracts.*, clients.name as client_name,
               count(distinct service_orders.id) as work_order_count
        from contracts
        join clients on clients.id = contracts.client_id
        left join service_orders on service_orders.contract_id = contracts.id
        where {" and ".join(clauses)}
        group by contracts.id
        order by
          case contracts.status when 'active' then 0 when 'signed' then 1 when 'review' then 2 else 3 end,
          coalesce(contracts.end_date, '9999-12-31'), contracts.id desc
        """,
        params,
    ).fetchall()
    return render_template(
        "contracts.html",
        contracts=rows,
        q=q,
        selected_type=contract_type,
        selected_status=status,
    )


@app.route("/contracts/new", methods=["GET", "POST"])
@login_required
def new_contract():
    if not can_manage_contracts():
        abort(403)
    clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
    if not clients_rows:
        flash("è¯·å…ˆåˆ›å»ºå®¢æˆ·ã€‚", "error")
        return redirect(url_for("clients"))
    if request.method == "POST":
        try:
            values = contract_form_values()
            cursor = db().execute(
                """
                insert into contracts (
                    contract_number, client_id, contract_type, title, status, signed_date,
                    start_date, end_date, currency, amount, payment_terms, rate_card,
                    project_name, notes, created_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["contract_number"], values["client_id"], values["contract_type"],
                    values["title"], values["status"], values["signed_date"], values["start_date"],
                    values["end_date"], values["currency"], values["amount"],
                    values["payment_terms"], values["rate_card"], values["project_name"],
                    values["notes"], g.user["id"], now(), now(),
                ),
            )
            contract_id = cursor.lastrowid
            save_contract_uploads(contract_id)
            log_action(
                "create",
                "contract",
                contract_id,
                values["contract_number"],
                f"{CONTRACT_TYPE_LABELS[values['contract_type']]} Â· {values['title']}",
            )
            db().commit()
            flash("åˆåŒå·²åˆ›å»ºã€‚", "success")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("åˆåŒç¼–å·å·²ç»å­˜åœ¨ï¼Œè¯·æ›´æ¢åˆåŒç¼–å·ã€‚", "error")
    defaults = dict(request.form) if request.method == "POST" else {
        "contract_number": next_contract_number(),
        "contract_type": "framework",
        "status": "draft",
        "currency": "USD",
    }
    return render_template(
        "contract_form.html",
        contract=None,
        clients=clients_rows,
        defaults=defaults,
        attachments=[],
        form_title="æ–°å»ºåˆåŒ",
    )


@app.route("/contracts/<int:contract_id>")
@login_required
def contract_detail(contract_id):
    contract = require_contract_access(contract_id)
    client = db().execute("select * from clients where id = ?", (contract["client_id"],)).fetchone()
    attachments = db().execute(
        """
        select contract_attachments.*, users.name as uploader_name
        from contract_attachments
        left join users on users.id = contract_attachments.uploaded_by
        where contract_attachments.contract_id = ?
        order by contract_attachments.uploaded_at desc, contract_attachments.id desc
        """,
        (contract_id,),
    ).fetchall()
    work_orders = db().execute(
        """
        select service_orders.*, work_order_types.name as work_order_type_name
        from service_orders
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        where service_orders.contract_id = ?
        order by service_orders.created_at desc, service_orders.id desc
        """,
        (contract_id,),
    ).fetchall()
    invoices = db().execute(
        """
        select distinct invoices.*
        from invoices
        join service_orders on service_orders.id = invoices.service_order_id
        where service_orders.contract_id = ? and invoices.status != 'void'
        order by invoices.issue_date desc, invoices.id desc
        """,
        (contract_id,),
    ).fetchall()
    return render_template(
        "contract_detail.html",
        contract=contract,
        client=client,
        attachments=attachments,
        work_orders=work_orders,
        invoices=invoices,
        labels=STATUS_LABELS,
    )


@app.route("/contracts/<int:contract_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contract(contract_id):
    if not can_manage_contracts():
        abort(403)
    contract = require_contract_access(contract_id)
    clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
    if request.method == "POST":
        try:
            values = contract_form_values(contract)
            linked_clients = db().execute(
                """
                select count(*) as count from service_orders
                where contract_id = ? and client_id != ?
                """,
                (contract_id, values["client_id"]),
            ).fetchone()["count"]
            if linked_clients:
                raise ValueError("åˆåŒå·²æœ‰å…¶ä»–å®¢æˆ·çš„å·¥å•ï¼Œä¸èƒ½æ›´æ”¹åˆåŒå®¢æˆ·ã€‚")
            db().execute(
                """
                update contracts
                set contract_number = ?, client_id = ?, contract_type = ?, title = ?, status = ?,
                    signed_date = ?, start_date = ?, end_date = ?, currency = ?, amount = ?,
                    payment_terms = ?, rate_card = ?, project_name = ?, notes = ?, updated_at = ?
                where id = ?
                """,
                (
                    values["contract_number"], values["client_id"], values["contract_type"],
                    values["title"], values["status"], values["signed_date"], values["start_date"],
                    values["end_date"], values["currency"], values["amount"],
                    values["payment_terms"], values["rate_card"], values["project_name"],
                    values["notes"], now(), contract_id,
                ),
            )
            save_contract_uploads(contract_id)
            log_action("update", "contract", contract_id, values["contract_number"], "ä¿®æ”¹åˆåŒèµ„æ–™")
            db().commit()
            flash("åˆåŒå·²æ›´æ–°ã€‚", "success")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("åˆåŒç¼–å·å·²ç»å­˜åœ¨ï¼Œè¯·æ›´æ¢åˆåŒç¼–å·ã€‚", "error")
    attachments = db().execute(
        "select * from contract_attachments where contract_id = ? order by uploaded_at desc, id desc",
        (contract_id,),
    ).fetchall()
    defaults = dict(request.form) if request.method == "POST" else dict(contract)
    return render_template(
        "contract_form.html",
        contract=contract,
        clients=clients_rows,
        defaults=defaults,
        attachments=attachments,
        form_title="ç¼–è¾‘åˆåŒ",
    )


@app.post("/contracts/<int:contract_id>/delete")
@login_required
def delete_contract(contract_id):
    if not can_manage_contracts():
        abort(403)
    contract = require_contract_access(contract_id)
    work_order_count = db().execute(
        "select count(*) as count from service_orders where contract_id = ?",
        (contract_id,),
    ).fetchone()["count"]
    if work_order_count:
        flash("åˆåŒå·²æœ‰å·¥å•å…³è”ï¼Œä¸èƒ½åˆ é™¤ï¼›å¯ä»¥å°†çŠ¶æ€æ”¹ä¸ºå·²ç»ˆæ­¢ã€‚", "error")
        return redirect(url_for("contract_detail", contract_id=contract_id))
    shutil.rmtree(os.path.join(CONTRACT_ATTACHMENTS_DIR, str(contract_id)), ignore_errors=True)
    db().execute("delete from contracts where id = ?", (contract_id,))
    log_action("delete", "contract", contract_id, contract["contract_number"], contract["title"])
    db().commit()
    flash("åˆåŒå·²åˆ é™¤ã€‚", "success")
    return redirect(url_for("contracts"))


@app.route("/contract-attachments/<int:attachment_id>")
@login_required
def preview_contract_attachment(attachment_id):
    attachment = db().execute(
        "select * from contract_attachments where id = ?",
        (attachment_id,),
    ).fetchone()
    if not attachment:
        abort(404)
    require_contract_access(attachment["contract_id"])
    return safe_attachment_response(
        contract_attachment_path(attachment),
        attachment["original_filename"],
    )


@app.route("/contract-attachments/<int:attachment_id>/download")
@login_required
def download_contract_attachment(attachment_id):
    attachment = db().execute(
        "select * from contract_attachments where id = ?",
        (attachment_id,),
    ).fetchone()
    if not attachment:
        abort(404)
    require_contract_access(attachment["contract_id"])
    return send_file(
        contract_attachment_path(attachment),
        as_attachment=True,
        download_name=attachment["original_filename"],
    )


@app.post("/contract-attachments/<int:attachment_id>/delete")
@login_required
def delete_contract_attachment(attachment_id):
    if not can_manage_contracts():
        abort(403)
    attachment = db().execute(
        "select * from contract_attachments where id = ?",
        (attachment_id,),
    ).fetchone()
    if not attachment:
        abort(404)
    require_contract_access(attachment["contract_id"])
    try:
        os.remove(contract_attachment_path(attachment))
    except FileNotFoundError:
        pass
    db().execute("delete from contract_attachments where id = ?", (attachment_id,))
    db().commit()
    flash("åˆåŒé™„ä»¶å·²åˆ é™¤ã€‚", "success")
    return redirect(url_for("edit_contract", contract_id=attachment["contract_id"]))


def payment_term_rows(active_only=False):
    where = "where is_active = 1" if active_only else ""
    return db().execute(
        f"select * from payment_terms {where} order by is_active desc, name collate nocase"
    ).fetchall()


def default_payment_term_id():
    row = db().execute(
        "select id from payment_terms where name = 'Net 30' order by id limit 1"
    ).fetchone()
    return row["id"] if row else None


def posted_payment_term_id(allow_inactive=False):
    raw_value = request.form.get("payment_term_id", "").strip()
    if not raw_value:
        return default_payment_term_id()
    if not raw_value.isdigit():
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„è´¦æœŸæ–¹æ¡ˆã€‚")
    row = db().execute(
        "select id, is_active from payment_terms where id = ?",
        (int(raw_value),),
    ).fetchone()
    if not row or (not row["is_active"] and not allow_inactive):
        raise ValueError("é€‰æ‹©çš„è´¦æœŸæ–¹æ¡ˆä¸å­˜åœ¨æˆ–å·²åœç”¨ã€‚")
    return row["id"]


def shifted_month(year, month, offset):
    month_index = year * 12 + (month - 1) + int(offset)
    return month_index // 12, month_index % 12 + 1


def calculate_payment_due_date(issue_date_value, payment_term):
    try:
        issue = date.fromisoformat(str(issue_date_value or ""))
    except ValueError as error:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„å¼€ç¥¨æ—¥æœŸã€‚") from error
    if not payment_term or payment_term["rule_type"] == "fixed_days":
        days = int(payment_term["fixed_days"] if payment_term else 30)
        return issue + timedelta(days=max(days, 0))
    cutoff_day = int(payment_term["cutoff_day"] or 1)
    due_day = int(payment_term["due_day"] or 1)
    month_offset = (
        int(payment_term["before_due_months"] or 0)
        if issue.day <= cutoff_day
        else int(payment_term["after_due_months"] or 0)
    )
    target_year, target_month = shifted_month(issue.year, issue.month, month_offset)
    target_day = min(due_day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def payment_term_description(payment_term):
    if payment_term["rule_type"] == "fixed_days":
        return f"å¼€ç¥¨æ—¥å {payment_term['fixed_days']} å¤©åˆ°æœŸ"
    return (
        f"æ¯æœˆ {payment_term['cutoff_day']} æ—¥æˆªæ­¢ï¼ˆå«å½“å¤©ï¼‰ï¼›æˆªæ­¢æ—¥å‰åˆ°æœŸæœˆ +"
        f"{payment_term['before_due_months']}ï¼Œæˆªæ­¢æ—¥ååˆ°æœŸæœˆ +{payment_term['after_due_months']}ï¼›"
        f"åˆ°æœŸæ—¥ {payment_term['due_day']} æ—¥"
    )


def payment_term_form_values():
    name = request.form.get("name", "").strip()
    rule_type = request.form.get("rule_type", "fixed_days").strip()
    if not name:
        raise ValueError("è¯·è¾“å…¥è´¦æœŸåç§°ã€‚")
    if rule_type not in {"fixed_days", "monthly_cutoff"}:
        raise ValueError("è¯·é€‰æ‹©æœ‰æ•ˆçš„è´¦æœŸç±»å‹ã€‚")
    if rule_type == "fixed_days":
        required_fields = (("fixed_days", "å›ºå®šå¤©æ•°"),)
    else:
        required_fields = (
            ("cutoff_day", "æˆªæ­¢æ—¥"), ("due_day", "åˆ°æœŸæ—¥"),
            ("before_due_months", "æˆªæ­¢æ—¥å‰åˆ°æœŸæœˆåç§»"),
            ("after_due_months", "æˆªæ­¢æ—¥ååˆ°æœŸæœˆåç§»"),
        )
    for field_name, label in required_fields:
        if request.form.get(field_name, "").strip() == "":
            raise ValueError(f"è¯·å¡«å†™{label}ã€‚")
    try:
        fixed_days = int(request.form.get("fixed_days", "30") or 30)
        cutoff_day = int(request.form.get("cutoff_day", "20") or 20)
        due_day = int(request.form.get("due_day", "19") or 19)
        before_due_months = int(request.form.get("before_due_months", "1") or 1)
        after_due_months = int(request.form.get("after_due_months", "2") or 2)
    except ValueError as error:
        raise ValueError("è´¦æœŸå‚æ•°å¿…é¡»æ˜¯æ•´æ•°ã€‚") from error
    if not 0 <= fixed_days <= 3650:
        raise ValueError("å›ºå®šå¤©æ•°å¿…é¡»åœ¨0è‡³3650ä¹‹é—´ã€‚")
    if not 1 <= cutoff_day <= 31 or not 1 <= due_day <= 31:
        raise ValueError("æˆªæ­¢æ—¥å’Œåˆ°æœŸæ—¥å¿…é¡»åœ¨1è‡³31ä¹‹é—´ã€‚")
    if not 0 <= before_due_months <= 24 or not 0 <= after_due_months <= 24:
        raise ValueError("åˆ°æœŸæœˆä»½åç§»å¿…é¡»åœ¨0è‡³24ä¹‹é—´ã€‚")
    if rule_type == "monthly_cutoff" and after_due_months < before_due_months:
        raise ValueError("æˆªæ­¢æ—¥åçš„åˆ°æœŸæœˆä»½ä¸èƒ½æ—©äºæˆªæ­¢æ—¥å‰ã€‚")
    return {
        "name": name,
        "rule_type": rule_type,
        "fixed_days": fixed_days,
        "cutoff_day": cutoff_day if rule_type == "monthly_cutoff" else None,
        "due_day": due_day if rule_type == "monthly_cutoff" else None,
        "before_due_months": before_due_months,
        "after_due_months": after_due_months,
        "notes": request.form.get("notes", "").strip(),
    }


def eligible_invoice_due_date_changes():
    rows = db().execute(
        """
        select invoices.id, invoices.invoice_number, invoices.issue_date, invoices.due_date,
               clients.name as client_name, payment_terms.*,
               service_orders.order_number
        from invoices
        join clients on clients.id = invoices.client_id
        join payment_terms on payment_terms.id = clients.payment_term_id
        join service_orders on service_orders.id = invoices.service_order_id
        where service_orders.status != 'closed'
          and invoices.paid_at is null
          and trim(coalesce(invoices.sent_at, '')) = ''
          and not exists (
              select 1 from email_delivery_logs
              where entity_type = 'invoice' and entity_id = invoices.id
          )
        order by invoices.issue_date, invoices.id
        """
    ).fetchall()
    changes = []
    for row in rows:
        new_due_date = calculate_payment_due_date(row["issue_date"], row).isoformat()
        if new_due_date != row["due_date"]:
            changes.append({"invoice": row, "new_due_date": new_due_date})
    return changes


@app.route("/settings/payment-terms", methods=["GET", "POST"])
@login_required
def payment_terms():
    if request.method == "POST":
        try:
            values = payment_term_form_values()
            cursor = db().execute(
                """
                insert into payment_terms (
                    name, rule_type, fixed_days, cutoff_day, due_day,
                    before_due_months, after_due_months, notes, is_active, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    values["name"], values["rule_type"], values["fixed_days"],
                    values["cutoff_day"], values["due_day"], values["before_due_months"],
                    values["after_due_months"], values["notes"], now(), now(),
                ),
            )
            log_action("create", "payment_term", cursor.lastrowid, values["name"], "æ–°å¢è´¦æœŸæ–¹æ¡ˆ")
            db().commit()
            flash("è´¦æœŸæ–¹æ¡ˆå·²åˆ›å»ºã€‚", "success")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("è´¦æœŸåç§°å·²ç»å­˜åœ¨ã€‚", "error")
        return redirect(url_for("payment_terms"))
    terms = payment_term_rows()
    assigned_counts = {
        row["payment_term_id"]: row["count"]
        for row in db().execute(
            "select payment_term_id, count(*) as count from clients group by payment_term_id"
        ).fetchall()
    }
    return render_template(
        "payment_terms.html",
        payment_terms=terms,
        assigned_counts=assigned_counts,
        due_date_changes=eligible_invoice_due_date_changes(),
        payment_term_description=payment_term_description,
    )


@app.post("/settings/payment-terms/<int:payment_term_id>/edit")
@login_required
def edit_payment_term(payment_term_id):
    term = db().execute("select * from payment_terms where id = ?", (payment_term_id,)).fetchone()
    if not term:
        abort(404)
    try:
        values = payment_term_form_values()
        db().execute(
            """
            update payment_terms
            set name = ?, rule_type = ?, fixed_days = ?, cutoff_day = ?, due_day = ?,
                before_due_months = ?, after_due_months = ?, notes = ?, updated_at = ?
            where id = ?
            """,
            (
                values["name"], values["rule_type"], values["fixed_days"],
                values["cutoff_day"], values["due_day"], values["before_due_months"],
                values["after_due_months"], values["notes"], now(), payment_term_id,
            ),
        )
        log_action("update", "payment_term", payment_term_id, values["name"], "ä¿®æ”¹è´¦æœŸæ–¹æ¡ˆ")
        db().commit()
        flash("è´¦æœŸæ–¹æ¡ˆå·²æ›´æ–°ã€‚", "success")
    except ValueError as error:
        db().rollback()
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("è´¦æœŸåç§°å·²ç»å­˜åœ¨ã€‚", "error")
    return redirect(url_for("payment_terms"))


@app.post("/settings/payment-terms/<int:payment_term_id>/toggle")
@login_required
def toggle_payment_term(payment_term_id):
    term = db().execute("select * from payment_terms where id = ?", (payment_term_id,)).fetchone()
    if not term:
        abort(404)
    if term["name"] == "Net 30" and term["is_active"]:
        flash("é»˜è®¤ Net 30 æ–¹æ¡ˆä¸èƒ½åœç”¨ã€‚", "error")
        return redirect(url_for("payment_terms"))
    next_status = 0 if term["is_active"] else 1
    db().execute(
        "update payment_terms set is_active = ?, updated_at = ? where id = ?",
        (next_status, now(), payment_term_id),
    )
    log_action(
        "update", "payment_term", payment_term_id, term["name"],
        "å¯ç”¨è´¦æœŸæ–¹æ¡ˆ" if next_status else "åœç”¨è´¦æœŸæ–¹æ¡ˆ",
    )
    db().commit()
    flash("è´¦æœŸçŠ¶æ€å·²æ›´æ–°ã€‚", "success")
    return redirect(url_for("payment_terms"))


@app.post("/settings/payment-terms/recalculate-invoices")
@login_required
def recalculate_payment_term_invoices():
    changes = eligible_invoice_due_date_changes()
    for change in changes:
        invoice = change["invoice"]
        db().execute(
            "update invoices set due_date = ? where id = ?",
            (change["new_due_date"], invoice["id"]),
        )
        log_action(
            "recalculate_due_date", "invoice", invoice["id"], invoice["invoice_number"],
            f"è´¦æœŸé‡ç®—ï¼š{invoice['due_date']} â†’ {change['new_due_date']}",
        )
    db().commit()
    flash(f"å·²æ›´æ–° {len(changes)} å¼ ç¬¦åˆæ¡ä»¶çš„æœªå‘é€ã€æœªæ ¸é”€å‘ç¥¨ã€‚", "success")
    return redirect(url_for("payment_terms"))


@app.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    if normalized_role() in {"employee", "external_employee"}:
        abort(403)
    if is_external_manager() and not g.user["client_id"]:
        flash("å¤–éƒ¨ç”¨æˆ·å°šæœªç»‘å®šå®¢æˆ·ã€‚", "error")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if is_external_user():
            abort(403)
        try:
            payment_term_id = posted_payment_term_id()
            db().execute(
                """
                insert into clients (
                    client_number, name, short_name, contact_name, email, address,
                    country, payment_term_id, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_client_number(),
                    request.form.get("name", "").strip(),
                    request.form.get("short_name", "").strip() or request.form.get("name", "").strip(),
                    request.form.get("contact_name", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("address", "").strip(),
                    request.form.get("country", "China").strip() or "China",
                    payment_term_id,
                    now(),
                ),
            )
            db().commit()
            flash("å®¢æˆ·å·²åˆ›å»ºã€‚", "success")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("å®¢æˆ·ç¼–å·é‡å¤ï¼Œè¯·é‡è¯•ã€‚", "error")
        return redirect(url_for("clients"))
    q = request.args.get("q", "").strip()
    if is_external_manager():
        rows = db().execute(
            """
            select clients.*, payment_terms.name as payment_term_name
            from clients left join payment_terms on payment_terms.id = clients.payment_term_id
            where clients.id = ?
            """,
            (g.user["client_id"],),
        ).fetchall()
    else:
        params = []
        where = ""
        if q:
            where = """
            where clients.client_number like ? or clients.name like ? or clients.short_name like ?
               or clients.contact_name like ? or clients.email like ?
            """
            params = [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
        rows = db().execute(
            f"""
            select clients.*, payment_terms.name as payment_term_name
            from clients left join payment_terms on payment_terms.id = clients.payment_term_id
            {where}
            order by clients.client_number asc
            """,
            params,
        ).fetchall()
    return render_template(
        "clients.html", clients=rows, q=q, payment_terms=payment_term_rows()
    )


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    if normalized_role() == "employee":
        abort(403)
    if is_external_user():
        abort(403)
    client = db().execute("select * from clients where id = ?", (client_id,)).fetchone()
    if not client:
        abort(404)
    if request.method == "POST":
        client_number = request.form.get("client_number", "").strip()
        if len(client_number) != 5 or not client_number.isdigit():
            flash("å®¢æˆ·ç¼–å·å¿…é¡»æ˜¯ 5 ä½æ•°å­—ã€‚", "error")
            return redirect(url_for("edit_client", client_id=client_id))
        try:
            payment_term_id = posted_payment_term_id(allow_inactive=True)
            db().execute(
                """
                update clients
                set client_number = ?, name = ?, short_name = ?, contact_name = ?, email = ?,
                    address = ?, country = ?, payment_term_id = ?
                where id = ?
                """,
                (
                    client_number,
                    request.form.get("name", "").strip(),
                    request.form.get("short_name", "").strip() or request.form.get("name", "").strip(),
                    request.form.get("contact_name", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("address", "").strip(),
                    request.form.get("country", "China").strip() or "China",
                    payment_term_id,
                    client_id,
                ),
            )
            log_action(
                "update", "client", client_id, client_number,
                f"æ›´æ–°å®¢æˆ·èµ„æ–™ï¼›è´¦æœŸæ–¹æ¡ˆIDï¼š{payment_term_id}",
            )
            db().commit()
            flash("å®¢æˆ·èµ„æ–™å·²æ›´æ–°ã€‚", "success")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
            return redirect(url_for("edit_client", client_id=client_id))
        except sqlite3.IntegrityError:
            flash("å®¢æˆ·ç¼–å·é‡å¤ï¼Œè¯·æ¢ä¸€ä¸ªç¼–å·ã€‚", "error")
            return redirect(url_for("edit_client", client_id=client_id))
        return redirect(url_for("clients"))
    return render_template("client_form.html", client=client)


@app.post("/clients/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    if normalized_role() == "employee":
        abort(403)
    if is_external_user():
        abort(403)
    invoice_count = db().execute("select count(*) as count from invoices where client_id = ?", (client_id,)).fetchone()["count"]
    contract_count = db().execute("select count(*) as count from contracts where client_id = ?", (client_id,)).fetchone()["count"]
    if invoice_count or contract_count:
        flash("è¿™ä¸ªå®¢æˆ·å·²æœ‰åˆåŒæˆ–å‘ç¥¨è®°å½•ï¼Œä¸èƒ½åˆ é™¤ã€‚å¯ä»¥ç¼–è¾‘å®¢æˆ·èµ„æ–™ä»¥ä¿ç•™å†å²è®°å½•ã€‚", "error")
        return redirect(url_for("clients"))
    db().execute("delete from clients where id = ?", (client_id,))
    db().commit()
    flash("å®¢æˆ·å·²åˆ é™¤ã€‚", "success")
    return redirect(url_for("clients"))


@app.route("/owners", methods=["GET", "POST"])
@login_required
def owners():
    required_action = "create" if request.method == "POST" else "view"
    if not has_action_permission("owners", required_action):
        abort(403)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("è¯·å¡«å†™ä¸šä¸»åç§°ã€‚", "error")
            return redirect(url_for("owners"))
        if db().execute("select 1 from owners where lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
            flash("ä¸šä¸»åç§°å·²å­˜åœ¨ã€‚", "error")
            return redirect(url_for("owners"))
        try:
            db().execute(
                "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
                (next_owner_number(), name, now()),
            )
            db().commit()
            flash("ä¸šä¸»å·²åˆ›å»ºã€‚", "success")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("ä¸šä¸»ç¼–å·é‡å¤ï¼Œè¯·é‡è¯•ã€‚", "error")
        return redirect(url_for("owners"))
    q = request.args.get("q", "").strip()
    params = []
    where = ""
    if q:
        where = "where owner_number like ? or name like ?"
        params = [f"%{q}%", f"%{q}%"]
    rows = db().execute(
        f"select * from owners {where} order by case when name = 'æœªçŸ¥' then 0 else 1 end, owner_number asc",
        params,
    ).fetchall()
    return render_template("owners.html", owners=rows, q=q)


@app.route("/owners/<int:owner_id>/edit", methods=["POST"])
@login_required
def edit_owner(owner_id):
    if not has_action_permission("owners", "edit"):
        abort(403)
    owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
    if not owner:
        abort(404)
    owner_number = request.form.get("owner_number", "").strip().upper()
    name = request.form.get("name", "").strip()
    if not owner_number or not name:
        flash("è¯·å¡«å†™ä¸šä¸»ç¼–å·å’Œåç§°ã€‚", "error")
        return redirect(url_for("owners"))
    if db().execute(
        "select 1 from owners where id != ? and lower(trim(name)) = lower(trim(?))",
        (owner_id, name),
    ).fetchone():
        flash("ä¸šä¸»åç§°å·²å­˜åœ¨ã€‚", "error")
        return redirect(url_for("owners"))
    try:
        db().execute(
            "update owners set owner_number = ?, name = ? where id = ?",
            (owner_number, name, owner_id),
        )
        db().execute(
            "update buyers set owner = ? where owner_id = ?",
            (name, owner_id),
        )
        db().commit()
        flash("ä¸šä¸»èµ„æ–™å·²æ›´æ–°ã€‚", "success")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("ä¸šä¸»ç¼–å·é‡å¤ï¼Œè¯·æ¢ä¸€ä¸ªç¼–å·ã€‚", "error")
    return redirect(url_for("owners"))


@app.post("/owners/<int:owner_id>/delete")
@login_required
def delete_owner(owner_id):
    if not has_action_permission("owners", "delete"):
        abort(403)
    owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
    if not owner:
        abort(404)
    if owner["name"] == "æœªçŸ¥":
        flash("é»˜è®¤ä¸šä¸»ä¸èƒ½åˆ é™¤ã€‚", "error")
        return redirect(url_for("owners"))
    used = db().execute(
        "select count(*) as count from buyers where owner_id = ?",
        (owner_id,),
    ).fetchone()["count"]
    if used:
        flash("è¿™ä¸ªä¸šä¸»å·²æœ‰ç«™ç‚¹ï¼Œä¸èƒ½åˆ é™¤ã€‚å¯ä»¥ç¼–è¾‘ä¸šä¸»èµ„æ–™ã€‚", "error")
        return redirect(url_for("owners"))
    db().execute("delete from owners where id = ?", (owner_id,))
    db().commit()
    flash("ä¸šä¸»å·²åˆ é™¤ã€‚", "success")
    return redirect(url_for("owners"))


@app.route("/manufacturers", methods=["GET", "POST"])
@login_required
def manufacturers():
    required_action = "create" if request.method == "POST" else "view"
    if not has_action_permission("manufacturers", required_action):
        abort(403)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("è¯·å¡«å†™å‚å®¶åç§°ã€‚", "error")
            return redirect(url_for("manufacturers"))
        if db().execute("select 1 from manufacturers where lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
            flash("å‚å®¶åç§°å·²å­˜åœ¨ã€‚", "error")
            return redirect(url_for("manufacturers"))
        try:
            db().execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values (?, ?, ?)",
                (next_manufacturer_number(), name, now()),
            )
            db().commit()
            flash("å‚å®¶å·²åˆ›å»ºã€‚", "success")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("å‚å®¶ç¼–å·é‡å¤ï¼Œè¯·é‡è¯•ã€‚", "error")
        return redirect(url_for("manufacturers"))
    q = request.args.get("q", "").strip()
    params = []
    where = ""
    if q:
        where = "where manufacturer_number like ? or name like ?"
        params = [f"%{q}%", f"%{q}%"]
    rows = db().execute(
        f"select * from manufacturers {where} order by manufacturer_number asc",
        params,
    ).fetchall()
    return render_template("manufacturers.html", manufacturers=rows, q=q)


@app.route("/manufacturers/<int:manufacturer_id>/edit", methods=["POST"])
@login_required
def edit_manufacturer(manufacturer_id):
    if not has_action_permission("manufacturers", "edit"):
        abort(403)
    manufacturer = db().execute("select * from manufacturers where id = ?", (manufacturer_id,)).fetchone()
    if not manufacturer:
        abort(404)
    manufacturer_number = request.form.get("manufacturer_number", "").strip().upper()
    name = request.form.get("name", "").strip()
    if not manufacturer_number or not name:
        flash("è¯·å¡«å†™å‚å®¶ç¼–å·å’Œåç§°ã€‚", "error")
        return redirect(url_for("manufacturers"))
    if db().execute(
        "select 1 from manufacturers where id != ? and lower(trim(name)) = lower(trim(?))",
        (manufacturer_id, name),
    ).fetchone():
        flash("å‚å®¶åç§°å·²å­˜åœ¨ã€‚", "error")
        return redirect(url_for("manufacturers"))
    try:
        db().execute(
            "update manufacturers set manufacturer_number = ?, name = ? where id = ?",
            (manufacturer_number, name, manufacturer_id),
        )
        db().execute(
            "update buyers set equipment_manufacturer = ? where manufacturer_id = ?",
            (name, manufacturer_id),
        )
        db().commit()
        flash("å‚å®¶èµ„æ–™å·²æ›´æ–°ã€‚", "success")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("å‚å®¶ç¼–å·é‡å¤ï¼Œè¯·æ¢ä¸€ä¸ªç¼–å·ã€‚", "error")
    return redirect(url_for("manufacturers"))


@app.post("/manufacturers/<int:manufacturer_id>/delete")
@login_required
def delete_manufacturer(manufacturer_id):
    if not has_action_permission("manufacturers", "delete"):
        abort(403)
    manufacturer = db().execute("select * from manufacturers where id = ?", (manufacturer_id,)).fetchone()
    if not manufacturer:
        abort(404)
    used = db().execute(
        "select count(*) as count from buyers where manufacturer_id = ?",
        (manufacturer_id,),
    ).fetchone()["count"]
    if used:
        flash("è¿™ä¸ªå‚å®¶å·²æœ‰ç«™ç‚¹ï¼Œä¸èƒ½åˆ é™¤ã€‚å¯ä»¥ç¼–è¾‘å‚å®¶èµ„æ–™ã€‚", "error")
        return redirect(url_for("manufacturers"))
    db().execute("delete from manufacturers where id = ?", (manufacturer_id,))
    db().commit()
    flash("å‚å®¶å·²åˆ é™¤ã€‚", "success")
    return redirect(url_for("manufacturers"))


@app.route("/buyers", methods=["GET", "POST"])
@login_required
def buyers():
    required_action = "create" if request.method == "POST" else "view"
    if not has_action_permission("buyers", required_action):
        abort(403)
    owners_rows = owner_options()
    manufacturers_rows = manufacturer_options()
    countries_rows = country_rows()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        country = country_from_form()
        owner = None
        owner_id = int(request.form["owner_id"]) if request.form.get("owner_id", "").isdigit() else None
        if owner_id:
            owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
            if not owner:
                flash("è¯·é€‰æ‹©æœ‰æ•ˆçš„ä¸šä¸»ã€‚", "error")
                return redirect(url_for("buyers"))
        else:
            owner = unknown_owner()
            owner_id = owner["id"]
        manufacturer = manufacturer_from_form()
        if request.form.get("manufacturer_id") and not manufacturer:
            flash("è¯·é€‰æ‹©æœ‰æ•ˆçš„å‚å®¶ã€‚", "error")
            return redirect(url_for("buyers"))
        detailed_address = request.form.get("detailed_address", "").strip()
        if not name or not detailed_address:
            flash("è¯·å¡«å†™ç«™ç‚¹åç§°å’Œè¯¦ç»†åœ°å€ã€‚", "error")
            return redirect(url_for("buyers"))
        try:
            manual_coordinates = parse_coordinate_pair(request.form.get("coordinates"))
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("buyers"))
        client_id = g.user["client_id"] if is_external_manager() else (
            int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else None
        )
        existing_site = db().execute(
            """
            select buyer_number from buyers
            where coalesce(client_id, 0) = coalesce(?, 0)
              and lower(trim(name)) = lower(trim(?))
              and lower(trim(detailed_address)) = lower(trim(?))
            """,
            (client_id, name, detailed_address),
        ).fetchone()
        if existing_site:
            flash(f"ç«™ç‚¹å·²å­˜åœ¨ï¼Œç¼–å·ï¼š{existing_site['buyer_number']}ã€‚", "error")
            return redirect(url_for("buyers"))
        try:
            db().execute(
                """
                insert into buyers (
                    buyer_number, client_id, country, country_code, name, owner_id, owner, manufacturer_id, contact_name, contact_details,
                    email, site_size, detailed_address, equipment_manufacturer, latitude, longitude, geocode_address,
                    geocode_status, geocode_attempted_at, geocode_version, manual_coordinates, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_buyer_number(),
                    client_id,
                    country["code"],
                    country["code"],
                    name,
                    owner_id,
                    owner["name"] if owner else "",
                    manufacturer["id"] if manufacturer else None,
                    request.form.get("contact_name", "").strip(),
                    request.form.get("contact_details", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("site_size", "").strip(),
                    detailed_address,
                    manufacturer["name"] if manufacturer else "",
                    manual_coordinates[0] if manual_coordinates else None,
                    manual_coordinates[1] if manual_coordinates else None,
                    detailed_address if manual_coordinates else None,
                    "success" if manual_coordinates else "pending",
                    now() if manual_coordinates else None,
                    GEOCODER_VERSION if manual_coordinates else None,
                    1 if manual_coordinates else 0,
                    now(),
                ),
            )
            db().commit()
            flash("ç«™ç‚¹å·²åˆ›å»ºã€‚", "success")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("ç«™ç‚¹ç¼–å·é‡å¤ï¼Œè¯·é‡è¯•ã€‚", "error")
        return redirect(url_for("buyers"))
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "buyer_number")
    direction = request.args.get("direction", "asc").lower()
    buyer_sort_columns = {
        "buyer_number": "buyers.buyer_number",
        "country_name": "coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code)",
        "name": "buyers.name",
        "owner_name": "coalesce(owners.name, buyers.owner)",
        "contact_name": "buyers.contact_name",
        "contact_details": "buyers.contact_details",
        "email": "buyers.email",
        "site_size": "buyers.site_size",
        "equipment_manufacturer": "buyers.equipment_manufacturer",
        "detailed_address": "buyers.detailed_address",
    }
    if sort not in buyer_sort_columns:
        sort = "buyer_number"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    order_direction = "desc" if direction == "desc" else "asc"
    order_by = f"lower(coalesce({buyer_sort_columns[sort]}, '')) {order_direction}, buyers.name asc, buyers.buyer_number asc"
    params = []
    clauses = []
    if is_external_manager():
        clauses.append("buyers.client_id = ?")
        params.append(g.user["client_id"])
    if q:
        clauses.append(
            """(
            buyers.buyer_number like ? or buyers.name like ? or coalesce(owners.name, buyers.owner) like ? or buyers.contact_name like ?
            or buyers.contact_details like ? or buyers.email like ? or buyers.site_size like ? or buyers.detailed_address like ? or buyers.equipment_manufacturer like ?
            )"""
        )
        params = [f"%{q}%"] * 9
        if is_external_manager():
            params.insert(0, g.user["client_id"])
    where = f"where {' and '.join(clauses)}" if clauses else ""
    rows = db().execute(
        f"""
        select buyers.*, coalesce(owners.name, buyers.owner) as owner_name,
               owners.owner_number as owner_number,
               coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code) as country_name
        from buyers
        left join owners on owners.id = buyers.owner_id
        left join country_translations country_local
          on country_local.country_code = buyers.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = buyers.country_code and country_zh.language_code = 'zh-CN'
        left join country_translations country_en
          on country_en.country_code = buyers.country_code and country_en.language_code = 'en'
        {where}
        order by {order_by}
        """,
        [current_language(), *params],
    ).fetchall()
    clients_rows = db().execute("select id, client_number, name from clients order by client_number").fetchall()
    return render_template(
        "buyers.html",
        buyers=rows,
        clients=clients_rows,
        owners=owners_rows,
        manufacturers=manufacturers_rows,
        countries=countries_rows,
        q=q,
        sort=sort,
        direction=direction,
        import_result=session.pop("buyer_import_result", None),
    )


@app.post("/buyers/import")
@login_required
def import_buyers():
    if not has_action_permission("buyers", "create"):
        abort(403)
    uploaded_file = request.files.get("import_file")
    if not uploaded_file or not uploaded_file.filename:
        flash("è¯·é€‰æ‹©è¦å¯¼å…¥çš„ç«™ç‚¹æ–‡ä»¶ã€‚", "error")
        return redirect(url_for("buyers"))
    client_id = g.user["client_id"] if is_external_manager() else (
        int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else None
    )
    try:
        result = import_buyers_from_file(uploaded_file, client_id)
        detail_lines = result["details"][:20]
        if len(result["details"]) > 20:
            detail_lines.append(f"è¿˜æœ‰ {len(result['details']) - 20} æ¡æ˜ç»†æœªåœ¨å¼¹çª—ä¸­æ˜¾ç¤ºï¼Œè¯·æŸ¥çœ‹æ“ä½œæ—¥å¿—ã€‚")
        session["buyer_import_result"] = {
            **result,
            "details": detail_lines,
        }
        log_summary = (
            f"å¯¼å…¥ç«™ç‚¹ï¼šæ–‡ä»¶ {uploaded_file.filename}ï¼›æ€»è¡Œæ•° {result['total']}ï¼›"
            f"æ–°å¢ {result['created']}ï¼›è·³è¿‡ {result['skipped']}ï¼›é”™è¯¯ {result['errors']}ï¼›"
            f"è‡ªåŠ¨åˆ›å»ºä¸šä¸» {result['owners_created']}ã€‚"
        )
        if result["details"]:
            log_summary = f"{log_summary} æ˜ç»†ï¼š{' | '.join(result['details'])}"
        log_action("import", "buyer", None, uploaded_file.filename, log_summary)
        db().commit()
        flash("ç«™ç‚¹å¯¼å…¥å·²å®Œæˆã€‚", "success")
    except ValueError as error:
        db().rollback()
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("å¯¼å…¥å¤±è´¥ï¼šç«™ç‚¹ç¼–å·æˆ–ä¸šä¸»ç¼–å·å†²çªï¼Œè¯·æ£€æŸ¥å¯¼å…¥æ–‡ä»¶åé‡è¯•ã€‚", "error")
    return redirect(url_for("buyers"))


@app.route("/buyers/<int:buyer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_buyer(buyer_id):
    if not has_action_permission("buyers", "edit"):
        abort(403)
    buyer = db().execute("select * from buyers where id = ?", (buyer_id,)).fetchone()
    if not buyer:
        abort(404)
    if not can_access_buyer(buyer):
        abort(403)
    if request.method == "POST":
        buyer_number = request.form.get("buyer_number", "").strip().upper()
        name = request.form.get("name", "").strip()
        country = country_from_form(buyer["country_code"] or "US")
        owner = None
        owner_id = int(request.form["owner_id"]) if request.form.get("owner_id", "").isdigit() else None
        if owner_id:
            owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
            if not owner:
                flash("è¯·é€‰æ‹©æœ‰æ•ˆçš„ä¸šä¸»ã€‚", "error")
                return redirect(url_for("edit_buyer", buyer_id=buyer_id))
        else:
            owner = unknown_owner()
            owner_id = owner["id"]
        manufacturer = manufacturer_from_form()
        if request.form.get("manufacturer_id") and not manufacturer:
            flash("è¯·é€‰æ‹©æœ‰æ•ˆçš„å‚å®¶ã€‚", "error")
            return redirect(url_for("edit_buyer", buyer_id=buyer_id))
        detailed_address = request.form.get("detailed_address", "").strip()
        if not buyer_number or not name or not detailed_address:
            flash("è¯·å¡«å†™ç¼–å·ã€ç«™ç‚¹åç§°å’Œè¯¦ç»†åœ°å€ã€‚", "error")
            return redirect(url_for("edit_buyer", buyer_id=buyer_id))
        try:
            pasted_coordinates = parse_coordinate_pair(request.form.get("coordinates"))
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("edit_buyer", buyer_id=buyer_id))
        clear_manual_coordinates = request.form.get("clear_manual_coordinates") == "1"
        address_changed = normalized_address(buyer["detailed_address"]) != normalized_address(detailed_address)
        try:
            db().execute(
                """
                update buyers
                set buyer_number = ?, client_id = ?, country = ?, country_code = ?, name = ?, owner_id = ?, owner = ?, contact_name = ?,
                    manufacturer_id = ?, contact_details = ?, email = ?, site_size = ?, detailed_address = ?, equipment_manufacturer = ?
                where id = ?
                """,
                (
                    buyer_number,
                    g.user["client_id"] if is_external_manager() else (
                        int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else buyer["client_id"]
                    ),
                    country["code"],
                    country["code"],
                    name,
                    owner_id,
                    owner["name"] if owner else "",
                    request.form.get("contact_name", "").strip(),
                    manufacturer["id"] if manufacturer else None,
                    request.form.get("contact_details", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("site_size", "").strip(),
                    detailed_address,
                    manufacturer["name"] if manufacturer else "",
                    buyer_id,
                ),
            )
            db().execute(
                """
                update service_orders
                set client_name = ?, buyer_contact_name = ?, buyer_contact_details = ?
                where buyer_id = ?
                """,
                (
                    name,
                    request.form.get("contact_name", "").strip(),
                    request.form.get("contact_details", "").strip(),
                    buyer_id,
                ),
            )
            if clear_manual_coordinates:
                db().execute(
                    """
                    update buyers
                    set latitude = null, longitude = null, geocode_address = null,
                        geocode_status = 'pending', geocode_attempted_at = null, geocode_version = null,
                        manual_coordinates = 0
                    where id = ?
                    """,
                    (buyer_id,),
                )
            elif pasted_coordinates:
                db().execute(
                    """
                    update buyers
                    set latitude = ?, longitude = ?, geocode_address = ?, geocode_status = 'success',
                        geocode_attempted_at = ?, geocode_version = ?, manual_coordinates = 1
                    where id = ?
                    """,
                    (pasted_coordinates[0], pasted_coordinates[1], detailed_address, now(), GEOCODER_VERSION, buyer_id),
                )
            elif buyer["manual_coordinates"]:
                db().execute(
                    """
                    update buyers
                    set geocode_address = ?, geocode_status = 'success', geocode_version = ?
                    where id = ?
                    """,
                    (detailed_address, GEOCODER_VERSION, buyer_id),
                )
            elif address_changed:
                db().execute(
                    """
                    update buyers
                    set latitude = null, longitude = null, geocode_address = null,
                        geocode_status = 'pending', geocode_attempted_at = null, geocode_version = null,
                        manual_coordinates = 0
                    where id = ?
                    """,
                    (buyer_id,),
                )
            db().commit()
            flash("ç«™ç‚¹èµ„æ–™å·²æ›´æ–°ã€‚", "success")
            return redirect(url_for("buyers"))
        except sqlite3.IntegrityError:
            db().rollback()
            flash("ç«™ç‚¹ç¼–å·é‡å¤ï¼Œè¯·æ¢ä¸€ä¸ªç¼–å·ã€‚", "error")
    clients_rows = db().execute("select id, client_number, name from clients order by client_number").fetchall()
    owners_rows = owner_options()
    return render_template(
        "buyer_form.html",
        buyer=buyer,
        clients=clients_rows,
        owners=owners_rows,
        manufacturers=manufacturer_options(),
        countries=country_rows(),
    )


@app.post("/buyers/<int:buyer_id>/delete")
@login_required
def delete_buyer(buyer_id):
    if not has_action_permission("buyers", "delete"):
        abort(403)
    buyer = db().execute("select * from buyers where id = ?", (buyer_id,)).fetchone()
    if not buyer:
        abort(404)
    if not can_access_buyer(buyer):
        abort(403)
    used = db().execute(
        "select count(*) as count from service_orders where buyer_id = ?",
        (buyer_id,),
    ).fetchone()["count"]
    if used:
        flash("è¿™ä¸ªç«™ç‚¹å·²æœ‰å·¥å•ï¼Œä¸èƒ½åˆ é™¤ã€‚å¯ä»¥ç¼–è¾‘ç«™ç‚¹èµ„æ–™ã€‚", "error")
        return redirect(url_for("buyers"))
    db().execute("delete from buyers where id = ?", (buyer_id,))
    db().commit()
  ózá¼­zÊ&ŠÛ^t€€€€€€€€€€€¤4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‹šr³š²‡¦^»¦Šc¦r¢šjš~—¢¾‹š¶—¦ª“¢ş–’k¾ò3¢¾ßò§–Â?š~—¢¾‹¢2–nÓ–B;¦7¢¾Wˆ¤4(€€€•á•ÁĞIÕ¹Ñ¥µ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰•ÉÉ½ÈˆèÍÑÈ¡•ÉÉ½È¥ô¤°€ĞÈÈ4(4(4(ŒƒŠRŠRŠR $…¥±äI•Á½ÉĞ€¡A¡…Í”€Ä¤ƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)‘•˜}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤è4(€€€€ˆˆ‰	Õ¥±„…¥±åI•Á½ÉÑM•ÉÙ¥”‰½Õ¹Ñ¼ÕÉÉ•¹ĞÉ•ÅÕ•ÍĞ½¹Ñ•áĞ¸ˆˆˆ4(€€€É•ÑÕÉ¸…¥±åI•Á½ÉÑM•ÉÙ¥”¡‘ˆ ¤°¹½Ü°œ¹ÕÍ•Él‰¥‰t°œ¹ÕÍ•Él‰¹…µ”‰t¤4(4(4)‘•˜}…¥}‘…¥±å}É•Á½ÉÑ}‰ÕÍ¥¹•ÍÍ}‘…Ñ” ¤€´øÍÑÈè4(€€€€ˆˆ‰•ĞÕÉÉ•¹Ğ‰ÕÍ¥¹•ÍÌ‘…Ñ”¥¸…ÁÀÑ¥µ•é½¹”¸ˆˆˆ4(€€€É•ÑÕÉ¸‘…Ñ•Ñ¥µ”¹¹½Ü¡…ÁÁ}Ñ¥µ•é½¹” ¤¤¹‘…Ñ” ¤¹¥Í½™½Éµ…Ğ ¤4(4(4)‘•˜}…ÕÑ½}ÁÉ•Á…É•}‘É…™Ñ}µ•‘¥„¡‘É…™Ñ}¥è¥¹Ğ¤€´ø±¥ÍĞè4(€€€€ˆˆ‰	•ÍĞµ•™™½ÉĞ…ÕÑ¼Á¥Á•±¥¹”™½È„‘É…™ĞƒŠP¹¼µ…¹Õ…°±¥­ÌÉ•ÅÕ¥É•¸4(4(€€€IÕ¹Ì°¥¸½É‘•È€¡•… ÍÑ•ÀÍ­¥ÁÁ•İ¡•¸…±É•…‘ä‘½¹”°‘•É…‘•ÌÉ…•™Õ±±ä¤è4(€€€€€€Ä¸‘¥Í½Ù•É}Á¡½Ñ½ÌèÍ…¸Í¥Ñ”Á¡½Ñ½Ì…¹‰Õ¥±Ñ¥µ•±¥¹”…¹‘¥‘…Ñ•Ì4(€€€€€€€€€¡½¹±äİ¡•¸Ñ¡”Ñ¥µ•±¥¹”¡…Ì¹½Ğ‰••¸Í…¹¹•å•Ğ¤¸4(€€€€€€È¸½¹™¥Éµ}Ñ¥µ•±¥¹”è…ÁÁ±ä…ÉÉ¥Ù…°½‘•Á…ÉÑÕÉ”Á¡½Ñ¼…¹‘¥‘…Ñ•Ì4(€€€€€€€€€¡™½É”µ½¹™¥Éµ•ìM-%AAİ¡•¸Ñ¡”ÕÍ•ÈÍ•ĞÑ¥µ•Ìµ…¹Õ…±±äÙ¥„4(€€€€€€€€ÕÍ•É}¥¹ÁÕĞ½µ…¹Õ…°Í½ÕÉ•Ì°Í¼¡Õµ…¸•‘¥ÑÌ…É”¹•Ù•È½Ù•ÉİÉ¥ÑÑ•¸¤¸4(€€€€€€Ì¸±…ÍÍ¥™å}Á¡½Ñ½ÌèY¥Í¥½¸µ±…ÍÍ¥™äÁ¡½Ñ½Ì…¹…ÕÑ¼µÍ•±•ĞÍ…™•Ñä€¬4(€€€€€€€€½¹ÍÑÉÕÑ¥½¸€¡Í•ÉÙ¥”¤Á¡½Ñ½Ì€¡½¹±äİ¡•¸¹½Ğ±…ÍÍ¥™¥•å•Ğ…¹4(€€€€€€€€Y¥Í¥½¸¥Ì•¹…‰±•ìÕÍ•ÈÍ•±•Ñ¥½¹Ì½É•µ½Ù…±Ì…É”ÁÉ•Í•ÉÙ•¤¸4(€€€€€€Ğ¸É•…±Õ±…Ñ•}µ¥±•…”è™½ÈÍ•±™}‘É¥Ù”İ½É­•ÉÌ±…­¥¹œ„ÍÕ•ÍÍ™Õ°4(€€€€€€€€É½ÕÑ”ƒŠP½µÁÕÑ”µ¥±•…”Ù¥„½½±”I½ÕÑ•Ì…¹•¹•É…Ñ”MÑ…Ñ¥Œ5…ÁÌ4(€€€€€€€€•Ù¥‘•¹”É•½É‘Ì¸4(4(€€€I•ÑÕÉ¹Ì„±¥ÍĞ½˜ì‰ÍÑ•Àˆ°€‰½¬ˆ°€¸¸¹ôÍÕµµ…É¥•Ìì¹•Ù•ÈÉ…¥Í•Ì¸4(€€€€ˆˆˆ4(€€€ÍÑ•ÁÌ€ômt4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(4(€€€‘•˜}±½…‘}‘É…™Ğ ¤è4(€€€€€€€É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸ÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡É½Ü¤¥˜É½Ü•±Í”9½¹”4(4(€€€‘•˜}ÉÕ¸¡ÍÑ•Á}¹…µ”°™¸¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€¥¹™¼€ô™¸ ¤½Èíô4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€ÍÑ•ÁÌ¹…ÁÁ•¹¡ì‰ÍÑ•ÀˆèÍÑ•Á}¹…µ”°€‰½¬ˆèQÉÕ”°€¨©¥¹™½ô¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè€€Œ…ÕÑ¼Á¥Á•±¥¹”µÕÍĞ¹•Ù•È‰É•…¬Ñ¡”…±±•È4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€€€€€Á…ÍÌ4(€€€€€€€€€€€ÍÑ•ÁÌ¹…ÁÁ•¹¡ì‰ÍÑ•ÀˆèÍÑ•Á}¹…µ”°€‰½¬ˆè…±Í”°€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¥ô¤4(4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Ü½È‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰½¹™¥Éµ•‰ôè4(€€€€€€€É•ÑÕÉ¸ÍÑ•ÁÌ4(€€€‘É…™Ğ€ô}±½…‘}‘É…™Ğ ¤4(4(€€€€Œ€Ä¤¥Í½Ù•ÈÁ¡½Ñ½Ì€¡½¹±ä¥˜¹½ĞÍ…¹¹•å•Ğ¤4(€€€¥˜‘É…™Ğ¹Á¡½Ñ½}Ñ¥µ•±¥¹•}ÍÑ…ÑÕÌ¥¸€¡9½¹”°€ˆˆ°€‰¹½Ñ}Í…¹¹•ˆ°€‰™…¥±•ˆ¤è4(€€€€€€€‘•˜}‘¥Í½Ù•È ¤è4(€€€€€€€€€€€Á¡½Ñ½}‘¥Í½Ù•Éä€ôA¡½Ñ½¥Í½Ù•ÉåM•ÉÙ¥” 4(€€€€€€€€€€€€€€€Í¡…É•‘}Á¡½Ñ½Í}É½½ĞõM!I}A!=Q=M}%H°4(€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}Á¡½Ñ½}™½±‘•É}™Õ¹ŒõÍ•ÉÙ¥•}½É‘•É}Á¡½Ñ½}™½±‘•È°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€Á¡½Ñ½}µ•Ñ…‘…Ñ„€ôA¡½Ñ½5•Ñ…‘…Ñ…M•ÉÙ¥”¡Í¡…É•‘}Á¡½Ñ½Í}É½½ĞõM!I}A!=Q=M}%H¤4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€™Á}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€€€€€‰Í•±•ĞÉ•±…Ñ¥Ù•}Á…Ñ °Á¡½Ñ½}ÑåÁ”™É½´™¥•±‘}Á¡½Ñ½Ìˆ4(€€€€€€€€€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€€€€€€€€€€€€€™Á}µ…À€ôíÉl‰É•±…Ñ¥Ù•}Á…Ñ ‰tè€¡Él‰Á¡½Ñ½}ÑåÁ”‰t½È€ˆˆ¤™½ÈÈ¥¸™Á}É½İÍô4(€€€€€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€€€€€™Á}µ…À€ôíô4(€€€€€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹‘¥Í½Ù•É}Á¡½Ñ½Í}™½É}‘É…™Ğ 4(€€€€€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€€€€€Á¡½Ñ½}‘¥Í½Ù•Éå}Í•ÉÙ¥”õÁ¡½Ñ½}‘¥Í½Ù•Éä°4(€€€€€€€€€€€€€€€Á¡½Ñ½}µ•Ñ…‘…Ñ…}Í•ÉÙ¥”õÁ¡½Ñ½}µ•Ñ…‘…Ñ„°4(€€€€€€€€€€€€€€€Á¡½Ñ½}ÑåÁ•}±½½­ÕÀõ±…µ‰‘„Àè™Á}µ…À¹•Ğ¡À¤½È9½¹”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•ÑÕÉ¸ì‰Á¡½Ñ½}½Õ¹ĞˆèÉ•ÍÕ±Ğ¹•Ğ ‰Á¡½Ñ½}½Õ¹Ğˆ¥ô4(€€€€€€€}ÉÕ¸ ‰‘¥Í½Ù•É}Á¡½Ñ½Ìˆ°}‘¥Í½Ù•È¤4(€€€€€€€‘É…™Ğ€ô}±½…‘}‘É…™Ğ ¤4(4(€€€€Œ€È¤½¹™¥É´Á¡½Ñ¼Ñ¥µ•±¥¹”€¡Í­¥Àİ¡•¸ÕÍ•ÈÍ•ĞÑ¥µ•Ìµ…¹Õ…±±ä¤4(€€€µ…¹Õ…±}Ñ¥µ•Ì€ô‘É…™Ğ¹…ÉÉ¥Ù…±}Ñ¥µ•}Í½ÕÉ”¥¸€ ‰ÕÍ•É}¥¹ÁÕĞˆ°€‰µ…¹Õ…°ˆ¤½È€ 4(€€€€€€€‘É…™Ğ¹‘•Á…ÉÑÕÉ•}Ñ¥µ•}Í½ÕÉ”¥¸€ ‰ÕÍ•É}¥¹ÁÕĞˆ°€‰µ…¹Õ…°ˆ¤4(€€€€¤4(€€€¥˜€ 4(€€€€€€€¹½Ğµ…¹Õ…±}Ñ¥µ•Ì4(€€€€€€€…¹‘É…™Ğ¹…ÉÉ¥Ù…±}Ñ¥µ•}Í½ÕÉ”€„ô€‰Á¡½Ñ½}Ñ¥µ•±¥¹•}½¹™¥Éµ•ˆ4(€€€€€€€…¹‘É…™Ğ¹Á¡½Ñ½}Ñ¥µ•±¥¹•}ÍÑ…ÑÕÌ4(€€€€€€€¹½Ğ¥¸€¡9½¹”°€ˆˆ°€‰¹½Ñ}Í…¹¹•ˆ°€‰¹½}Á¡½Ñ½Ìˆ°€‰™…¥±•ˆ¤4(€€€€¤è4(€€€€€€€}ÉÕ¸ ‰½¹™¥Éµ}Ñ¥µ•±¥¹”ˆ°±…µ‰‘„èÍÙŒ¹½¹™¥Éµ}Á¡½Ñ½}Ñ¥µ•±¥¹”¡‘É…™Ñ}¥°™½É”õQÉÕ”¤¤4(4(€€€€Œ€Ì¤Y¥Í¥½¸±…ÍÍ¥™ä€¬…ÕÑ¼µÍ•±•ĞÍ…™•Ñä½½¹ÍÑÉÕÑ¥½¸Á¡½Ñ½Ì4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€‘É…™Ğ€ô}±½…‘}‘É…™Ğ ¤4(€€€Ù¥Í¥½¹}™œ€ôÙ¥Í¥½¹}Í•ÑÑ¥¹Ì ¤4(€€€¥˜€ 4(€€€€€€€‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰‘É…™Ğˆ4(€€€€€€€…¹Ù¥Í¥½¹}™œ¹•Ğ ‰•¹…‰±•ˆ¤4(€€€€€€€…¹‘É…™Ğ¹Á¡½Ñ½}…¹‘¥‘…Ñ•Ì4(€€€€€€€…¹¹½Ğ‘É…™Ğ¹Á¡½Ñ½}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ4(€€€€¤è4(€€€€€€€‘•˜}±…ÍÍ¥™ä ¤è4(€€€€€€€€€€€Á¡½Ñ½}ÍÙŒ€ô}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤4(€€€€€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹±…ÍÍ¥™å}‘É…™Ñ}Á¡½Ñ½Ì 4(€€€€€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€€€€€Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥”õÁ¡½Ñ½}ÍÙŒ°4(€€€€€€€€€€€€€€€…¹…±åÍ¥Í}µ½‘•°õÙ¥Í¥½¹}™œ¹•Ğ ‰µ½‘•°ˆ°€ˆˆ¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡É•ÍÕ±Ğ¹•Ğ ‰•ÉÉ½Èˆ°€‰±…ÍÍ¥™¥…Ñ¥½¹}™…¥±•ˆ¤¤4(€€€€€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€€€€€‰Í•±•Ñ•‘}Í•ÉÙ¥•}½Õ¹ĞˆèÉ•ÍÕ±Ğ¹•Ğ ‰Í•±•Ñ•‘}Í•ÉÙ¥•}½Õ¹Ğˆ¤°4(€€€€€€€€€€€€€€€€‰Í•±•Ñ•‘}Í…™•ÑäˆèÉ•ÍÕ±Ğ¹•Ğ ‰Í•±•Ñ•‘}Í…™•Ñäˆ¤°4(€€€€€€€€€€€ô4(€€€€€€€}ÉÕ¸ ‰±…ÍÍ¥™å}Á¡½Ñ½Ìˆ°}±…ÍÍ¥™ä¤4(4(€€€€Œ€Ğ¤I•…±Õ±…Ñ”µ¥±•…”™½ÈÍ•±™}‘É¥Ù”İ½É­•ÉÌİ¥Ñ¡½ÕĞ„ÍÕ•ÍÍ™Õ°É½ÕÑ”4(€€€‘É…™Ğ€ô}±½…‘}‘É…™Ğ ¤4(€€€¥˜…¹ä 4(€€€€€€€Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€„ô€‰ÍÕ•ÍÌˆ4(€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌ4(€€€€¤è4(€€€€€€€‘•˜}µ¥±•…” ¤è4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡‘É…™Ğ¹Í•ÉÙ¥•}½É‘•É}¥¤4(€€€€€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€€€€€É•ÑÕÉ¸ì‰Í­¥ÁÁ•ˆè€‰½É‘•É}¹½Ñ}™½Õ¹‰ô4(€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ€ô€¡½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t½È€ˆˆ¤¥˜½É‘•È•±Í”€ˆˆ4(€€€€€€€€€€€•µÁ}É•Í½±Ù•È€ôµÁ±½å••I•Í½±ÕÑ¥½¹M•ÉÙ¥”¡‘ˆ ¤°œ¹ÕÍ•Él‰¥‰t°œ¹ÕÍ•Él‰¹…µ”‰t¤4(€€€€€€€€€€€ÑÉ…Ù•±}ÍÙŒ€ôQÉ…Ù•±M•ÉÙ¥”¡‘ˆ ¤°•µÁ}É•Í½±Ù•È¤4(€€€€€€€€€€€ÑÉ…Ù•±}ÍÙŒ¹Í•Ñ}‘•ÍÑ¥¹…Ñ¥½¹}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ°Í¥Ñ•}…‘‘É•ÍÌ¤4(€€€€€€€€€€€µ¥ÍÍ¥¹œ°}µÍÌ€ôÑÉ…Ù•±}ÍÙŒ¹Ù•É¥™å}ÑÉ…Ù•±}™¥•±‘Ì 4(€€€€€€€€€€€€€€€‘É…™Ğ¹İ½É­•ÉÌ°‰½½°¡Í¥Ñ•}…‘‘É•ÍÌ¹ÍÑÉ¥À ¤¤4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É½ÕÑ•Í}…Á¥}­•ä€ô•Ñ}½½±•}É½ÕÑ•Í}…Á¥}­•ä ¤4(€€€€€€€€€€€¥˜µ¥ÍÍ¥¹œè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸ì‰Í­¥ÁÁ•ˆè€‰µ¥ÍÍ¥¹}ÑÉ…Ù•±}™¥•±‘Ì‰ô4(€€€€€€€€€€€¥˜¹½ĞÉ½ÕÑ•Í}…Á¥}­•äè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸ì‰Í­¥ÁÁ•ˆè€‰¹½}É½ÕÑ•Í}…Á¥}­•ä‰ô4(4(€€€€€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞ€ 4(€€€€€€€€€€€€€€€½½±•I½ÕÑ•ÍM•ÉÙ¥”°4(€€€€€€€€€€€€€€€5¥±•…•M•ÉÙ¥”°4(€€€€€€€€€€€€€€€½½±•MÑ…Ñ¥5…ÁÍM•ÉÙ¥”°4(€€€€€€€€€€€€€€€5¥±•…•Ù¥‘•¹•M•ÉÙ¥”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É½ÕÑ•Í}ÍÙŒ€ô½½±•I½ÕÑ•ÍM•ÉÙ¥”¡É½ÕÑ•Í}…Á¥}­•ä¤4(€€€€€€€€€€€µ¥±•…•}ÍÙŒ€ô5¥±•…•M•ÉÙ¥”¡É½ÕÑ•Í}ÍÙŒ¤4(€€€€€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€„ô€‰ÍÕ•ÍÌˆè4(€€€€€€€€€€€€€€€€€€€QÉ…Ù•±M•ÉÙ¥”¹¥¹Ù…±¥‘…Ñ•}İ½É­•É}É½ÕÑ”¡Ü¤4(€€€€€€€€€€€µ¥±•…•}ÍÙŒ¹…±Õ±…Ñ•}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ¤4(4(€€€€€€€€€€€ÍÑ…Ñ¥}µ…ÁÍ}­•ä€ô•Ñ}½½±•}ÍÑ…Ñ¥}µ…ÁÍ}…Á¥}­•ä ¤4(€€€€€€€€€€€¥˜ÍÑ…Ñ¥}µ…ÁÍ}­•äè4(€€€€€€€€€€€€€€€•Ù¥‘•¹•}ÍÙŒ€ô5¥±•…•Ù¥‘•¹•M•ÉÙ¥” 4(€€€€€€€€€€€€€€€€€€€½½±•MÑ…Ñ¥5…ÁÍM•ÉÙ¥”¡ÍÑ…Ñ¥}µ…ÁÍ}­•ä¤°4(€€€€€€€€€€€€€€€€€€€½Ì¹Á…Ñ ¹©½¥¸¡Q}%H°€‰…¤µ‘…¥±äµÉ•Á½ÉĞµ‘É…™ÑÌˆ¤°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€ôô€‰ÍÕ•ÍÌˆè4(€€€€€€€€€€€€€€€€€€€€€€€É•½É€ô•Ù¥‘•¹•}ÍÙŒ¹•¹•É…Ñ•}•Ù¥‘•¹” 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ü°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õ‘É…™Ğ¹Í•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ”õ‘É…™Ğ¹É•Á½ÉÑ}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹•É…Ñ•‘}‰äõœ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ñ}•Ù¥‘•¹•}É•½É‘Ìõ‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì°4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì€ô5¥±•…•Ù¥‘•¹•M•ÉÙ¥”¹ÕÁÍ•ÉÑ}•Ù¥‘•¹•}É•½É 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì°É•½É4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}•Ù¥‘•¹•}¥€ôÉ•½É¹•Ù¥‘•¹•}¥4(€€€€€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}•Ù¥‘•¹•}Á…Ñ €ôÉ•½É¹™¥±•}É•±…Ñ¥Ù•}Á…Ñ 4(€€€€€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ô€ 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€É•½É¹•Ù¥‘•¹•}ÍÑ…ÑÕÌ€ôô€‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆ4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ¤4(€€€€€€€€€€€É•ÑÕÉ¸ì‰…±Õ±…Ñ•ˆèQÉÕ•ô4(€€€€€€€}ÉÕ¸ ‰É•…±Õ±…Ñ•}µ¥±•…”ˆ°}µ¥±•…”¤4(4(€€€É•ÑÕÉ¸ÍÑ•ÁÌ4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½¡…Ğˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}¡…Ğ ¤è4(€€€€ˆˆ‰5…¥¸¡…Ğ•¹‘Á½¥¹Ğè¹…ÑÕÉ…°±…¹Õ…”€´øÑ¥½¸€´øÉ…™ĞÕÁ‘…Ñ”€´øÁÉ•Ù¥•Ü¸4(4(€€€I•ÅÕ•ÍĞ)M=8è4(€€€€€€€µ•ÍÍ…”èÍÑÈ€¡É•ÅÕ¥É•¤4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¥è¥¹Ğ€¡É•ÅÕ¥É•¤4(€€€€€€€‘É…™Ñ}¥è¥¹Ğ€¡½ÁÑ¥½¹…°°™½È½¹Ñ¥¹Õ¥¹œ…¸•á¥ÍÑ¥¹œ‘É…™Ğ¤4(€€€€€€€É•Á½ÉÑ}‘…Ñ”èÍÑÈ€¡½ÁÑ¥½¹…°°‘•™…Õ±ÑÌÑ¼ÕÉÉ•¹Ğ‰ÕÍ¥¹•ÍÌ‘…Ñ”¤4(€€€€€€€…Ñ¥½¹}¥èÍÑÈ€¡½ÁÑ¥½¹…°°™½È¥‘•µÁ½Ñ•¹äì…ÕÑ¼µ•¹•É…Ñ•¥˜µ¥ÍÍ¥¹œ¤4(4(€€€I•ÍÁ½¹Í”)M=8è4(€€€€€€€½¬è‰½½°4(€€€€€€€‘É…™Ñ}¥è¥¹Ğ4(€€€€€€€ÁÉ•Ù¥•Üè‘¥Ğ€¡…¥±åI•Á½ÉÑÉ…™ĞÁÉ•Ù¥•Ü¤4(€€€€€€€µ•ÍÍ…”èÍÑÈ€¡…Ñ¥½¸É•ÍÕ±Ğ½È±…É¥™¥…Ñ¥½¸ÅÕ•ÍÑ¥½¸¤4(€€€€€€€±…É¥™¥…Ñ¥½¹}É•ÅÕ¥É•è‰½½°4(€€€€€€€µ¥ÍÍ¥¹}™¥•±‘Ìè±¥ÍĞ4(€€€€€€€•ÉÉ½ÈèÍÑÈ€¡½¹±ä¥˜½¬õ™…±Í”¤4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€‰½‘ä€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€µ•ÍÍ…”€ôÍÑÈ¡‰½‘ä¹•Ğ ‰µ•ÍÍ…”ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€Í•ÉÙ¥•}½É‘•É}¥€ô‰½‘ä¹•Ğ ‰Í•ÉÙ¥•}½É‘•É}¥ˆ¤4(€€€‘É…™Ñ}¥€ô‰½‘ä¹•Ğ ‰‘É…™Ñ}¥ˆ¤4(€€€É•Á½ÉÑ}‘…Ñ”€ô‰½‘ä¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤½È}…¥}‘…¥±å}É•Á½ÉÑ}‰ÕÍ¥¹•ÍÍ}‘…Ñ” ¤4(€€€…Ñ¥½¹}¥€ô‰½‘ä¹•Ğ ‰…Ñ¥½¹}¥ˆ¤½È•¹•É…Ñ•}…Ñ¥½¹}¥ ¤4(4(€€€¥˜¹½Ğµ•ÍÍ…”è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šÚ#š¿’â7¢÷’âë¦è‰ô¤°€ĞÀÀ4(€€€¥˜¹½ĞÍ•ÉÙ¥•}½É‘•É}¥è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂDÍ•ÉÙ¥•}½É‘•É}¥‰ô¤°€ĞÀÀ4(4(€€€€ŒY•É¥™äÍ•ÉÙ¥”½É‘•È•á¥ÍÑÌ…¹ÕÍ•È¡…Ì…•ÍÌ4(€€€ÑÉäè4(€€€€€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡Í•ÉÙ¥•}½É‘•É}¥¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–Ş—–6W’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€Í•ÑÑ¥¹Ì€ô‘••ÁÍ••­}…ÍÍ¥ÍÑ…¹Ñ}Í•ÑÑ¥¹Ì ¤4(€€€¥¹Ñ•¹Ñ}Í•ÉÙ¥”€ô%%¹Ñ•¹ÑM•ÉÙ¥”¡Í•ÑÑ¥¹Ì¤4(€€€¥˜¹½Ğ¥¹Ñ•¹Ñ}Í•ÉÙ¥”¹¥Í}…Ù…¥±…‰±” ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰••ÁM••¬ƒšr«–B¿R£š"[šr«¦7ö¸A$-•ä‰ô¤°€ÔÀÌ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(4(€€€€Œ•Ğ½ÈÉ•…Ñ”‘É…™Ğ4(€€€‘É…™Ñ}É½Ü€ô9½¹”4(€€€¥˜‘É…™Ñ}¥è4(€€€€€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€•±Í”è4(€€€€€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}…Ñ¥Ù•}‘É…™Ğ¡Í•ÉÙ¥•}½É‘•É}¥°É•Á½ÉÑ}‘…Ñ”¤4(€€€€€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€€€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹É•…Ñ•}‘É…™Ğ 4(€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õÍ•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ”õÉ•Á½ÉÑ}‘…Ñ”°4(€€€€€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌõ½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t°4(€€€€€€€€€€€€€€€…¥}µ½‘•°õÍ•ÑÑ¥¹Íl‰µ½‘•°‰t°4(€€€€€€€€€€€€¤4(4(€€€‘É…™Ñ}¥€ô‘É…™Ñ}É½İl‰¥‰t4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(4(€€€€Œ%‘•µÁ½Ñ•¹ä¡•¬4(€€€•á•ÕÑ•‘}¥‘Ì€ôÍÙŒ¹•Ñ}•á•ÕÑ•‘}…Ñ¥½¹}¥‘Ì¡‘É…™Ñ}¥¤4(€€€¥˜…Ñ¥½¹}¥¥¸•á•ÕÑ•‘}¥‘Ìè4(€€€€€€€€ŒI•ÑÕÉ¸ÕÉÉ•¹Ğ‘É…™ĞÍÑ…Ñ”İ¥Ñ¡½ÕĞÉ”µ•á•ÕÑ¥¹œ4(€€€€€€€ÁÉ•Ù¥•Ü€ôÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ğ¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€€€€€‰ÁÉ•Ù¥•ÜˆèÁÉ•Ù¥•Ü°4(€€€€€€€€€€€€‰µ•ÍÍ…”ˆè€‹šN7’ös–ŞËš&Ÿ¢†3¾ò#–æ¶'¢ŞÏ¢ş¾ò$ˆ°4(€€€€€€€€€€€€‰±…É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆè‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•°4(€€€€€€€€€€€€‰µ¥ÍÍ¥¹}™¥•±‘Ìˆè‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì°4(€€€€€€€€€€€€‰¥‘•µÁ½Ñ•¹ĞˆèQÉÕ”°4(€€€€€€€ô¤4(4(€€€€ŒI•©•Ğµ•ÍÍ…•Ì•á••‘¥¹œµ…à±•¹Ñ €¡‘¼9=PÍ¥±•¹Ñ±äÑÉÕ¹…Ñ”‰ÕÍ¥¹•ÍÌ¥¹ÍÑÉÕÑ¥½¹Ì¤4(€€€€ŒQÉÕ¹…Ñ¥½¸½Õ±ÕĞÉ¥Ñ¥…°¥¹™¼±¥­”€‹–òƒ’â'’î+–’§’ö?¦K–ê\ˆ…ĞÑ¡”•¹°4(€€€€Œ…ÕÍ¥¹œ¥¹½ÉÉ•Ğµ¥±•…”…±Õ±…Ñ¥½¸¸UÍ•ÈµÕÍĞÍ¡½ÉÑ•¸Ñ¡”µ•ÍÍ…”¸4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¹‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥”¥µÁ½ÉĞ5a}5MM}19Q 4(€€€¥˜±•¸¡µ•ÍÍ…”¤€ø5a}5MM}19Q è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‹šÚ#š¿¢ş¦Vÿ¾ò!í±•¸¡µ•ÍÍ…”¥÷–¶_²›¾ò'¾ò3šr–’Ÿ–¢ºàí5a}5MM}19Q!ôƒ–¶_²›¢¾ßò§~·šÚ#š¿–B;¦7¢¾Wˆ°4(€€€€€€€€€€€€‰•ÉÉ½É}½‘”ˆè€‰µ•ÍÍ…•}Ñ½½}±½¹œˆ°4(€€€€€€€ô¤°€ĞÄÌ4(4(€€€€Œ‘ÕÍ•Èµ•ÍÍ…”Ñ¼½¹Ù•ÉÍ…Ñ¥½¸½¹Ñ•áĞ¸Q¡”É•ÑÕÉ¹•±¥ÍĞ¥¹±Õ‘•ÌÑ¡”4(€€€€Œ©ÕÍĞµ…‘‘•ÕÍ•Èµ•ÍÍ…”ìÁ…ÍÌ¡¥ÍÑ½Éä]%Q!=UP¥ĞÑ¼••ÁM••¬‰•…ÕÍ”4(€€€€Œ‰Õ¥±‘}ÕÍ•É}ÁÉ½µÁĞ…±É•…‘ä…ÁÁ•¹‘ÌÑ¡”ÕÉÉ•¹Ğµ•ÍÍ…”…ÌÑ¡”™¥¹…°ÑÕÉ¸¸4(€€€½¹Ù•ÉÍ…Ñ¥½¹}¡¥ÍÑ½Éä€ôÍÙŒ¹…‘‘}½¹Ù•ÉÍ…Ñ¥½¹}µ•ÍÍ…”¡‘É…™Ñ}¥°€‰ÕÍ•Èˆ°µ•ÍÍ…”¥lè´Åt4(4(€€€€Œ	Õ¥±•á¥ÍÑ¥¹œ‘É…™ĞÍÕµµ…Éä™½È½¹Ñ•áĞ€¡Ñ½­•¸µ•™™¥¥•¹Ğ°¹¼™Õ±°¡¥ÍÑ½Éä¤4(€€€‘É…™Ñ}ÍÕµµ…Éä€ôÍÙŒ¹‰Õ¥±‘}‘É…™Ñ}ÍÕµµ…Éä¡‘É…™Ğ¤4(4(€€€€ŒA¡…Í”€Èè%¹¥Ñ¥…±¥é”½¹Ñ•áĞÍ•ÉÙ¥•Ì4(€€€•µÁ}É•Í½±Ù•È€ôµÁ±½å••I•Í½±ÕÑ¥½¹M•ÉÙ¥”¡‘ˆ ¤°œ¹ÕÍ•Él‰¥‰t°œ¹ÕÍ•Él‰¹…µ”‰t¤4(€€€ÑÉ…Ù•±}ÍÙŒ€ôQÉ…Ù•±M•ÉÙ¥”¡‘ˆ ¤°•µÁ}É•Í½±Ù•È¤4(€€€½É‘•É}Ñà€ô]½É­=É‘•É½¹Ñ•áÑM•ÉÙ¥”¡‘ˆ ¤°œ¹ÕÍ•Él‰¥‰t°œ¹ÕÍ•Él‰¹…µ”‰t¤4(4(€€€€Œ…±°••ÁM••¬4(€€€É•ÍÕ±Ğ€ô¥¹Ñ•¹Ñ}Í•ÉÙ¥”¹Á…ÉÍ•}¥¹Ñ•¹Ğ 4(€€€€€€€ÕÍ•É}µ•ÍÍ…”õµ•ÍÍ…”°4(€€€€€€€ÕÉÉ•¹Ñ}‰ÕÍ¥¹•ÍÍ}‘…Ñ”õÉ•Á½ÉÑ}‘…Ñ”°4(€€€€€€€ÕÉÉ•¹Ñ}Ñ¥µ•é½¹”õ•Ñ}Ñ¥µ•é½¹•}¹…µ” ¤°4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õÍ•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¹Õµ‰•Èõ½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°4(€€€€€€€Í¥Ñ•}…‘‘É•ÍÌõ½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t½È€ˆˆ°4(€€€€€€€ÕÉÉ•¹Ñ}ÕÍ•É}¹…µ”õœ¹ÕÍ•Él‰¹…µ”‰t°4(€€€€€€€•á¥ÍÑ¥¹}‘É…™Ñ}ÍÕµµ…Éäõ‘É…™Ñ}ÍÕµµ…Éä°4(€€€€€€€½¹Ù•ÉÍ…Ñ¥½¹}¡¥ÍÑ½Éäõ½¹Ù•ÉÍ…Ñ¥½¹}¡¥ÍÑ½Éä°4(€€€€¤4(4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹½¬è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½ÈˆèÉ•ÍÕ±Ğ¹•ÉÉ½È°4(€€€€€€€€€€€€‰•ÉÉ½É}½‘”ˆèÉ•ÍÕ±Ğ¹•ÉÉ½É}½‘”°4(€€€€€€€ô¤°€ĞÈÈ4(4(€€€…Ñ¥½¸€ôÉ•ÍÕ±Ğ¹…Ñ¥½¸4(4(€€€€ŒMÑ…Ñ”µ…¡¥¹”è½¹™¥Éµ•‘É…™ÑÌ…¹¹½Ğ‰”Í¥±•¹Ñ±äµ½‘¥™¥•¸4(€€€‘É…™Ñ}ÍÑ…ÑÕÌ€ô‘É…™Ñ}É½Ü¹•Ğ ‰ÍÑ…ÑÕÌˆ°€‰‘É…™Ğˆ¤4(€€€¥˜¹½Ğ…¥±åI•Á½ÉÑM•ÉÙ¥”¹…¹}•á•ÕÑ•}…Ñ¥½¸¡‘É…™Ñ}ÍÑ…ÑÕÌ°…Ñ¥½¸¹¥¹Ñ•¹Ğ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‰É…™Ğƒ–ŞË†»¢º“¾ò!ÍÑ…ÑÕÌõí‘É…™Ñ}ÍÑ…ÑÕÍ÷¾ò'¾ò3–š¦r’ş»šRç¢¾ß–#¢ÂR É•½Á•¸ˆ°4(€€€€€€€€€€€€‰•ÉÉ½É}½‘”ˆè€‰‘É…™Ñ}½¹™¥Éµ•ˆ°4(€€€€€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€ô¤°€ĞÀä4(4(€€€€Œ¡•¬¥˜¥¹Ñ•¹Ğ¥Ì¥µÁ±•µ•¹Ñ•¥¸A¡…Í”€Ä¼È4(€€€¥˜¹½Ğ¥Í}Á¡…Í”Å}¥µÁ±•µ•¹Ñ•¡…Ñ¥½¸¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‹šN7’öpí…Ñ¥½¸¹¥¹Ñ•¹Ñôƒ–Â–r£–B;î·¦bÛšº×–º{:Àˆ°4(€€€€€€€€€€€€‰•ÉÉ½É}½‘”ˆè€‰¹½Ñ}¥µÁ±•µ•¹Ñ•ˆ°4(€€€€€€€ô¤°€ÔÀÄ4(4(€€€€Œ%˜$Í…åÌ‘…Ñ”¥ÌÍ•Ğ°ÕÍ”¥Ğ€¡ÕÍ•È•áÁ±¥¥Ñ±äµ•¹Ñ¥½¹•„‘…Ñ”¤4(€€€¥˜…Ñ¥½¸¹‘…Ñ”…¹…Ñ¥½¸¹‘…Ñ”€„ôÉ•Á½ÉÑ}‘…Ñ”è4(€€€€€€€‘É…™Ğ¹É•Á½ÉÑ}‘…Ñ”€ô…Ñ¥½¸¹‘…Ñ”4(€€€€€€€É•Á½ÉÑ}‘…Ñ”€ô…Ñ¥½¸¹‘…Ñ”4(4(€€€€ŒA¡…Í”€ÈèI•Í½±Ù”İ½É­•ÉÌÑ¼É•…°ÕÍ•É}¥‘Ì4(€€€É•Í½±Ù•‘}İ½É­•ÉÌ€ômt4(€€€±…É¥™¥…Ñ¥½¹}µ•ÍÍ…•Ì€ômt4(€€€•µÁ±½å••}…¹‘¥‘…Ñ•Ì€ômt4(4(€€€¥˜…Ñ¥½¸¹İ½É­•ÉÌè4(€€€€€€€™½Èİ¤¥¸…Ñ¥½¸¹İ½É­•ÉÌè4(€€€€€€€€€€€•µÁ}É•ÍÕ±Ğ€ô•µÁ}É•Í½±Ù•È¹É•Í½±Ù”¡İ¤¹¹…µ”¤4(€€€€€€€€€€€¥˜•µÁ}É•ÍÕ±Ğ¹É•Í½±Ù•è4(€€€€€€€€€€€€€€€É•Í½±Ù•‘}İ½É­•ÉÌ¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€€€€€€€€€‰ÕÍ•É}¥ˆè•µÁ}É•ÍÕ±Ğ¹ÕÍ•É}¥°4(€€€€€€€€€€€€€€€€€€€€‰¹…µ”ˆè•µÁ}É•ÍÕ±Ğ¹¹…µ”°4(€€€€€€€€€€€€€€€€€€€€‰ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸ˆèİ¤¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸°4(€€€€€€€€€€€€€€€€€€€€‰½É¥¥¸ˆèİ¤¹½É¥¥¸°4(€€€€€€€€€€€€€€€ô¤4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€±…É¥™¥…Ñ¥½¹}µ•ÍÍ…•Ì¹…ÁÁ•¹¡•µÁ}É•ÍÕ±Ğ¹±…É¥™¥…Ñ¥½¹}ÅÕ•ÍÑ¥½¸¤4(€€€€€€€€€€€€€€€¥˜•µÁ}É•ÍÕ±Ğ¹…¹‘¥‘…Ñ•Ìè4(€€€€€€€€€€€€€€€€€€€•µÁ±½å••}…¹‘¥‘…Ñ•Ì¹•áÑ•¹¡•µÁ}É•ÍÕ±Ğ¹…¹‘¥‘…Ñ•Ì¤4(4(€€€€Œá•ÕÑ”…Ñ¥½¸½¸‘É…™Ğ€¡İ¥Ñ É•Í½±Ù•İ½É­•ÉÌ™½ÈÕÍ•É}¥µ…Ñ¡¥¹œ¤4(€€€‘É…™Ğ°•á•}µ•ÍÍ…”€ôÍÙŒ¹•á•ÕÑ•}…Ñ¥½¸¡‘É…™Ğ°…Ñ¥½¸°É•Í½±Ù•‘}İ½É­•ÉÌõÉ•Í½±Ù•‘}İ½É­•ÉÌ¤4(4(€€€€ŒA¡…Í”€ÈèM•Ğ‘•ÍÑ¥¹…Ñ¥½¸™É½´Í•ÉÙ¥•}½É‘•È¹Í¥Ñ•}…‘‘É•ÍÌ™½È…±°İ½É­•ÉÌ4(€€€Í¥Ñ•}…‘‘É•ÍÌ€ô½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t½È€ˆˆ4(€€€ÑÉ…Ù•±}ÍÙŒ¹Í•Ñ}‘•ÍÑ¥¹…Ñ¥½¹}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ°Í¥Ñ•}…‘‘É•ÍÌ¤4(4(€€€€ŒA¡…Í”€ÈèÁÁ±ä•µÁ±½å•”‘•™…Õ±Ğ½É¥¥¸™½ÈÍ•±™}‘É¥Ù”İ½É­•ÉÌ¸4(€€€€Œ%˜Ñ¡”ÕÍ•È•áÁ±¥¥Ñ±äÍ…¥Ñ¡•ä‘•Á…ÉĞ™É½´¡½µ”€ ‹’î;–ºÛ–ë–>Dˆ¤°Ñ¡”4(€€€€Œ•µÁ±½å•”‘•™…Õ±Ğ…‘‘É•ÍÌ€¡ÕÍ•ÉÌ¹…‘‘É•ÍÌ¤½Õ¹ÑÌ…Ì…¸•áÁ±¥¥Ğ4(€€€€Œ½¹™¥Éµ…Ñ¥½¸è½É¥¥¹}½¹™¥Éµ•õQÉÕ”€¡¹¼=I%´ÀÀÈ¤°…¹µ¥±•…”½É½ÕÑ”4(€€€€Œ…¸‰”•¹•É…Ñ•‘¥É•Ñ±ä¸]¥Ñ¡½ÕĞÍÕ İ½É‘¥¹œÑ¡”…‘‘É•ÍÌÉ•µ…¥¹Ì„4(€€€€ŒÍÕ•ÍÑ¥½¸Ñ¡…ĞÍÑ¥±°¹••‘Ì½¹™¥Éµ…Ñ¥½¸€¡•á¥ÍÑ¥¹œ‰•¡…Ù¥½ÕÈ¤¸4(€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹ÕÍ•É}¥€ø€Àè4(€€€€€€€€€€€™É½µ}¡½µ”€ôÑÉ…Ù•±}ÍÙŒ¹¥Í}¡½µ•}½É¥¥¸¡Ü¹½É¥¥¸¤4(€€€€€€€€€€€¥˜€¡¹½ĞÜ¹½É¥¥¸¤½È™É½µ}¡½µ”è4(€€€€€€€€€€€€€€€‘•™…Õ±Ñ}…‘‘È€ô•µÁ}É•Í½±Ù•È¹•Ñ}•µÁ±½å••}‘•™…Õ±Ñ}…‘‘É•ÍÌ¡Ü¹ÕÍ•É}¥¤4(€€€€€€€€€€€€€€€¥˜‘•™…Õ±Ñ}…‘‘Èè4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¸€ô‘•™…Õ±Ñ}…‘‘È4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¹}Í½ÕÉ”€ô€‰•µÁ±½å••}‘•™…Õ±Ğˆ4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¹}½¹™¥Éµ•€ô™É½µ}¡½µ”4(€€€€€€€€€€€€€€€€€€€¥˜™É½µ}¡½µ”è4(€€€€€€€€€€€€€€€€€€€€€€€ÑÉ…Ù•±}ÍÙŒ¹¥¹Ù…±¥‘…Ñ•}İ½É­•É}É½ÕÑ”¡Ü¤4(4(€€€€ŒA¡…Í”€ÈèY•É¥™äÑÉ…Ù•°™¥•±‘ÌÕÍ¥¹œQÉ…Ù•±M•ÉÙ¥”4(€€€µ¥ÍÍ¥¹œ°ÑÉ…Ù•±}µ•ÍÍ…•Ì€ôÑÉ…Ù•±}ÍÙŒ¹Ù•É¥™å}ÑÉ…Ù•±}™¥•±‘Ì 4(€€€€€€€‘É…™Ğ¹İ½É­•ÉÌ°‰½½°¡Í¥Ñ•}…‘‘É•ÍÌ¹ÍÑÉ¥À ¤¤4(€€€€¤4(4(€€€€ŒA¡…Í”€Íè…±Õ±…Ñ”µ¥±•…”™½È•±¥¥‰±”İ½É­•ÉÌ€¡½¹±äİ¡•¸¹¼µ¥ÍÍ¥¹œ™¥•±‘Ì¤4(€€€€Œ½½±”I½ÕÑ•Ì¥Ì½¹±ä…±±•™½ÈÍ•±™}‘É¥Ù”İ½É­•ÉÌİ¥Ñ ½¹™¥Éµ•½É¥¥¸€¬‘•ÍÑ¥¹…Ñ¥½¸€¬½Ù•É¹¥¡Ğ4(€€€µ¥±•…•}…±Õ±…Ñ•€ô…±Í”4(€€€¥˜¹½Ğµ¥ÍÍ¥¹œè4(€€€€€€€É½ÕÑ•Í}…Á¥}­•ä€ô•Ñ}½½±•}É½ÕÑ•Í}…Á¥}­•ä ¤4(€€€€€€€¥˜É½ÕÑ•Í}…Á¥}­•äè4(€€€€€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞ½½±•I½ÕÑ•ÍM•ÉÙ¥”°5¥±•…•M•ÉÙ¥”4(€€€€€€€€€€€É½ÕÑ•Í}ÍÙŒ€ô½½±•I½ÕÑ•ÍM•ÉÙ¥”¡É½ÕÑ•Í}…Á¥}­•ä¤4(€€€€€€€€€€€µ¥±•…•}ÍÙŒ€ô5¥±•…•M•ÉÙ¥”¡É½ÕÑ•Í}ÍÙŒ¤4(€€€€€€€€€€€€Œ=¹±ä…±Õ±…Ñ”¥˜É•…±Õ±…Ñ•}µ¥±•…”¥¹Ñ•¹Ğ=Hİ½É­•ÉÌ¡…Ù”¹¼É½ÕÑ”å•Ğ4(€€€€€€€€€€€¹••‘Í}…±Œ€ô…Ñ¥½¸¹¥¹Ñ•¹Ğ€ôô€‰É•…±Õ±…Ñ•}µ¥±•…”ˆ½È…¹ä 4(€€€€€€€€€€€€€€€Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€„ô€‰ÍÕ•ÍÌˆ™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌ¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ4(€€€€€€€€€€€€¤4(€€€€€€€€€€€¥˜¹••‘Í}…±Œè4(€€€€€€€€€€€€€€€µ¥±•…•}ÍÙŒ¹…±Õ±…Ñ•}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ¤4(€€€€€€€€€€€€€€€µ¥±•…•}…±Õ±…Ñ•€ôQÉÕ”4(€€€€€€€•±Í”è4(€€€€€€€€€€€€Œ9¼A$­•ä½¹™¥ÕÉ•€´µ…É¬Ù•É¥™¥…Ñ¥½¸É•ÅÕ¥É•4(€€€€€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹¹½ĞÜ¹É½ÕÑ•}ÍÑ…ÑÕÌè4(€€€€€€€€€€€€€€€€€€€Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€ô€‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆ4(€€€€€€€€€€€€€€€€€€€Ü¹É½ÕÑ•}•ÉÉ½È€ô€‰½½±”I½ÕÑ•ÌA$­•ä¹½Ğ½¹™¥ÕÉ•ˆ4(4(€€€€Œ½µ‰¥¹”…±°µ¥ÍÍ¥¹œ™¥•±‘Ì…¹±…É¥™¥…Ñ¥½¸µ•ÍÍ…•Ì4(€€€…±±}µ¥ÍÍ¥¹œ€ô±¥ÍĞ¡Í•Ğ¡µ¥ÍÍ¥¹œ¤¤4(€€€…±±}±…É¥™¥…Ñ¥½¹Ì€ô±¥ÍĞ¡Í•Ğ¡±…É¥™¥…Ñ¥½¹}µ•ÍÍ…•Ì€¬ÑÉ…Ù•±}µ•ÍÍ…•Ì¤¤4(4(€€€¥˜…Ñ¥½¸¹±…É¥™¥…Ñ¥½¹}É•ÅÕ¥É•è4(€€€€€€€…±±}µ¥ÍÍ¥¹œ¹•áÑ•¹¡…Ñ¥½¸¹µ¥ÍÍ¥¹}™¥•±‘Ì¤4(4(€€€€ŒØÀ¸Ä¸ÈÌØèƒšZ÷–Ş—––ºçšb¿š>?¢şÃšZ–¶_¢3¦v{¢†£š‚ó–2[–¶_šº×¾ò#R£š"Ü€ÈÀÈØ´Àä´ÄÜƒ–Ï¶[¾ò'4(€€€€Œƒš¢‡–z/–>¿¢÷’î7š*(İ½É­}¥Ñ•µÌ¸¨ƒš‚’âë–ú†»¢º“¾ò3¢şg¦3î’â¢şšî“¾ò3¦ÿ–7¢›–>G†»¢º“–òçª_4(€€€…±±}µ¥ÍÍ¥¹œ€ôl4(€€€€€€€˜™½È˜¥¸…±±}µ¥ÍÍ¥¹œ4(€€€€€€€¥˜¹½ĞÍÑÈ¡˜¤¹±½İ•È ¤¹ÍÑ…ÉÑÍİ¥Ñ  ‰İ½É­}¥Ñ•µÌ¸ˆ¤4(€€€t4(4(€€€¥˜…±±}µ¥ÍÍ¥¹œ½È…±±}±…É¥™¥…Ñ¥½¹Ìè4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ôQÉÕ”4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ô±¥ÍĞ¡Í•Ğ¡…±±}µ¥ÍÍ¥¹œ¤¤4(€€€•±Í”è4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ô…±Í”4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ômt4(4(€€€€ŒI•½É…Ñ¥½¸€¡¥‘•µÁ½Ñ•¹Ğ¤4(€€€ÍÙŒ¹É•½É‘}…Ñ¥½¸¡‘É…™Ñ}¥°…Ñ¥½¹}¥°…Ñ¥½¸°É•ÍÕ±Ğô‰½¬ˆ¤4(4(€€€€ŒM…Ù”‘É…™Ğİ¥Ñ ½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ4(€€€ÑÉäè4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğ…Ì•áŒè4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¤°4(€€€€€€€€€€€€‰•ÉÉ½É}½‘”ˆè€‰Ù•ÉÍ¥½¹}½¹™±¥Ğˆ°4(€€€€€€€ô¤°€ĞÀä4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(4(€€€€ŒÕÑ¼µ•‘¥„Á¥Á•±¥¹”€¡ØÀ¸Ä¸ÈÌĞ¤è‘¥Í½Ù•ÈÁ¡½Ñ½Ì°½¹™¥É´Ñ¥µ•±¥¹”°4(€€€€Œ…ÕÑ¼µÍ•±•ĞÍ…™•Ñä½½¹ÍÑÉÕÑ¥½¸Á¡½Ñ½Ì°É•…±Õ±…Ñ”µ¥±•…”ƒŠP…±°4(€€€€Œİ¥Ñ¡½ÕĞµ…¹Õ…°±¥­Ì¸%‘•µÁ½Ñ•¹Ğè™¥¹¥Í¡•ÍÑ•ÁÌ…É”Í­¥ÁÁ•…¹4(€€€€Œµ…¹Õ…°ÕÍ•È½Ù•ÉÉ¥‘•Ì…É”¹•Ù•È½Ù•ÉİÉ¥ÑÑ•¸¸4(€€€…ÕÑ½}ÍÑ•ÁÌ€ô}…ÕÑ½}ÁÉ•Á…É•}‘É…™Ñ}µ•‘¥„¡‘É…™Ñ}¥¤4(€€€¥˜…¹ä¡Ì¹•Ğ ‰½¬ˆ¤™½ÈÌ¥¸…ÕÑ½}ÍÑ•ÁÌ¤è4(€€€€€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(4(€€€ÁÉ•Ù¥•Ü€ôÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ğ¤4(€€€±…É¥™¥…Ñ¥½¹}µÍœ€ô…Ñ¥½¸¹±…É¥™¥…Ñ¥½¹}ÅÕ•ÍÑ¥½¸½È€ˆˆ4(€€€¥˜…±±}±…É¥™¥…Ñ¥½¹Ì…¹¹½Ğ±…É¥™¥…Ñ¥½¹}µÍœè4(€€€€€€€±…É¥™¥…Ñ¥½¹}µÍœ€ô€‹¾òlˆ¹©½¥¸¡…±±}±…É¥™¥…Ñ¥½¹Ì¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÁÉ•Ù¥•ÜˆèÁÉ•Ù¥•Ü°4(€€€€€€€€‰µ•ÍÍ…”ˆè•á•}µ•ÍÍ…”°4(€€€€€€€€‰±…É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆè‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•°4(€€€€€€€€‰µ¥ÍÍ¥¹}™¥•±‘Ìˆè‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì°4(€€€€€€€€‰±…É¥™¥…Ñ¥½¹}ÅÕ•ÍÑ¥½¸ˆè±…É¥™¥…Ñ¥½¹}µÍœ°4(€€€€€€€€‰•µÁ±½å••}…¹‘¥‘…Ñ•Ìˆè•µÁ±½å••}…¹‘¥‘…Ñ•Ì°4(€€€€€€€€‰…Ñ¥½¹}¥ˆè…Ñ¥½¹}¥°4(€€€ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰•Ğ‘É…™ĞÁÉ•Ù¥•Ü¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€€‰ÁÉ•Ù¥•ÜˆèÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ğ¤°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½…ÕÑ¼µÁÉ•Á…É”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}…ÕÑ½}ÁÉ•Á…É”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰=¹”µ±¥¬…ÕÑ¼Á¥Á•±¥¹”è‘¥Í½Ù•ÈÁ¡½Ñ½Ì€´ø½¹™¥É´Ñ¥µ•±¥¹”€´ø4(€€€±…ÍÍ¥™äÁ¡½Ñ½Ì€¡…ÕÑ¼µÍ•±•ĞÍ…™•Ñä½½¹ÍÑÉÕÑ¥½¸Á¡½Ñ½Ì¤€´ø4(€€€É•…±Õ±…Ñ”µ¥±•…”¸4(4(€€€%‘•µÁ½Ñ•¹Ğ…¹‰•ÍĞµ•™™½ÉĞè…±É•…‘äµ™¥¹¥Í¡•ÍÑ•ÁÌ…É”Í­¥ÁÁ•°µ…¹Õ…°4(€€€ÕÍ•È½Ù•ÉÉ¥‘•Ì€¡Ñ¥µ•Ì°Á¡½Ñ¼Í•±•Ñ¥½¹Ì¤…É”ÁÉ•Í•ÉÙ•°…¹™…¥±ÕÉ•Ì4(€€€‘•É…‘”É…•™Õ±±äÍ¼Ñ¡”…±±•È…¸É•ÑÉä½È™¥à‘…Ñ„µ…¹Õ…±±ä¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€ÍÑ•ÁÌ€ô}…ÕÑ½}ÁÉ•Á…É•}‘É…™Ñ}µ•‘¥„¡‘É…™Ñ}¥¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ•ÁÌˆèÍÑ•ÁÌ°4(€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆè‘É…™Ñ}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°4(€€€€€€€€‰ÁÉ•Ù¥•ÜˆèÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ğ¤°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½‘¥Í½Ù•ÈµÁ¡½Ñ½Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘¥Í½Ù•É}Á¡½Ñ½Ì¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰¥Í½Ù•È½É¥¥¹…°Í¥Ñ”Á¡½Ñ½Ì™½È‘É…™Ğ…¹½µÁÕÑ”…ÉÉ¥Ù…°½‘•Á…ÉÑÕÉ”…¹‘¥‘…Ñ•Ì¸4(4(€€€A¡…Í”€Ğè=¹±ä•¹•É…Ñ•Ì…¹‘¥‘…Ñ•Ì¸½•Ì9=P½Ù•ÉİÉ¥Ñ”™½Éµ…°…ÉÉ¥Ù…°½‘•Á…ÉÑÕÉ”¸4(€€€A¡½Ñ½Ì½µ”™É½´Í•ÉÙ•È‘¥É•Ñ½Éäè€ñÉ½½Ğø¼ñM<µaaaaa`ø½Á¥ÑÕÉ•Ì½eeedµ54µ¼4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰½¹™¥Éµ•‰ôè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè˜‰É…™Ğƒ*Ûš’âèí‘É…™Ñ}É½İlÍÑ…ÑÕÌu÷¾ò3š^ƒšÎW–>G:ÃŸ&‰ô¤°€ĞÀÀ4(4(€€€€Œ•Ğ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸™½È½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ€¡½ÁÑ¥½¹…°¤4(€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô9½¹”4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤4(€€€€€€€¥˜‘…Ñ„…¹€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¥¸‘…Ñ„è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡‘…Ñ…l‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€Á…ÍÌ4(4(€€€€Œ%¹¥Ñ¥…±¥é”Á¡½Ñ¼Í•ÉÙ¥•Ì4(€€€Á¡½Ñ½}‘¥Í½Ù•Éä€ôA¡½Ñ½¥Í½Ù•ÉåM•ÉÙ¥” 4(€€€€€€€Í¡…É•‘}Á¡½Ñ½Í}É½½ĞõM!I}A!=Q=M}%H°4(€€€€€€€Í•ÉÙ¥•}½É‘•É}Á¡½Ñ½}™½±‘•É}™Õ¹ŒõÍ•ÉÙ¥•}½É‘•É}Á¡½Ñ½}™½±‘•È°4(€€€€¤4(€€€Á¡½Ñ½}µ•Ñ…‘…Ñ„€ôA¡½Ñ½5•Ñ…‘…Ñ…M•ÉÙ¥”¡Í¡…É•‘}Á¡½Ñ½Í}É½½ĞõM!I}A!=Q=M}%H¤4(4(€€€€Œ¥•±µİ½É¬µ…¹Õ…°Á¡½Ñ½}ÑåÁ”¥Ì…ÕÑ¡½É¥Ñ…Ñ¥Ù”İ¡•¸ÁÉ•Í•¹Ğ€¡•ÅÕ¥Áµ•¹Ğ€¼4(€€€€Œ…ÉÉ¥Ù…°€¼‘•Á…ÉÑÕÉ”€¼Í…™•Ñä¤¸%¹¡•É¥Ğ¥Ğ¥¹Ñ¼‘¥Í½Ù•É•Á¡½Ñ¼…¹‘¥‘…Ñ•Ì¸4(€€€ÑÉäè4(€€€€€€€}™Á}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•ĞÉ•±…Ñ¥Ù•}Á…Ñ °Á¡½Ñ½}ÑåÁ”™É½´™¥•±‘}Á¡½Ñ½Ìˆ¤¹™•Ñ¡…±° ¤4(€€€€€€€}™Á}µ…À€ôíÉl‰É•±…Ñ¥Ù•}Á…Ñ ‰tè€¡Él‰Á¡½Ñ½}ÑåÁ”‰t½È€ˆˆ¤™½ÈÈ¥¸}™Á}É½İÍô4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€}™Á}µ…À€ôíô4(4(€€€‘•˜}Á¡½Ñ½}ÑåÁ•}±½½­ÕÀ¡É•±…Ñ¥Ù•}Á…Ñ èÍÑÈ¤è4(€€€€€€€Ù…±Õ”€ô}™Á}µ…À¹•Ğ¡É•±…Ñ¥Ù•}Á…Ñ ¤4(€€€€€€€É•ÑÕÉ¸Ù…±Õ”½È9½¹”4(4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹‘¥Í½Ù•É}Á¡½Ñ½Í}™½É}‘É…™Ğ 4(€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€Á¡½Ñ½}‘¥Í½Ù•Éå}Í•ÉÙ¥”õÁ¡½Ñ½}‘¥Í½Ù•Éä°4(€€€€€€€€€€€Á¡½Ñ½}µ•Ñ…‘…Ñ…}Í•ÉÙ¥”õÁ¡½Ñ½}µ•Ñ…‘…Ñ„°4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€Á¡½Ñ½}ÑåÁ•}±½½­ÕÀõ}Á¡½Ñ½}ÑåÁ•}±½½­ÕÀ°4(€€€€€€€€¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(€€€¥˜É•ÍÕ±Ñl‰ÍÑ…ÑÕÌ‰t€ôô€‰™…¥±•ˆè4(€€€€€€€•ÉÉ½È€ôÉ•ÍÕ±Ğ¹•Ğ ‰•ÉÉ½Èˆ°€‹Ÿ&–>G:Ã–’Ç¢Ò”ˆ¤4(€€€€€€€¥˜€‰Ù•ÉÍ¥½¸ˆ¥¸•ÉÉ½Èè4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•ÉÉ½Éô¤°€ĞÀä4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•ÉÉ½Éô¤°€ÔÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€¨©É•ÍÕ±Ñô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½½¹™¥É´µÁ¡½Ñ¼µÑ¥µ•±¥¹”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}½¹™¥Éµ}Á¡½Ñ½}Ñ¥µ•±¥¹”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰½¹™¥É´Á¡½Ñ¼Ñ¥µ•±¥¹”…¹…ÁÁ±ä…¹‘¥‘…Ñ•ÌÑ¼™½Éµ…°…ÉÉ¥Ù…°½‘•Á…ÉÑÕÉ”¸4(4(€€€UÍ•È½¹™¥ÉµÌ€‰Á¡½Ñ¼Ñ¥µ•Ì…É”=,ˆ€´ø…ÁÁ±ä…¹‘¥‘…Ñ•Ì¸4(€€€%˜Ñ¥µ•±¥¹”¥ÌÍÕÍÁ¥¥½ÕÌ½¥¹ÍÕ™™¥¥•¹Ğ½Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•°É•ÅÕ¥É•Ì™½É”õÑÉÕ”¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€€Œ•Ğ™½É”™±…œ…¹•áÁ•Ñ•‘}Ù•ÉÍ¥½¸4(€€€™½É”€ô…±Í”4(€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô9½¹”4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤4(€€€€€€€¥˜‘…Ñ„è4(€€€€€€€€€€€™½É”€ô‰½½°¡‘…Ñ„¹•Ğ ‰™½É”ˆ°…±Í”¤¤4(€€€€€€€€€€€¥˜€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¥¸‘…Ñ„è4(€€€€€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡‘…Ñ…l‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€Á…ÍÌ4(4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹½¹™¥Éµ}Á¡½Ñ½}Ñ¥µ•±¥¹” 4(€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€™½É”õ™½É”°4(€€€€€€€€¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4(ŒƒŠRŠRŠR A¡…Í”€ÔèA¡½Ñ¼±…ÍÍ¥™¥…Ñ¥½¸A%ÌƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)‘•˜}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤è4(€€€€ˆˆ‰É•…Ñ”A¡½Ñ½±…ÍÍ¥™¥…Ñ¥½¹M•ÉÙ¥”İ¥Ñ Y¥Í¥½¸ÁÉ½Ù¥‘•È™É½´½¹™¥œ¸ˆˆˆ4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞ€ 4(€€€€€€€Y¥Í¥½¹±…ÍÍ¥™¥…Ñ¥½¹M•ÉÙ¥”°••ÁM••­Y¥Í¥½¹AÉ½Ù¥‘•È°A¡½Ñ½±…ÍÍ¥™¥…Ñ¥½¹M•ÉÙ¥”°4(€€€€¤4(€€€Í•ÑÑ¥¹Ì€ôÙ¥Í¥½¹}Í•ÑÑ¥¹Ì ¤4(€€€ÁÉ½Ù¥‘•È€ô••ÁM••­Y¥Í¥½¹AÉ½Ù¥‘•È 4(€€€€€€€…Á¥}­•äõÍ•ÑÑ¥¹Íl‰…Á¥}­•ä‰t°4(€€€€€€€µ½‘•°õÍ•ÑÑ¥¹Íl‰µ½‘•°‰t°4(€€€€¤4(€€€Ù¥Í¥½¸€ôY¥Í¥½¹±…ÍÍ¥™¥…Ñ¥½¹M•ÉÙ¥” 4(€€€€€€€ÁÉ½Ù¥‘•ÈõÁÉ½Ù¥‘•È°4(€€€€€€€•¹…‰±•õÍ•ÑÑ¥¹Íl‰•¹…‰±•‰t°4(€€€€€€€µ…á}¥µ…•}Í¥é”õÍ•ÑÑ¥¹Íl‰Ù¥Í¥½¹}µ…á}¥µ…•}Í¥é”‰t°4(€€€€€€€µ…á}¥µ…•}‰åÑ•ÌõÍ•ÑÑ¥¹Íl‰Ù¥Í¥½¹}µ…á}¥µ…•}‰åÑ•Ì‰t°4(€€€€¤4(€€€É•ÑÕÉ¸A¡½Ñ½±…ÍÍ¥™¥…Ñ¥½¹M•ÉÙ¥” 4(€€€€€€€Ù¥Í¥½¹}Í•ÉÙ¥”õÙ¥Í¥½¸°4(€€€€€€€Í¡…É•‘}Á¡½Ñ½Í}É½½ĞõM!I}A!=Q=M}%H°4(€€€€€€€‘‰}½¹¹•Ñ¥½¸õ‘ˆ ¤°4(€€€€€€€Í…™•Ñå}…ÕÑ½}Í•±•Ñ}½¹™¥‘•¹”õÍ•ÑÑ¥¹Íl‰Í…™•Ñå}…ÕÑ½}Í•±•Ñ}½¹™¥‘•¹”‰t°4(€€€€€€€Í…™•Ñå}Ù•É¥™å}½¹™¥‘•¹”õÍ•ÑÑ¥¹Íl‰Í…™•Ñå}Ù•É¥™å}½¹™¥‘•¹”‰t°4(€€€€€€€µ…á}Í•ÉÙ¥•}Á¡½Ñ½ÌõÍ•ÑÑ¥¹Íl‰µ…á}Í•ÉÙ¥•}Á¡½Ñ½Ì‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½±…ÍÍ¥™äµÁ¡½Ñ½Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}±…ÍÍ¥™å}Á¡½Ñ½Ì¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰±…ÍÍ¥™ä…±°Á¡½Ñ½Ì¥¸‘É…™ĞÕÍ¥¹œY¥Í¥½¸A$€¡A¡…Í”€Ô¤¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô9½¹”4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤4(€€€€€€€¥˜‘…Ñ„…¹€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¥¸‘…Ñ„è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡‘…Ñ…l‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€Á…ÍÌ4(4(€€€Í•ÑÑ¥¹Ì€ôÙ¥Í¥½¹}Í•ÑÑ¥¹Ì ¤4(€€€Á¡½Ñ½}ÍÙŒ€ô}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹±…ÍÍ¥™å}‘É…™Ñ}Á¡½Ñ½Ì 4(€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥”õÁ¡½Ñ½}ÍÙŒ°4(€€€€€€€€€€€…¹…±åÍ¥Í}µ½‘•°õÍ•ÑÑ¥¹Íl‰µ½‘•°‰t°4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸°4(€€€€€€€€¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½¡…¹”µÍ…™•ÑäµÁ¡½Ñ¼ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}¡…¹•}Í…™•Ñå}Á¡½Ñ¼¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÍ•Èµ…¹Õ…±±ä¡…¹•ÌÍ…™•ÑäÁ¡½Ñ¼€¡A¡…Í”€Ô¤¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€Á¡½Ñ½}¥€ô‘…Ñ„¹•Ğ ‰Á¡½Ñ½}¥ˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜¹½ĞÁ¡½Ñ½}¥è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂDÁ¡½Ñ½}¥‰ô¤°€ĞÀÀ4(4(€€€Á¡½Ñ½}ÍÙŒ€ô}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹¡…¹•}Í…™•Ñå}Á¡½Ñ¼¡‘É…™Ñ}¥°Á¡½Ñ½}¥°Á¡½Ñ½}ÍÙŒ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½…‘µÍ•ÉÙ¥”µÁ¡½Ñ¼ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}…‘‘}Í•ÉÙ¥•}Á¡½Ñ¼¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÍ•Èµ…¹Õ…±±ä…‘‘Ì„Í•ÉÙ¥”Á¡½Ñ¼€¡A¡…Í”€Ô¤¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€Á¡½Ñ½}¥€ô‘…Ñ„¹•Ğ ‰Á¡½Ñ½}¥ˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜¹½ĞÁ¡½Ñ½}¥è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂDÁ¡½Ñ½}¥‰ô¤°€ĞÀÀ4(4(€€€Á¡½Ñ½}ÍÙŒ€ô}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹…‘‘}Í•ÉÙ¥•}Á¡½Ñ¼¡‘É…™Ñ}¥°Á¡½Ñ½}¥°Á¡½Ñ½}ÍÙŒ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½É•µ½Ù”µÍ•ÉÙ¥”µÁ¡½Ñ¼ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}É•µ½Ù•}Í•ÉÙ¥•}Á¡½Ñ¼¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÍ•Èµ…¹Õ…±±äÉ•µ½Ù•Ì„Í•ÉÙ¥”Á¡½Ñ¼€¡A¡…Í”€Ô¤¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€Á¡½Ñ½}¥€ô‘…Ñ„¹•Ğ ‰Á¡½Ñ½}¥ˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜¹½ĞÁ¡½Ñ½}¥è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂDÁ¡½Ñ½}¥‰ô¤°€ĞÀÀ4(4(€€€Á¡½Ñ½}ÍÙŒ€ô}µ…­•}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¹}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹É•µ½Ù•}Í•ÉÙ¥•}Á¡½Ñ¼¡‘É…™Ñ}¥°Á¡½Ñ½}¥°Á¡½Ñ½}ÍÙŒ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½Á¡½Ñ½Ì½…ÕÑ¼µÍ•±•Ğˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}…ÕÑ½}Í•±•Ñ}Á¡½Ñ½Ì¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰ÕÑ¼µÍ•±•ĞÕÀÑ¼€ÄÀÍ•ÉÙ¥”€¡•ÅÕ¥Áµ•¹Ğ¤Á¡½Ñ½Ì™É½´…¹‘¥‘…Ñ•Ì¸4(4(€€€ƒš.7Ÿ–*¢ôèƒ¢«–*£¶o¦'šZ÷–Ş—Ÿ&¾ò#¢ºû–’Ÿ&¾ò'¾ò3’â7–"À€ÄÀƒ–òƒ–£¦'4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^¸‰ô¤°€ĞÀÌ4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ğ‰ô¤°€ĞÀÌ4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹…ÕÑ½}Í•±•Ñ}Í•ÉÙ¥•}Á¡½Ñ½Ì¡‘É…™Ñ}¥°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°€ĞÀÀ4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½Á¡½Ñ¼¼ñÍÑÉ¥¹œéÁ¡½Ñ½}¥ø½µ…É¬ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}µ…É­}Á¡½Ñ¼¡‘É…™Ñ}¥°Á¡½Ñ½}¥¤è4(€€€€ˆˆ‰5…É¬„¹½¸µ•ÅÕ¥Áµ•¹ĞÁ¡½Ñ¼…Ì…ÉÉ¥Ù…°€¼‘•Á…ÉÑÕÉ”€¼Í…™•Ñä¸4(4(€€€ƒš.7Ÿ–*¢ôèƒ¦v{¢ºû–’Ÿ&’â'¦'’âš‚¢ºÃ4(€€€ƒ¢şo–rè¿šï–rëŸ&jš^Û¦^Ğ€ôƒR£š"ß’ş»šRçš^Û¦^Ó’òc–#¾ò3š.7Ÿš^Û¦^Óš²‡’æ/4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^¸‰ô¤°€ĞÀÌ4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ğ‰ô¤°€ĞÀÌ4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€±…ÍÍ¥™¥…Ñ¥½¸€ô‘…Ñ„¹•Ğ ‰±…ÍÍ¥™¥…Ñ¥½¸ˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹µ…É­}Á¡½Ñ½}±…ÍÍ¥™¥…Ñ¥½¸ 4(€€€€€€€€€€€‘É…™Ñ}¥°Á¡½Ñ½}¥°±…ÍÍ¥™¥…Ñ¥½¸°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸4(€€€€€€€€¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€ÍÑ…ÑÕÌ€ô€ĞÈÈ¥˜É•ÍÕ±Ğ¹•Ğ ‰•ÉÉ½Èˆ¤¥¸€ ‰¥¹Ù…±¥‘}Á¡½Ñ½}¥ˆ°€‰¥¹Ù…±¥‘}±…ÍÍ¥™¥…Ñ¥½¸ˆ¤•±Í”€ĞÀÀ4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°ÍÑ…ÑÕÌ4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½Á¡½Ñ¼¼ñÍÑÉ¥¹œéÁ¡½Ñ½}¥ø½Ñ¥µ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÕÁ‘…Ñ•}Á¡½Ñ½}Ñ¥µ”¡‘É…™Ñ}¥°Á¡½Ñ½}¥¤è4(€€€€ˆˆ‰M•Ğ„ÕÍ•Èµµ½‘¥™¥•Ñ¥µ”½¸„Á¡½Ñ¼¸4(4(€€€ƒš.7Ÿ–*¢ôèƒR£š"ß’ş»šRçŸ&š^Û¦^Ó¾òo¢.—Ÿ&–ŞËš‚¢ºÃ’âë¢şo–rè¿šï–rë¾ò04(€€€ƒ–"g–"Ã¢úø¿šï–rëš^Û¦^Ó®/–6ÏR£’ş»šRç–B;jš^Û¦^Ó4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^¸‰ô¤°€ĞÀÌ4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ğ‰ô¤°€ĞÀÌ4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€Ñ¥µ•}ÍÑÈ€ô‘…Ñ„¹•Ğ ‰Ñ¥µ”ˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ğ€ôÍÙŒ¹ÕÁ‘…Ñ•}Á¡½Ñ½}Ñ¥µ” 4(€€€€€€€€€€€‘É…™Ñ}¥°Á¡½Ñ½}¥°Ñ¥µ•}ÍÑÈ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸4(€€€€€€€€¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(€€€¥˜¹½ĞÉ•ÍÕ±Ğ¹•Ğ ‰½¬ˆ¤è4(€€€€€€€ÍÑ…ÑÕÌ€ô€ĞÈÈ¥˜É•ÍÕ±Ğ¹•Ğ ‰•ÉÉ½Èˆ¤¥¸€ ‰¥¹Ù…±¥‘}Á¡½Ñ½}¥ˆ°€‰¥¹Ù…±¥‘}Ñ¥µ•}™½Éµ…Ğˆ°€‰Ñ¥µ•}µÕÍÑ}µ…Ñ¡}É•Á½ÉÑ}‘…Ñ”ˆ°€‰¥¹Ù…±¥‘}Ñ¥µ•}Ù…±Õ”ˆ¤•±Í”€ĞÀÀ4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤°ÍÑ…ÑÕÌ4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡É•ÍÕ±Ğ¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½½¹™¥É´ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}½¹™¥É´¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰½¹™¥É´‘É…™Ğ…¹…ÕÑ¼µ¡…¥¸¥¹Ñ¼Ñ¡”™½Éµ…°Í•ÉÙ¥”É•Á½ÉĞ¸4(4(€€€½¹™¥ÉµÌÑ¡”‘É…™Ğ€¡É•½É‘Ì…Õ‘¥Ğè½¹™¥Éµ•‘}‰ä°½¹™¥Éµ•‘}…Ğ°4(€€€Ù•É¥™¥…Ñ¥½¹}½Ù•ÉÉ¥‘”°½Ù•ÉÉ¥‘•}™¥•±‘Ì¤°Ñ¡•¸…ÕÑ½µ…Ñ¥…±±äÁÉ•Á…É•ÌÑ¡”4(€€€…ÑÑ…¡µ•¹Ğµ…¹¥™•ÍĞ°…ÕÑ¼µÉ•Ù¥•İÌµ¥±•…”µ•Ù¥‘•¹”½µÁ±¥…¹”€¡ÁÉ½‘ÕĞ4(€€€‘•¥Í¥½¸€ÈÀÈØ´Àä´ÄÜ¤°…¹ÉÕ¹ÌÑ¡”A¡…Í”€ä™½Éµ…°Í…Ù”Í¼Ñ¡”‘É…™Ğ±…¹‘Ì4(€€€¥¸Í•ÉÙ¥•}É•Á½ÉÑÌ¸%˜…¹ä…Ñ”‰±½­Ì°Ñ¡”‘É…™ĞÍÑ…åÌ€½¹™¥Éµ•œ…¹4(€€€Ñ¡”É•ÍÁ½¹Í”…ÉÉ¥•Ì…ÕÑ½}™½Éµ…±}Í…Ù”¹‰±½­•‘}½‘”½‰±½­•‘}µ•ÍÍ…”¸4(€€€I•ÅÕ¥É•ÌMIÑ½­•¸¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€€Œ9½Ñ”è½¹™¥É´½…¹•°½É•½Á•¸…É”A¡…Í”€ÄA%Ì…±±•‰ä$ÍÍ¥ÍÑ…¹Ğ°4(€€€€ŒÍ¼MI¥Ì¹½Ğ•¹™½É•¡•É”Ñ¼…Ù½¥‰É•…­¥¹œ•á¥ÍÑ¥¹œ¥¹Ñ•É…Ñ¥½¹Ì¸4(€€€€ŒA¡…Í”€Ø¹•ÜµÕÑ…Ñ¥½¸A%Ì€¡ÕÁ‘…Ñ”µİ½É­•È°•ÑŒ¸¤‘¼É•ÅÕ¥É”MI¸4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€€Œ%‘•µÁ½Ñ•¹ĞÉ”µ½¹™¥É´½˜…¸…±É•…‘ä™½Éµ…±±äÍ…Ù•‘É…™Ğ€¡$ÍÍ¥ÍÑ…¹Ğ4(€€€€ŒÉ•ÑÉ¥•Ì¤èÉ•ÑÕÉ¸Ñ¡”•á¥ÍÑ¥¹œ™½Éµ…°É•Á½ÉĞ¥¹ÍÑ•…½˜„€ĞÀÀ•ÉÉ½È¸4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰Í…Ù•ˆ…¹‘É…™Ñ}É½Ü¹•Ğ ‰Í…Ù•‘}É•Á½ÉÑ}¥ˆ¤è4(€€€€€€€É•Á½ÉÑ}¥€ô¥¹Ğ¡‘É…™Ñ}É½İl‰Í…Ù•‘}É•Á½ÉÑ}¥‰t¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Í…Ù•ˆ°4(€€€€€€€€€€€€‰µ•ÍÍ…”ˆè˜‹¢¾—š^—š*—–ŞË†»¢º“–æÛRš"C–Ş—–6Wš^—š*—¾ò!I•Á½ÉĞ€íÉ•Á½ÉÑ}¥‘÷¾ò$ˆ°4(€€€€€€€€€€€€‰…ÕÑ½}™½Éµ…±}Í…Ù”ˆèì4(€€€€€€€€€€€€€€€€‰…ÑÑ•µÁÑ•ˆèQÉÕ”°4(€€€€€€€€€€€€€€€€‰™½Éµ…±}Í…Ù•ˆèQÉÕ”°4(€€€€€€€€€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}¥ˆèÉ•Á½ÉÑ}¥°4(€€€€€€€€€€€€€€€€‰É•Á½ÉÑ}ÕÉ°ˆèÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤°4(€€€€€€€€€€€€€€€€‰‰±½­•‘}½‘”ˆè9½¹”°4(€€€€€€€€€€€€€€€€‰‰±½­•‘}µ•ÍÍ…”ˆè9½¹”°4(€€€€€€€€€€€ô°4(€€€€€€€ô¤4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰½¹™¥Éµ•‰ôè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè˜‰É…™Ğƒ*Ûš’âèí‘É…™Ñ}É½İlÍÑ…ÑÕÌu÷¾ò3š^ƒšÎW†»¢º‰ô¤°€ĞÀÀ4(4(€€€€Œ9½Ñ”è½¹™¥É´¥Ì„A¡…Í”€ÄA$…±±•‰ä$ÍÍ¥ÍÑ…¹Ğ°4(€€€€ŒÍ¼…ÕÑ¡½É¥é…Ñ¥½¸¥Ì¹½Ğ•¹™½É•¡•É”Ñ¼…Ù½¥‰É•…­¥¹œ•á¥ÍÑ¥¹œ¥¹Ñ•É…Ñ¥½¹Ì¸4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€€Œ½¹Ù•ÉĞÍÅ±¥Ñ”Ì¹I½ÜÑ¼‘¥Ğ€¡ÍÅ±¥Ñ”Ì¹I½Ü‘½•Ì9=PÍÕÁÁ½ÉĞ…ÑÑÉ¥‰ÕÑ”…•ÍÌ¤4(€€€¥˜¡…Í…ÑÑÈ¡ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€ÕÍ•È€ô‘¥Ğ¡ÕÍ•È¤4(€€€ÕÍ•É}¥€ôÕÍ•È¹•Ğ ‰¥ˆ°€ˆˆ¤4(4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(4(€€€€ŒØÀ¸Ä¸ÈĞÄèÍå¹ŒÙ•É¥™¥…Ñ¥½¸™¥•±‘Ìİ¥Ñ ÕÉÉ•¹ĞÍÑ…Ñ”	=IÑ¡”¡•¬¸4(€€€€ŒÙ•É¥™¥…Ñ¥½¹}™¥•±‘Ì…ÕµÕ±…Ñ•Ì½Ù•ÈÑ¡”‘É…™ĞÌ±¥™”ì½¹”„İ½É­•ÈÌ4(€€€€Œ½É¥¥¸½½Ù•É¹¥¡Ğ¥Ì½¹™¥Éµ•€¡µ…¹Õ…°•‘¥Ğ½È…ÕÑ¼™±½Ü¤°ÍÑ…±”•¹ÑÉ¥•Ì4(€€€€Œ±¥­”€‰¹Ñ½¹¥¼¹½É¥¥¸ˆ€¼€‰İ½É­•É|Ñ}½É¥¥¹}Õ¹½¹™¥Éµ•ˆÕÍ•Ñ¼­••ÀÑ¡”4(€€€€Œ½Ù•ÉÉ¥‘”‘¥…±½œÁ½ÁÁ¥¹œÕÀ™½É•Ù•È•Ù•¸Ñ¡½Õ ¹½Ñ¡¥¹œİ…Ì±•™ĞÑ¼½¹™¥É´¸4(€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ôÉ•½¹¥±•}ÑÉ…Ù•±}Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì 4(€€€€€€€mÜ¹µ½‘•±}‘ÕµÀ ¤™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÍt°4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì°4(€€€€¤4(€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ô‰½½°¡‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì¤4(4(€€€€Œ¡•¬Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•4(€€€¥˜‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•è4(€€€€€€€€Œ±±½Ü•áÁ±¥¥Ğ½Ù•ÉÉ¥‘”4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€€€€€½Ù•ÉÉ¥‘”€ô‘…Ñ„¹•Ğ ‰½Ù•ÉÉ¥‘•}Ù•É¥™¥…Ñ¥½¸ˆ°…±Í”¤4(€€€€€€€€€€€½Ù•ÉÉ¥‘•}™¥•±‘Ì€ô‘…Ñ„¹•Ğ ‰½Ù•ÉÉ¥‘•}™¥•±‘Ìˆ°mt¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€½Ù•ÉÉ¥‘”€ô…±Í”4(€€€€€€€€€€€½Ù•ÉÉ¥‘•}™¥•±‘Ì€ômt4(4(€€€€€€€¥˜¹½Ğ½Ù•ÉÉ¥‘”è4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè€‹–¶c–r£¦r¢š†»¢º“j–¶_šº×¾ò3¢¾ß–#¢†—–’ş‡š¿š"[šb;†»†»¢º“¢šnXˆ°4(€€€€€€€€€€€€€€€€‰µ¥ÍÍ¥¹}™¥•±‘Ìˆè‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì°4(€€€€€€€€€€€€€€€€‰½Ù•ÉÉ¥‘•}…Ù…¥±…‰±”ˆèQÉÕ”°4(€€€€€€€€€€€ô¤°€ĞÀÀ4(4(€€€€€€€€ŒI•½É½Ù•ÉÉ¥‘”4(€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}½Ù•ÉÉ¥‘”€ôQÉÕ”4(€€€€€€€‘É…™Ğ¹½Ù•ÉÉ¥‘•}™¥•±‘Ì€ô½Ù•ÉÉ¥‘•}™¥•±‘Ì½È‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì4(4(€€€€ŒI•½É…Õ‘¥Ğ4(€€€‘É…™Ğ¹½¹™¥Éµ•‘}‰ä€ôÕÍ•É}¥4(€€€‘É…™Ğ¹½¹™¥Éµ•‘}…Ğ€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•‘P• è•4è•Mhˆ¤4(€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ¤4(4(€€€€ŒA¡…Í”€Øèµ…É¬½¹™¥Éµ•°Ñ¡•¸…ÕÑ¼µ¡…¥¸¥¹Ñ¼Ñ¡”™½Éµ…°Í•ÉÙ¥”É•Á½ÉĞ¸4(€€€ÍÙŒ¹ÕÁ‘…Ñ•}‘É…™Ñ}ÍÑ…ÑÕÌ¡‘É…™Ñ}¥°€‰½¹™¥Éµ•ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(4(€€€€ŒÕÑ¼µ¡…¥¸€¡ØÀ¸Ä¸ÈÌÌ¤è½¹™¥É´€´øÁÉ•Á…É”µ…¹¥™•ÍĞ€´ø™½Éµ…°Í…Ù”¸4(€€€€Œ	•ÍĞ•™™½ÉĞè…¹ä…Ñ”Ñ¡…Ğ‰±½­Ì™½Éµ…°Í…Ù”€¡A¡…Í”€ÜII=H°µ…¹¥™•ÍĞ4(€€€€Œ¥¹Ñ•É¥Ñä°½µÁ±¥…¹”É•Ù¥•Ü°ÑÉ…Ù•°µµ½‘”µ…ÁÁ¥¹œ°½¹ÕÉÉ•¹Ğ½µµ¥Ğ¤4(€€€€Œ±•…Ù•ÌÑ¡”‘É…™Ğ¥¸€½¹™¥Éµ•œ…¹É•Á½ÉÑÌÑ¡”É•…Í½¸ìÑ¡”ÕÍ•È…¸4(€€€€ŒÑ¡•¸É•Í½±Ù”¥Ğ…¹™¥¹¥Í Ù¥„Ñ¡”•á¥ÍÑ¥¹œÁÉ•Á…É”€¼™½Éµ…°µÍ…Ù”U$¸4(€€€…ÕÑ¼€ôì4(€€€€€€€€‰…ÑÑ•µÁÑ•ˆèQÉÕ”°4(€€€€€€€€‰™½Éµ…±}Í…Ù•ˆè…±Í”°4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}¥ˆè9½¹”°4(€€€€€€€€‰É•Á½ÉÑ}ÕÉ°ˆè9½¹”°4(€€€€€€€€‰‰±½­•‘}½‘”ˆè9½¹”°4(€€€€€€€€‰‰±½­•‘}µ•ÍÍ…”ˆè9½¹”°4(€€€ô4(€€€ÑÉäè4(€€€€€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€Ù…±¥‘…Ñ¥½¸€ô}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤4(€€€€€€€¥˜¹½ĞÙ…±¥‘…Ñ¥½¸¹…¹}ÁÉ½••è4(€€€€€€€€€€€…ÕÑ½l‰‰±½­•‘}½‘”‰t€ô€‰Ù…±¥‘…Ñ¥½¹}…¹¹½Ñ}ÁÉ½••ˆ4(€€€€€€€€€€€…ÕÑ½l‰‰±½­•‘}µ•ÍÍ…”‰t€ô€‹–¶c–r£šr«¢–ÏjII=K¾ò3š^ƒšÎW¢«–*£Rš"Cš¶–ò?š^—š*—¾ò3¢¾ß–r£š‚‡¦ª3¦v‹švÿ–’B–B;š&/–*£š¶–ò?’şw–¶cˆ4(€€€€€€€•±Í”è4(€€€€€€€€€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€€€€€€€€€µ…¹¥™•ÍĞ€ôµ…¹¥™•ÍÑ}ÍÙŒ¹ÁÉ•Á…É”¡‘É…™Ñ}É½Ü°Ù…±¥‘…Ñ¥½¸¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€ŒÕÑ¼µ½µÁ±¥…¹”É•Ù¥•Ü€¡ÕÍ•È‘•¥Í¥½¸€ÈÀÈØ´Àä´ÄÜ¤èÑ¡”½¹™¥É´4(€€€€€€€€€€€€Œ…ÕÑ½µ…Ñ¥½¸ÑÉ•…ÑÌµ¥±•…”µ•Ù¥‘•¹”½µÁ±¥…¹”É•Ù¥•Ü…ÌÉ•Ù¥•İ•°4(€€€€€€€€€€€€Œµ¥ÉÉ½É¥¹œÑ¡”µ…¹Õ…°½µÁ±¥…¹”µÉ•Ù¥•Ü•¹‘Á½¥¹ĞÌ™¥•±İÉ¥Ñ•Ì¸4(€€€€€€€€€€€É•Ù¥•İ•‘}…Ğ€ô¹½Ü ¤4(€€€€€€€€€€€‘‰Œ€ô‘ˆ ¤4(€€€€€€€€€€€‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ”…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}Í½ÕÉ•Ì4(€€€€€€€€€€€€€€€Í•Ğ½µÁ±¥…¹•}ÍÑ…ÑÕÌ€ô€É•Ù¥•İ•œ°4(€€€€€€€€€€€€€€€€€€€½µÁ±¥…¹•}É•Ù¥•İ•‘}‰ä€ô€ü°4(€€€€€€€€€€€€€€€€€€€½µÁ±¥…¹•}É•Ù¥•İ•‘}…Ğ€ô€ü4(€€€€€€€€€€€€€€€İ¡•É”µ…¹¥™•ÍÑ}¥€ô€ü…¹½µÁ±¥…¹•}É•Ù¥•İ}É•ÅÕ¥É•€ô€Ä…¹½µÁ±¥…¹•}ÍÑ…ÑÕÌ€„ô€É•Ù¥•İ•œ4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€¡ÕÍ•É}¥°É•Ù¥•İ•‘}…Ğ°µ…¹¥™•ÍÑl‰µ…¹¥™•ÍÑ}¥‰t¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”…¥}‘…¥±å}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑÌÍ•ĞÕÁ‘…Ñ•‘}…Ğ€ô€üİ¡•É”µ…¹¥™•ÍÑ}¥€ô€üˆ°4(€€€€€€€€€€€€€€€€¡É•Ù¥•İ•‘}…Ğ°µ…¹¥™•ÍÑl‰µ…¹¥™•ÍÑ}¥‰t¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€™½Éµ…±}ÍÙŒ€ô½Éµ…±M…Ù•M•ÉÙ¥” 4(€€€€€€€€€€€€€€€‘ˆ ¤°Q}%H°IA=IQ}QQ!59QM}%H°ÕÍ•É}¥4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•ÍÕ±Ğ€ô™½Éµ…±}ÍÙŒ¹ÉÕ¸¡‘É…™Ñ}É½Ü°9½¹”°9½¹”°Ù…±¥‘…Ñ¥½¸°µ…¹¥™•ÍÑ}ÍÙŒ¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€…ÕÑ½l‰™½Éµ…±}Í…Ù•‰t€ôQÉÕ”4(€€€€€€€€€€€…ÕÑ½l‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t€ôÉ•ÍÕ±Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t4(€€€€€€€€€€€…ÕÑ½l‰É•Á½ÉÑ}ÕÉ°‰t€ôÕÉ±}™½È 4(€€€€€€€€€€€€€€€€‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•ÍÕ±Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t4(€€€€€€€€€€€€¤4(€€€•á•ÁĞ½Éµ…±M…Ù•ÉÉ½È…Ì•áŒè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€Á…ÍÌ4(€€€€€€€…ÕÑ½l‰‰±½­•‘}½‘”‰t€ô•áŒ¹½‘”4(€€€€€€€…ÕÑ½l‰‰±½­•‘}µ•ÍÍ…”‰t€ô•áŒ¹µ•ÍÍ…”4(€€€•á•ÁĞ5…¹¥™•ÍÑÉÉ½È…Ì•áŒè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€Á…ÍÌ4(€€€€€€€…ÕÑ½l‰‰±½­•‘}½‘”‰t€ô€‰µ…¹¥™•ÍÑ}•ÉÉ½Èˆ4(€€€€€€€…ÕÑ½l‰‰±½­•‘}µ•ÍÍ…”‰t€ôÍÑÈ¡•áŒ¤4(€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè€€ŒÁÉ…µ„è¹¼½Ù•È€´‘•™•¹Í¥Ù”™…±±‰…¬4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€Á…ÍÌ4(€€€€€€€…ÕÑ½l‰‰±½­•‘}½‘”‰t€ô€‰¥¹Ñ•É¹…±}•ÉÉ½Èˆ4(€€€€€€€…ÕÑ½l‰‰±½­•‘}µ•ÍÍ…”‰t€ô€‹¢«–*£Rš"Cš¶–ò?š^—š*—š^Û–>GR–¦£¦Rg¢¾¿¾ò3–>¿¢7–B;–r£†»¢º“*Ûš’â/š&/–*£š¶–ò?’şw–¶cˆ4(€€€€€€€…ÁÀ¹±½•È¹İ…É¹¥¹œ ‰$‘…¥±äÉ•Á½ÉĞ…ÕÑ¼™½Éµ…°µÍ…Ù”™…¥±•™½È‘É…™Ğ€•Ìè€•Ìˆ°‘É…™Ñ}¥°•áŒ¤4(4(€€€¥˜…ÕÑ½l‰™½Éµ…±}Í…Ù•‰tè4(€€€€€€€µ•ÍÍ…”€ô˜‹–ŞË†»¢º“–æÛ¢«–*£Rš"C–Ş—–6Wš^—š*—¾ò!I•Á½ÉĞ€í…ÕÑ½lÍ•ÉÙ¥•}É•Á½ÉÑ}¥u÷¾ò$ˆ4(€€€•±Í”è4(€€€€€€€µ•ÍÍ…”€ô€‰É…™Ğƒ–ŞË†»¢º“¾ò3’ö¢«–*£Rš"C–Ş—–6Wš^—š*—šr«–º3š"C¾òhˆ€¬€¡…ÕÑ½l‰‰±½­•‘}µ•ÍÍ…”‰t½È€‹šr«~—–:–n€ˆ¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Í…Ù•ˆ¥˜…ÕÑ½l‰™½Éµ…±}Í…Ù•‰t•±Í”€‰½¹™¥Éµ•ˆ°4(€€€€€€€€‰µ•ÍÍ…”ˆèµ•ÍÍ…”°4(€€€€€€€€‰½¹™¥Éµ•‘}‰äˆèÕÍ•É}¥°4(€€€€€€€€‰½¹™¥Éµ•‘}…Ğˆè‘É…™Ğ¹½¹™¥Éµ•‘}…Ğ°4(€€€€€€€€‰…ÕÑ½}™½Éµ…±}Í…Ù”ˆè…ÕÑ¼°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½…¹•°ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}…¹•°¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰M½™Ğµ…¹•°„‘É…™Ğ€¡ØÀ¸Ä¸ÈĞÄ°Á•ÈÕÍ•È‘•¥Í¥½¸€ÈÀÈØ´Àä´ÄÜ¤¸4(4(€€€MÑ…Ñ”µ…¡¥¹”è‘É…™Ğ½½¹™¥Éµ•€´ø…¹•±±•¸Q¡”‘É…™ĞÉ½Ü¥Ì-APİ¥Ñ 4(€€€¥ÑÌ…Õ‘¥ĞÑÉ…¥°€¡…¹•±±•‘}‰ä€¼…¹•±±•‘}…Ğ¤ƒŠPÑ¡¥Ì¥Ì€‹–>[šÚ ˆ°¹½Ğ„4(€€€Á¡åÍ¥…°‘•±•Ñ”¸UÍ”€½‘•±•Ñ”™½È„¡…É‘•±•Ñ”¸4(€€€I•ÅÕ¥É•ÌMIÑ½­•¸¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰Í…Ù•ˆè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–ŞËš¶–ò?’şw–¶cjš^—š*—’â7¢÷–>[šÚ ‰ô¤°€ĞÀä4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰…¹•±±•ˆè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰…¹•±±•ˆ°4(€€€€€€€€€€€€‰µ•ÍÍ…”ˆè€‹¢¾”É…™Ğƒ–ŞËšb¿–>[šÚ#*Ûšˆ°4(€€€€€€€ô¤4(4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€€Œ½¹Ù•ÉĞÍÅ±¥Ñ”Ì¹I½ÜÑ¼‘¥Ğ€¡ÍÅ±¥Ñ”Ì¹I½Ü‘½•Ì9=PÍÕÁÁ½ÉĞ…ÑÑÉ¥‰ÕÑ”…•ÍÌ¤4(€€€¥˜¡…Í…ÑÑÈ¡ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€ÕÍ•È€ô‘¥Ğ¡ÕÍ•È¤4(€€€ÕÍ•É}¥€ôÕÍ•È¹•Ğ ‰¥ˆ°€ˆˆ¤4(4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€‘É…™Ğ¹…¹•±±•‘}‰ä€ôÕÍ•É}¥4(€€€‘É…™Ğ¹…¹•±±•‘}…Ğ€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•‘P• è•4è•Mhˆ¤4(€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ¤4(€€€ÍÙŒ¹ÕÁ‘…Ñ•}‘É…™Ñ}ÍÑ…ÑÕÌ¡‘É…™Ñ}¥°€‰…¹•±±•ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰…¹•±±•ˆ°4(€€€€€€€€‰…¹•±±•‘}‰äˆèÕÍ•É}¥°4(€€€€€€€€‰…¹•±±•‘}…Ğˆè‘É…™Ğ¹…¹•±±•‘}…Ğ°4(€€€€€€€€‰µ•ÍÍ…”ˆè€‰É…™Ğƒ–ŞË–>[šÚ#¾ò#’şwVg¢ºÃ–öW¾ò3–>¿–r£–"_¢†£’â·¶o¦$…¹•±±•ƒš~—r/¾ò$ˆ°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘•±•Ñ”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰A¡åÍ¥…±±ä‘•±•Ñ”„‘É…™Ğ€¡¡…É‘•±•Ñ”°¹½Ğ„Í½™Ğ…¹•°¤¸4(4(€€€ØÀ¸Ä¸ÈĞÄè€½…¹•°İ…ÌÑÕÉ¹•‰…¬¥¹Ñ¼„Í½™Ğ…¹•°ìÑ¡¥Ì•¹‘Á½¥¹Ğ¹½Ü4(€€€½İ¹ÌÑ¡”¡…É‘•±•Ñ”¸I•µ½Ù•ÌÑ¡”‘É…™ĞÁ±ÕÌ…Í…‘•…Ñ¥½¹Ì€¼µ…¹¥™•ÍÑÌ4(€€€€¡İ¥Ñ Ñ¡•¥ÈÍ½ÕÉ•Ì°…ÍÍ•ÑÌ°É½±•Ì¤…¹™½Éµ…°½µµ¥ÑÌ¸±•…¹Ìµ…¹¥™•ÍĞ4(€€€ÍÑ…¥¹œ‘¥É•Ñ½É¥•Ì™¥ÉÍĞ¸M…Ù•€¡™½Éµ…±±ä½µµ¥ÑÑ•¤‘É…™ÑÌ…¹¹½Ğ‰”4(€€€‘•±•Ñ•¸I•ÅÕ¥É•ÌMIÑ½­•¸¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜‘É…™Ñ}É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰Í…Ù•ˆè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–ŞËš¶–ò?’şw–¶cjš^—š*—’â7¢÷–"ƒ¦f‰ô¤°€ĞÀä4(4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€€Œ½¹Ù•ÉĞÍÅ±¥Ñ”Ì¹I½ÜÑ¼‘¥Ğ€¡ÍÅ±¥Ñ”Ì¹I½Ü‘½•Ì9=PÍÕÁÁ½ÉĞ…ÑÑÉ¥‰ÕÑ”…•ÍÌ¤4(€€€¥˜¡…Í…ÑÑÈ¡ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€ÕÍ•È€ô‘¥Ğ¡ÕÍ•È¤4(€€€ÕÍ•É}¥€ôÕÍ•È¹•Ğ ‰¥ˆ°€ˆˆ¤4(4(€€€€Œ±•…¸µ…¹¥™•ÍĞÍÑ…¥¹œ‘¥É•Ñ½É¥•Ì‰•™½É”Ñ¡”É½İÌ‘¥Í…ÁÁ•…È¸4(€€€µ…¹¥™•ÍÑ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğµ…¹¥™•ÍÑ}¥™É½´…¥}‘…¥±å}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑÌİ¡•É”‘É…™Ñ}¥€ô€üˆ°4(€€€€€€€€¡‘É…™Ñ}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜µ…¹¥™•ÍÑ}É½İÌè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€µÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€€€€€€€€€™½ÈÉ½Ü¥¸µ…¹¥™•ÍÑ}É½İÌè4(€€€€€€€€€€€€€€€µÍÙŒ¹}‘•±•Ñ•}ÍÑ…¥¹}‘¥È¡‘É…™Ñ}¥°É½İlÁt¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€Á…ÍÌ4(4(€€€ÍÙŒ¹‘•±•Ñ•}‘É…™Ğ¡‘É…™Ñ}¥¤€€ŒÁ¡åÍ¥…°‘•±•Ñ”€¡…Í…‘•Ì¡¥±É½İÌ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰‘•±•Ñ•ˆ°4(€€€€€€€€‰‘•±•Ñ•‘}‰äˆèÕÍ•É}¥°4(€€€€€€€€‰‘•±•Ñ•‘}…Ğˆè‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•‘P• è•4è•Mhˆ¤°4(€€€€€€€€‰µ•ÍÍ…”ˆè€‰É…™Ğƒ–ŞË–öï–êW–"ƒ¦fˆ°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½É•½Á•¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}É•½Á•¸¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰áÁ±¥¥Ñ±äÉ•½Á•¸„½¹™¥Éµ•‘É…™Ğ™½È•‘¥Ñ¥¹œ¸4(4(€€€MÑ…Ñ”µ…¡¥¹”è½¹™¥Éµ•€´ø‘É…™Ğ¸4(€€€I•½É‘ÌÉ•½Á•¹•‘}‰ä°É•½Á•¹•‘}…Ğ¸4(€€€AÉ•Í•ÉÙ•Ì…±°$½Á¡½Ñ¼½µ¥±•…”µ•Ñ…‘…Ñ„¸4(€€€I•ÅÕ¥É•ÌMIÑ½­•¸¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€€Œ9½Ñ”èÉ•½Á•¸¥Ì„A¡…Í”€ÄA$…±±•‰ä$ÍÍ¥ÍÑ…¹Ğ°4(€€€€ŒÍ¼…ÕÑ¡½É¥é…Ñ¥½¸¥Ì¹½Ğ•¹™½É•¡•É”Ñ¼…Ù½¥‰É•…­¥¹œ•á¥ÍÑ¥¹œ¥¹Ñ•É…Ñ¥½¹Ì¸4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€€Œ½¹Ù•ÉĞÍÅ±¥Ñ”Ì¹I½ÜÑ¼‘¥Ğ€¡ÍÅ±¥Ñ”Ì¹I½Ü‘½•Ì9=PÍÕÁÁ½ÉĞ…ÑÑÉ¥‰ÕÑ”…•ÍÌ¤4(€€€¥˜¡…Í…ÑÑÈ¡ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€ÕÍ•È€ô‘¥Ğ¡ÕÍ•È¤4(€€€ÕÍ•É}¥€ôÕÍ•È¹•Ğ ‰¥ˆ°€ˆˆ¤4(4(€€€ÑÉäè4(€€€€€€€ÍÙŒ¹É•½Á•¹}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€•á•ÁĞÉ…™ÑMÑ…Ñ•ÉÉ½È…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¥ô¤°€ĞÀä4(4(€€€€ŒI•½É…Õ‘¥Ğ€¡ÁÉ•Í•ÉÙ•Ì…±°½Ñ¡•Èµ•Ñ…‘…Ñ„¤4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡ÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤¤4(€€€‘É…™Ğ¹É•½Á•¹•‘}‰ä€ôÕÍ•É}¥4(€€€‘É…™Ğ¹É•½Á•¹•‘}…Ğ€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•‘P• è•4è•Mhˆ¤4(€€€€Œ±•…È½¹™¥Éµ•…Õ‘¥Ğ4(€€€‘É…™Ğ¹½¹™¥Éµ•‘}‰ä€ô9½¹”4(€€€‘É…™Ğ¹½¹™¥Éµ•‘}…Ğ€ô9½¹”4(€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}½Ù•ÉÉ¥‘”€ô…±Í”4(€€€‘É…™Ğ¹½Ù•ÉÉ¥‘•}™¥•±‘Ì€ômt4(€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ¤4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰‘É…™Ğˆ°4(€€€€€€€€‰É•½Á•¹•‘}‰äˆèÕÍ•É}¥°4(€€€€€€€€‰É•½Á•¹•‘}…Ğˆè‘É…™Ğ¹É•½Á•¹•‘}…Ğ°4(€€€€€€€€‰µ•ÍÍ…”ˆè€‰É…™Ğƒ–ŞË¦7šZÃš&O–ò¾ò3–>¿’î—îŸî·’ş»šRç¾ò!$¿Ÿ&¿¦3¢/šVÃš6»–ŞË’şwVg¾ò$ˆ°4(€€€ô¤4(4(4(ŒƒŠRŠRŠR A¡…Í”€Øè$…¥±äI•Á½ÉĞI•Ù¥•Ü•¹Ñ•ÈA%ÌƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½ÍÉ˜ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤è4(€€€€ˆˆ‰•ĞMIÑ½­•¸™½ÈI•Ù¥•Ü•¹Ñ•ÈµÕÑ…Ñ¥½¸A%Ì¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰ÍÉ™}Ñ½­•¸ˆè…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ™}Ñ½­•¸ ¥ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½Í•ÉÙ¥”µ½É‘•ÉÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥•}½É‘•ÉÍ}½ÁÑ¥½¹Ì ¤è4(€€€€ˆˆ‰)M=8½ÁÑ¥½¹Ì™½ÈÑ¡”€9•Ü$…¥±äI•Á½ÉĞœ½É‘•ÈÁ¥­•È¸4(4(€€€%¹Ñ•É¹…°ÕÍ•ÉÌ½¹±ä¸ÁÁ±¥•ÌÑ¡”Í…µ”±¥•¹ĞµÍ½Á”™¥±Ñ•ÉÌ…ÌÑ¡”É•ÍĞ½˜4(€€€Ñ¡”…ÁÀ€¡Í•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ¤°Í¼Ñ¡”Á¥­•È¹•Ù•È±•…­Ì½É‘•ÉÌ4(€€€Ñ¡”ÕÍ•È½Õ±¹½Ğ½Ñ¡•Éİ¥Í”Í•”¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÑÉäè4(€€€€€€€±¥µ¥Ğ€ôµ¥¸¡¥¹Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰±¥µ¥Ğˆ°€ˆÈÀˆ¤½È€ÈÀ¤°€ÔÀ¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€±¥µ¥Ğ€ô€ÈÀ4(€€€±…ÕÍ•Ì°Á…É…µÌ€ôÍ•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤4(€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ€„ô€±½Í•œˆ¤4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆ¡Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü€ˆ4(€€€€€€€€€€€€‰½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È±¥­”€ü½È½…±•Í”¡Í•ÉÙ¥•}½É‘•ÉÌ¹Í¥Ñ•}…‘‘É•ÍÌ°€œœ¤±¥­”€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€±¥­”€ô˜ˆ•íÅô”ˆ4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m±¥­”°±¥­”°±¥­”°±¥­•t¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜‰Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¹¥°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°€ˆ4(€€€€€€€˜‰Í•ÉÙ¥•}½É‘•ÉÌ¹Í¥Ñ•}…‘‘É•ÍÌ€ˆ4(€€€€€€€˜‰™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”ìœ…¹€œ¹©½¥¸¡±…ÕÍ•Ì¥ô€ˆ4(€€€€€€€˜‰½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥µ¥Ğ€üˆ°4(€€€€€€€Á…É…µÌ€¬m±¥µ¥Ñt°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰½É‘•ÉÌˆèm‘¥Ğ¡È¤™½ÈÈ¥¸É½İÍuô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}É•…Ñ•}‘É…™Ğ ¤è4(€€€€ˆˆ‰É•…Ñ”€¡½ÈÉ•ÕÍ”¤…¸$…¥±äI•Á½ÉĞÉ…™Ğ™½È…¸½É‘•È€¬‘…Ñ”¸4(4(€€€1¥¡Ñİ•¥¡ĞÉ•…Ñ¥½¸•¹ÑÉäİ¥Ñ¡½ÕĞÑ¡”$¡…ĞèÁ¥¬…¸½É‘•È…¹‘…Ñ”¥¸4(€€€Ñ¡”I•Ù¥•Ü•¹Ñ•È°•Ğ„É…™Ğ°Ñ¡•¸½¹Ñ¥¹Õ”Ñ¡”•á¥ÍÑ¥¹œA¡…Í”€Ğ´ä4(€€€İ½É­™±½Ü€¡‘¥Í½Ù•ÈÁ¡½Ñ½Ì°±…ÍÍ¥™ä°µ¥±•…”°Ù…±¥‘…Ñ¥½¸°½¹™¥É´°4(€€€…ÑÑ…¡µ•¹ÑÌ°™½Éµ…°Í…Ù”¤¸%˜…¸…Ñ¥Ù”‘É…™Ğ…±É•…‘ä•á¥ÍÑÌ™½ÈÑ¡”Í…µ”4(€€€½É‘•È€¬‘…Ñ”¥Ğ¥ÌÉ•ÑÕÉ¹•¥¹ÍÑ•…€¡Í…µ”É•ÕÍ”Í•µ…¹Ñ¥Ì…Ì€½¡…Ğ¤¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€‰½‘ä€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€Í•ÉÙ¥•}½É‘•É}¥€ô‰½‘ä¹•Ğ ‰Í•ÉÙ¥•}½É‘•É}¥ˆ¤4(€€€É•Á½ÉÑ}‘…Ñ”€ôÍÑÈ¡‰½‘ä¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤½È}…¥}‘…¥±å}É•Á½ÉÑ}‰ÕÍ¥¹•ÍÍ}‘…Ñ” ¤4(€€€ÑÉäè4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¥€ô¥¹Ğ¡Í•ÉÙ¥•}½É‘•É}¥¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂGšr'šV#jÍ•ÉÙ¥•}½É‘•É}¥‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡Í•ÉÙ¥•}½É‘•É}¥¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–Ş—–6W’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€•á¥ÍÑ¥¹œ€ôÍÙŒ¹•Ñ}…Ñ¥Ù•}‘É…™Ğ¡Í•ÉÙ¥•}½É‘•É}¥°É•Á½ÉÑ}‘…Ñ”¤4(€€€¥˜•á¥ÍÑ¥¹œè4(€€€€€€€ÁÉ•Ù¥•Ü€ôÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡ÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡•á¥ÍÑ¥¹œ¤¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}¥ˆè•á¥ÍÑ¥¹l‰¥‰t°€‰É•ÕÍ•ˆèQÉÕ”°€‰ÁÉ•Ù¥•ÜˆèÁÉ•Ù¥•İô¤4(4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹É•…Ñ•}‘É…™Ğ 4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õÍ•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€É•Á½ÉÑ}‘…Ñ”õÉ•Á½ÉÑ}‘…Ñ”°4(€€€€€€€Í¥Ñ•}…‘‘É•ÍÌõ½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t°4(€€€€€€€…¥}µ½‘•°õ‘••ÁÍ••­}…ÍÍ¥ÍÑ…¹Ñ}Í•ÑÑ¥¹Ì ¥l‰µ½‘•°‰t°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}¥ˆè‘É…™Ñ}É½İl‰¥‰t°€‰É•ÕÍ•ˆè…±Í•ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™ÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™ÑÍ}±¥ÍĞ ¤è4(€€€€ˆˆ‰1¥ÍĞ$…¥±äI•Á½ÉĞÉ…™ÑÌ°™¥±Ñ•É•‰äÕÍ•ÈÁ•Éµ¥ÍÍ¥½¸¸4(4(€€€EÕ•ÉäÁ…É…µÌèÍÑ…ÑÕÌ°‘…Ñ•}™É½´°‘…Ñ•}Ñ¼°Í•ÉÙ¥•}½É‘•É}¥°Á…”°Á•É}Á…”¸4(€€€1¥ÍĞ¥Ì™¥±Ñ•É•…ĞME0±•Ù•°‰äÕÍ•ÈÁ•Éµ¥ÍÍ¥½¸€¡¹½ĞÅÕ•Éäµ…±°µÑ¡•¸µ¡¥‘”¤¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€±…ÕÍ•Ì°Á…É…µÌ€ô…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}±¥ÍÑ}™¥±Ñ•ÉÌ¡ÕÍ•È¤4(4(€€€€Œ¥±Ñ•ÉÌ4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÍÑ…ÑÕÌˆ¤4(€€€¥˜ÍÑ…ÑÕÌ…¹ÍÑ…ÑÕÌ¥¸€ ‰‘É…™Ğˆ°€‰½¹™¥Éµ•ˆ°€‰…¹•±±•ˆ°€‰Í…Ù•ˆ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÍÑ…ÑÕÌ¤4(4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ¤4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰É•Á½ÉÑ}‘…Ñ”€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰É•Á½ÉÑ}‘…Ñ”€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(4(€€€Í•ÉÙ¥•}½É‘•É}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Í•ÉÙ¥•}½É‘•É}¥ˆ°ÑåÁ”õ¥¹Ğ¤4(€€€¥˜Í•ÉÙ¥•}½É‘•É}¥è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•É}¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡Í•ÉÙ¥•}½É‘•É}¥¤4(4(€€€€ŒA…¥¹…Ñ¥½¸4(€€€Á…”€ôµ…à Ä°É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…”ˆ°€Ä°ÑåÁ”õ¥¹Ğ¤¤4(€€€Á•É}Á…”€ôµ¥¸ ÄÀÀ°µ…à Ä°É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á•É}Á…”ˆ°€ÈÀ°ÑåÁ”õ¥¹Ğ¤¤¤4(€€€½™™Í•Ğ€ô€¡Á…”€´€Ä¤€¨Á•É}Á…”4(4(€€€İ¡•É”€ô€ˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¤¥˜±…ÕÍ•Ì•±Í”€ˆÄôÄˆ4(4(€€€€Œ½Õ¹Ğ4(€€€½Õ¹Ñ}É½Ü€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜‰Í•±•Ğ½Õ¹Ğ ¨¤…Ì¹Ğ™É½´…¥}‘…¥±å}É•Á½ÉÑ}‘É…™ÑÌİ¡•É”íİ¡•É•ôˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€Ñ½Ñ…°€ô½Õ¹Ñ}É½İl‰¹Ğ‰t¥˜½Õ¹Ñ}É½Ü•±Í”€À4(4(€€€€ŒEÕ•Éä€¡‘É…™Ğ™¥ÉÍĞ°Ñ¡•¸ÕÁ‘…Ñ•‘}…Ğ‘•ÍŒ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆ‰Í•±•Ğ¥°Í•ÉÙ¥•}½É‘•É}¥°É•Á½ÉÑ}‘…Ñ”°ÍÑ…ÑÕÌ°‘É…™Ñ}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€€€€€€€€É•…Ñ•‘}‰ä°É•…Ñ•‘}…Ğ°ÕÁ‘…Ñ•‘}…Ğ°‘É…™Ñ}‘…Ñ„4(€€€€€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉÑ}‘É…™ÑÌ4(€€€€€€€€€€€İ¡•É”íİ¡•É•ô4(€€€€€€€€€€€½É‘•È‰ä4(€€€€€€€€€€€€€€€…Í”İ¡•¸ÍÑ…ÑÕÌ€ô€‘É…™ĞœÑ¡•¸€À•±Í”€Ä•¹°4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ•‘}…Ğ‘•ÍŒ4(€€€€€€€€€€€±¥µ¥Ğ€ü½™™Í•Ğ€üˆˆˆ°4(€€€€€€€Á…É…µÌ€¬mÁ•É}Á…”°½™™Í•Ñt°4(€€€€¤¹™•Ñ¡…±° ¤4(4(€€€€Œ¹É¥ İ¥Ñ Í•ÉÙ¥”½É‘•È¥¹™¼…¹İ½É­•È½Õ¹Ğ4(€€€‘É…™ÑÌ€ômt4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€‘É…™Ñ}‘…Ñ„€ôíô4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘É…™Ñ}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤¥˜€‰‘É…™Ñ}‘…Ñ„ˆ¥¸É½Ü¹­•åÌ ¤•±Í”íô4(€€€€€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€€€€€Á…ÍÌ4(4(€€€€€€€Í•ÉÙ¥•}½É‘•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ½É‘•É}¹Õµ‰•È°±¥•¹Ñ}¹…µ”°Í¥Ñ•}…‘‘É•ÍÌ™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É½İl‰Í•ÉÙ¥•}½É‘•É}¥‰t°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(4(€€€€€€€İ½É­•É}½Õ¹Ğ€ô±•¸¡‘É…™Ñ}‘…Ñ„¹•Ğ ‰İ½É­•ÉÌˆ°mt¤¤4(€€€€€€€Á¡½Ñ½}½Õ¹Ğ€ô±•¸¡‘É…™Ñ}‘…Ñ„¹•Ğ ‰Á¡½Ñ½}…¹‘¥‘…Ñ•Ìˆ°mt¤¤4(€€€€€€€€ŒØÀ¸Ä¸ÈĞÄèƒ–"_¢†£–ú÷š‚–B3š¶—šâB–ŞË†»¢º“j–ë¢†3–¶_šº×’â8İ½É­}¥Ñ•µÌ¸¨ƒ–¶c¦?š‚¢ºÀ4(€€€€€€€Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ôÉ•½¹¥±•}ÑÉ…Ù•±}Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì 4(€€€€€€€€€€€‘É…™Ñ}‘…Ñ„¹•Ğ ‰İ½É­•ÉÌˆ°mt¤°4(€€€€€€€€€€€‘É…™Ñ}‘…Ñ„¹•Ğ ‰Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ìˆ¤½Èmt°4(€€€€€€€€¤4(€€€€€€€Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ôl4(€€€€€€€€€€€˜™½È˜¥¸Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì4(€€€€€€€€€€€¥˜¹½ĞÍÑÈ¡˜¤¹±½İ•È ¤¹ÍÑ…ÉÑÍİ¥Ñ  ‰İ½É­}¥Ñ•µÌ¸ˆ¤4(€€€€€€€t4(4(€€€€€€€‘É…™ÑÌ¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€‰¥ˆèÉ½İl‰¥‰t°4(€€€€€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}¥ˆèÉ½İl‰Í•ÉÙ¥•}½É‘•É}¥‰t°4(€€€€€€€€€€€€‰½É‘•É}¹Õµ‰•ÈˆèÍ•ÉÙ¥•}½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¥˜Í•ÉÙ¥•}½É‘•È•±Í”9½¹”°4(€€€€€€€€€€€€‰±¥•¹Ñ}¹…µ”ˆèÍ•ÉÙ¥•}½É‘•Él‰±¥•¹Ñ}¹…µ”‰t¥˜Í•ÉÙ¥•}½É‘•È•±Í”9½¹”°4(€€€€€€€€€€€€‰É•Á½ÉÑ}‘…Ñ”ˆèÉ½İl‰É•Á½ÉÑ}‘…Ñ”‰t°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆèÉ½İl‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÉ½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°4(€€€€€€€€€€€€‰İ½É­•É}½Õ¹Ğˆèİ½É­•É}½Õ¹Ğ°4(€€€€€€€€€€€€‰Á¡½Ñ½}½Õ¹ĞˆèÁ¡½Ñ½}½Õ¹Ğ°4(€€€€€€€€€€€€‰¡…Í}Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆè±•¸¡Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì¤€ø€À°4(€€€€€€€€€€€€‰Ù•É¥™¥…Ñ¥½¹}™¥•±‘ÌˆèÙ•É¥™¥…Ñ¥½¹}™¥•±‘Ì°4(€€€€€€€€€€€€‰É•…Ñ•‘}‰äˆèÉ½İl‰É•…Ñ•‘}‰ä‰t°4(€€€€€€€€€€€€‰É•…Ñ•‘}…ĞˆèÉ½İl‰É•…Ñ•‘}…Ğ‰t°4(€€€€€€€€€€€€‰ÕÁ‘…Ñ•‘}…ĞˆèÉ½İl‰ÕÁ‘…Ñ•‘}…Ğ‰t°4(€€€€€€€ô¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™ÑÌˆè‘É…™ÑÌ°4(€€€€€€€€‰Ñ½Ñ…°ˆèÑ½Ñ…°°4(€€€€€€€€‰Á…”ˆèÁ…”°4(€€€€€€€€‰Á•É}Á…”ˆèÁ•É}Á…”°4(€€€€€€€€‰Ñ½Ñ…±}Á…•Ìˆè€¡Ñ½Ñ…°€¬Á•É}Á…”€´€Ä¤€¼¼Á•É}Á…”¥˜Ñ½Ñ…°€ø€À•±Í”€À°4(€€€ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}ÁÉ•Ù¥•Ü¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰•ĞAÉ•Ù¥•ÜÉ•…Ñ¥½¸™½È„É…™Ğ€¡É•…µ½¹±ä¤¸4(4(€€€½•Ì9=P…±°½½±”I½ÕÑ•Ì°Í…¸Á¡½Ñ½Ì°½È…±°Y¥Í¥½¸¸4(€€€=¹±äÉ•…‘ÌÍ…Ù•É…™Ğ‘…Ñ„…¹…É•…Ñ•Ì™½È‘¥ÍÁ±…ä¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€€ŒÕÑ¡½É¥é…Ñ¥½¸¡•¬4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^»š¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞAÉ•Ù¥•İÉ•…Ñ¥½¹M•ÉÙ¥”4(€€€ÁÉ•Ù¥•İ}ÍÙŒ€ôAÉ•Ù¥•İÉ•…Ñ¥½¹M•ÉÙ¥”¡‘ˆ ¤°M!I}A!=Q=M}%H¤4(€€€ÁÉ•Ù¥•Ü€ôÁÉ•Ù¥•İ}ÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ñ}É½Ü¤4(4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞAÉ•Ù¥•İÉ•…Ñ¥½¹M•ÉÙ¥”4(€€€ÁÉ•Ù¥•İ}ÍÙŒ€ôAÉ•Ù¥•İÉ•…Ñ¥½¹M•ÉÙ¥”¡‘ˆ ¤°M!I}A!=Q=M}%H¤4(€€€ÁÉ•Ù¥•Ü€ôÁÉ•Ù¥•İ}ÍÙŒ¹‰Õ¥±‘}ÁÉ•Ù¥•Ü¡‘É…™Ñ}É½Ü¤4(4(€€€€ŒA¡…Í”€àèÑÑ…¡µ•¹ĞAÉ•Á…É…Ñ¥½¸ÍÕµµ…Éä€¡Iµ=91dì¹•Ù•Èµ…Ñ•É¥…±¥é•Ì¤4(€€€Ù…±¥‘…Ñ¥½¸€ô}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤4(€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€ÁÉ•Ù¥•İl‰…ÑÑ…¡µ•¹Ñ}ÁÉ•Á…É…Ñ¥½¸‰t€ôµ…¹¥™•ÍÑ}ÍÙŒ¹ÍÕµµ…Éå}™½É}ÁÉ•Ù¥•Ü¡‘É…™Ñ}É½Ü°Ù…±¥‘…Ñ¥½¸¤4(4(€€€€ŒA¡…Í”€äè½Éµ…°M…Ù”ÍÑ…ÑÕÌ€¡Iµ=91d¤4(€€€ÁÉ•Ù¥•İl‰™½Éµ…±}Í…Ù”‰t€ô}™½Éµ…±}Í…Ù•}ÁÉ•Ù¥•İ}ÍÕµµ…Éä¡‘É…™Ñ}É½Ü¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰ÁÉ•Ù¥•ÜˆèÁÉ•Ù¥•İô¤4(4(4)‘•˜}™½Éµ…±}Í…Ù•}ÁÉ•Ù¥•İ}ÍÕµµ…Éä¡‘É…™Ñ}É½Ü¤è4(€€€€ˆˆ‰I•…µ½¹±äA¡…Í”€äÍÑ…ÑÕÌ…É•…Ñ¥½¸™½ÈÑ¡”I•Ù¥•Ü•¹Ñ•ÈÁÉ•Ù¥•Ü¸ˆˆˆ4(€€€‘É…™Ñ}¥€ô¥¹Ğ¡‘É…™Ñ}É½İl‰¥‰t¤4(€€€½µµ¥Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•ĞÍÑ…ÑÕÌ°Í•ÉÙ¥•}É•Á½ÉÑ}¥°‘É…™Ñ}Ù•ÉÍ¥½¸°½µµ¥ÑÑ•‘}…Ğ°™…¥±ÕÉ•}½‘”4(€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉÑ}™½Éµ…±}½µµ¥ÑÌ4(€€€€€€€İ¡•É”‘É…™Ñ}¥€ô€ü½É‘•È‰ä¥‘•ÍŒ±¥µ¥Ğ€Ä4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡‘É…™Ñ}¥°¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€Í…Ù•‘}É•Á½ÉÑ}¥€ô‘É…™Ñ}É½Ü¹•Ğ ‰Í…Ù•‘}É•Á½ÉÑ}¥ˆ¤4(€€€ÍÕµµ…Éä€ôì4(€€€€€€€€‰…±±½İ•ˆè…¹}™½Éµ…±}Í…Ù•}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}Í…Ù•ˆ°4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}¥ˆèÍ…Ù•‘}É•Á½ÉÑ}¥°4(€€€ô4(€€€¥˜Í…Ù•‘}É•Á½ÉÑ}¥è4(€€€€€€€ÍÕµµ…Éål‰ÍÑ…ÑÕÌ‰t€ô€‰Í…Ù•ˆ4(€€€¥˜½µµ¥Ğè4(€€€€€€€ÍÕµµ…Éål‰½µµ¥Ñ}ÍÑ…ÑÕÌ‰t€ô½µµ¥Ñl‰ÍÑ…ÑÕÌ‰t4(€€€€€€€ÍÕµµ…Éål‰½µµ¥Ñ}É•Á½ÉÑ}¥‰t€ô½µµ¥Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t4(€€€€€€€ÍÕµµ…Éål‰½µµ¥ÑÑ•‘}…Ğ‰t€ô½µµ¥Ñl‰½µµ¥ÑÑ•‘}…Ğ‰t4(€€€€€€€ÍÕµµ…Éål‰™…¥±ÕÉ•}½‘”‰t€ô½µµ¥Ñl‰™…¥±ÕÉ•}½‘”‰t4(€€€€€€€¥˜½µµ¥Ñl‰ÍÑ…ÑÕÌ‰t€ôô€‰½µµ¥ÑÑ•ˆ…¹¹½ĞÍ…Ù•‘}É•Á½ÉÑ}¥è4(€€€€€€€€€€€ÍÕµµ…Éål‰ÍÑ…ÑÕÌ‰t€ô€‰Í…Ù•ˆ4(€€€€€€€€€€€ÍÕµµ…Éål‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t€ô½µµ¥Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t4(€€€€€€€•±¥˜½µµ¥Ñl‰ÍÑ…ÑÕÌ‰t¥¸€ ‰½µµ¥ÑÑ¥¹œˆ°€‰™…¥±•ˆ¤è4(€€€€€€€€€€€ÍÕµµ…Éål‰ÍÑ…ÑÕÌ‰t€ô€‰Í…Ù•ˆ¥˜Í…Ù•‘}É•Á½ÉÑ}¥•±Í”€‰½µµ¥Ñ|ˆ€¬½µµ¥Ñl‰ÍÑ…ÑÕÌ‰t4(€€€É•ÑÕÉ¸ÍÕµµ…Éä4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½Ù…±¥‘…Ñ¥½¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰•ĞY…±¥‘…Ñ¥½¹I•ÍÕ±Ğ™½È„É…™Ğ€¡A¡…Í”€Ü¤¸4(4(€€€Iµ=91dèÉÕ¹ÌY…±¥‘…Ñ¥½¹¹¥¹”€¡ÁÕÉ”°¹¼İÉ¥Ñ•Ì°¹¼•áÑ•É¹…°A$°4(€€€¹¼™¥±•ÍåÍÑ•´Í…¸¤¸UÍ•ÌY…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È™½È…ÕÑ¡½É¥Ñ…Ñ¥Ù”‘…Ñ„¸4(4(€€€A•Éµ¥ÍÍ¥½¸è…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ€¡™¥¹…¹”É•…µ½¹±ä…±±½İ•¤¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^»š¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€‘É…™Ñ}‘…Ñ„€ôíô4(€€€ÑÉäè4(€€€€€€€‘É…™Ñ}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡‘É…™Ñ}É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤4(€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€‘É…™Ñ}‘…Ñ„€ôì‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆèQÉÕ”°€‰Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ìˆèl‰‘É…™Ñ}‘…Ñ…}½ÉÉÕÁÑ•‰uô4(4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¹Ù…±¥‘…Ñ¥½¹}•¹¥¹”¥µÁ½ÉĞY…±¥‘…Ñ¥½¹¹¥¹”°Y…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È4(€€€½¹Ñ•áÑ}‰Õ¥±‘•È€ôY…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È¡‘ˆ ¤¤4(€€€½¹Ñ•áĞ€ô½¹Ñ•áÑ}‰Õ¥±‘•È¹‰Õ¥±¡‘É…™Ñ}‘…Ñ„¤4(€€€•¹¥¹”€ôY…±¥‘…Ñ¥½¹¹¥¹” ¤4(€€€É•ÍÕ±Ğ€ô•¹¥¹”¹Ù…±¥‘…Ñ” 4(€€€€€€€‘É…™Ñ}‘…Ñ„õ‘É…™Ñ}‘…Ñ„°4(€€€€€€€½¹Ñ•áĞõ½¹Ñ•áĞ°4(€€€€€€€‘É…™Ñ}Ù•ÉÍ¥½¸õ‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆè‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè‘É…™Ñ}É½Ü¹•Ğ ‰ÍÑ…ÑÕÌˆ¤°4(€€€€€€€€‰Ù…±¥‘…Ñ¥½¸ˆèÉ•ÍÕ±Ğ¹Ñ½}‘¥Ğ ¤°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½…­¹½İ±•‘”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}…­¹½İ±•‘”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰­¹½İ±•‘”„]I9%9¥ÍÍÕ”€¡A¡…Í”€Ü¤¸4(4(€€€½¹ÍÑÉ…¥¹ÑÌè4(€€€€´MIÉ•ÅÕ¥É•4(€€€€´‘É…™Ñ}Ù•ÉÍ¥½¸½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ€¡ÍÑ…±”ƒŠH€ĞÀä¤4(€€€€´¥ÍÍÕ•}­•äµÕÍĞ•á¥ÍĞ¥¸ÕÉÉ•¹ĞÙ…±¥‘…Ñ¥½¸4(€€€€´=¹±ä]I9%9Í•Ù•É¥Ñä…¸‰”…­¹½İ±•‘•€¡II=H½%9<É•©•Ñ•¤4(€€€€´…­¹½İ±•‘•µ•¹Ñ}É•ÅÕ¥É•µÕÍĞ‰”ÑÉÕ”4(€€€€´¥¹…¹”½É•…µ½¹±äèA=MP‘•¹¥•4(€€€€´½¹™¥Éµ•½…¹•±±•½Í…Ù•‘É…™ÑÌèÉ•©•Ñ•‰ä…¹}•‘¥Ğ¡•¬4(4(€€€	½‘äèì¥ÍÍÕ•}­•ä°‘É…™Ñ}Ù•ÉÍ¥½¸ô4(€€€I•ÑÕÉ¹ÌÕÁ‘…Ñ•Y…±¥‘…Ñ¥½¹I•ÍÕ±Ğ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€€Œ…¹}•‘¥Ğ•¹™½É•ÌèÍÑ…ÑÕÌôô‘É…™Ğœ°É½±”¥¸í…‘µ¥¸°µ…¹…•È°•µÁ±½å•”İ¥Ñ …•ÍÍô4(€€€€Œ™¥¹…¹”¥ÌÉ•…µ½¹±ä°½¹™¥Éµ•½…¹•±±•½Í…Ù•…É”É•…µ½¹±ä4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ó¾ò#¦r ‘É…™Ğƒ*Ûš’âSšr'ò[¢úGšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€¥ÍÍÕ•}­•ä€ô‘…Ñ„¹•Ğ ‰¥ÍÍÕ•}­•äˆ°€ˆˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜¹½Ğ¥ÍÍÕ•}­•äè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂD¥ÍÍÕ•}­•ä‰ô¤°€ĞÀÀ4(4(€€€€ŒA…ÉÍ”‘É…™Ğ‘…Ñ„4(€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€‘É…™Ñ}‘…Ñ„€ô‘É…™Ğ¹µ½‘•±}‘ÕµÀ ¤4(4(€€€€ŒIÕ¸Ù…±¥‘…Ñ¥½¸Ñ¼™¥¹Ñ¡”¥ÍÍÕ”4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¹Ù…±¥‘…Ñ¥½¹}•¹¥¹”¥µÁ½ÉĞ€ 4(€€€€€€€Y…±¥‘…Ñ¥½¹¹¥¹”°Y…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È°4(€€€€€€€…‘‘}…­¹½İ±•‘•µ•¹Ğ°MYI%Qe}]I9%9°4(€€€€¤4(€€€½¹Ñ•áÑ}‰Õ¥±‘•È€ôY…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È¡‘ˆ ¤¤4(€€€½¹Ñ•áĞ€ô½¹Ñ•áÑ}‰Õ¥±‘•È¹‰Õ¥±¡‘É…™Ñ}‘…Ñ„¤4(€€€•¹¥¹”€ôY…±¥‘…Ñ¥½¹¹¥¹” ¤4(€€€É•ÍÕ±Ğ€ô•¹¥¹”¹Ù…±¥‘…Ñ” 4(€€€€€€€‘É…™Ñ}‘…Ñ„õ‘É…™Ñ}‘…Ñ„°4(€€€€€€€½¹Ñ•áĞõ½¹Ñ•áĞ°4(€€€€€€€‘É…™Ñ}Ù•ÉÍ¥½¸õ‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€¤4(4(€€€€Œ¥¹Ñ¡”¥ÍÍÕ”‰ä­•ä4(€€€Ñ…É•Ñ}¥ÍÍÕ”€ô9½¹”4(€€€™½È¥ÍÍÕ”¥¸É•ÍÕ±Ğ¹¥ÍÍÕ•Ìè4(€€€€€€€¥˜¥ÍÍÕ”¹¥ÍÍÕ•}­•ä€ôô¥ÍÍÕ•}­•äè4(€€€€€€€€€€€Ñ…É•Ñ}¥ÍÍÕ”€ô¥ÍÍÕ”4(€€€€€€€€€€€‰É•…¬4(4(€€€¥˜Ñ…É•Ñ}¥ÍÍÕ”¥Ì9½¹”è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè€‰¥ÍÍÕ•}­•äƒ’â7–¶c–r ˆ°4(€€€€€€€€€€€€‰¥ÍÍÕ•}­•äˆè¥ÍÍÕ•}­•ä°4(€€€€€€€ô¤°€ĞÀĞ4(4(€€€€Œ=¹±ä]I9%9…¸‰”…­¹½İ±•‘•4(€€€¥˜Ñ…É•Ñ}¥ÍÍÕ”¹Í•Ù•É¥Ñä€„ôMYI%Qe}]I9%9è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‹–>«¢÷†»¢º]I9%9ƒêŸ–"¯j¦^»¦Šc¾ò#–öO–&4èíÑ…É•Ñ}¥ÍÍÕ”¹Í•Ù•É¥Ñå÷¾ò$ˆ°4(€€€€€€€€€€€€‰¥ÍÍÕ•}­•äˆè¥ÍÍÕ•}­•ä°4(€€€€€€€€€€€€‰Í•Ù•É¥ÑäˆèÑ…É•Ñ}¥ÍÍÕ”¹Í•Ù•É¥Ñä°4(€€€€€€€ô¤°€ĞÀÀ4(4(€€€¥˜¹½ĞÑ…É•Ñ}¥ÍÍÕ”¹…­¹½İ±•‘•µ•¹Ñ}É•ÅÕ¥É•è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè€‹¢¾—¦^»¦Šc’â7¦r¢š†»¢ºˆ°4(€€€€€€€€€€€€‰¥ÍÍÕ•}­•äˆè¥ÍÍÕ•}­•ä°4(€€€€€€€ô¤°€ĞÀÀ4(4(€€€€Œ=ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ4(€€€ÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸€ô‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤4(€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”…¹•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€„ôÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾Tˆ°4(€€€€€€€€€€€€‰•áÁ•Ñ•‘}Ù•ÉÍ¥½¸ˆè•áÁ•Ñ•‘}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€€‰ÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸ˆèÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸°4(€€€€€€€ô¤°€ĞÀä4(4(€€€€Œ‘…­¹½İ±•‘•µ•¹Ğ€¡µÕÑ…Ñ•Ì‘É…™Ñ}‘…Ñ„¥¸Á±…”¤4(€€€ÕÍ•È€ôœ¹ÕÍ•È4(€€€¥˜¡…Í…ÑÑÈ¡ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€ÕÍ•È€ô‘¥Ğ¡ÕÍ•È¤4(€€€ÕÍ•É}¥€ôÕÍ•È¹•Ğ ‰¥ˆ°€ˆˆ¤4(4(€€€¹½İ}¥Í¼€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•‘P• è•4è•Mhˆ¤4(4(€€€…‘‘}…­¹½İ±•‘•µ•¹Ğ 4(€€€€€€€‘É…™Ñ}‘…Ñ„õ‘É…™Ñ}‘…Ñ„°4(€€€€€€€¥ÍÍÕ•}­•äõ¥ÍÍÕ•}­•ä°4(€€€€€€€ÉÕ±•}¥õÑ…É•Ñ}¥ÍÍÕ”¹ÉÕ±•}¥°4(€€€€€€€¥ÍÍÕ•}™¥¹•ÉÁÉ¥¹ĞõÑ…É•Ñ}¥ÍÍÕ”¹¥ÍÍÕ•}™¥¹•ÉÁÉ¥¹Ğ°4(€€€€€€€Ù…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹ĞõÉ•ÍÕ±Ğ¹Ù…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹Ğ°4(€€€€€€€‘É…™Ñ}Ù•ÉÍ¥½¸õÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸°4(€€€€€€€ÕÍ•É}¥õÕÍ•É}¥°4(€€€€€€€¹½İ}¥Í¼õ¹½İ}¥Í¼°4(€€€€¤4(4(€€€€ŒM…Ù”ÕÁ‘…Ñ•‘É…™Ğ4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¹Í¡•µ…Ì¥µÁ½ÉĞ…¥±åI•Á½ÉÑÉ…™Ğ4(€€€ÕÁ‘…Ñ•‘}‘É…™Ğ€ô…¥±åI•Á½ÉÑÉ…™Ğ ¨©‘É…™Ñ}‘…Ñ„¤4(€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°ÕÁ‘…Ñ•‘}‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(4(€€€€ŒI”µÉÕ¸Ù…±¥‘…Ñ¥½¸Ñ¼É•ÑÕÉ¸ÕÁ‘…Ñ•É•ÍÕ±Ğ4(€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€ÕÁ‘…Ñ•‘}‘…Ñ„€ôíô4(€€€ÑÉäè4(€€€€€€€ÕÁ‘…Ñ•‘}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡ÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤4(€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€ÕÁ‘…Ñ•‘}‘…Ñ„€ôíô4(€€€½¹Ñ•áĞÈ€ô½¹Ñ•áÑ}‰Õ¥±‘•È¹‰Õ¥±¡ÕÁ‘…Ñ•‘}‘…Ñ„¤4(€€€É•ÍÕ±ĞÈ€ô•¹¥¹”¹Ù…±¥‘…Ñ” 4(€€€€€€€‘É…™Ñ}‘…Ñ„õÕÁ‘…Ñ•‘}‘…Ñ„°4(€€€€€€€½¹Ñ•áĞõ½¹Ñ•áĞÈ°4(€€€€€€€‘É…™Ñ}Ù•ÉÍ¥½¸õÕÁ‘…Ñ•‘}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€¤4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€€€€€‰¥ÍÍÕ•}­•äˆè¥ÍÍÕ•}­•ä°4(€€€€€€€€‰…­¹½İ±•‘•‘}‰äˆèÕÍ•É}¥°4(€€€€€€€€‰…­¹½İ±•‘•‘}…Ğˆè¹½İ}¥Í¼°4(€€€€€€€€‰Ù…±¥‘…Ñ¥½¸ˆèÉ•ÍÕ±ĞÈ¹Ñ½}‘¥Ğ ¤°4(€€€ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½Á¡½Ñ¼¼ñÁ¡½Ñ½}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}Á¡½Ñ½}ÁÉ•Ù¥•Ü¡‘É…™Ñ}¥°Á¡½Ñ½}¥¤è4(€€€€ˆˆ‰M•ÕÉ”Á¡½Ñ¼ÁÉ•Ù¥•Ü™½ÈÉ…™Ğ¸4(4(€€€½•Ì9=P…•ÁĞ±¥•¹ĞµÍÕÁÁ±¥•Á…Ñ ¸UÍ•Ì‘É…™Ñ}¥€¬Á¡½Ñ½}¥Ñ¼É•Í½±Ù”¸4(€€€Y…±¥‘…Ñ•ÌèÕÍ•ÈÁ•Éµ¥ÍÍ¥½¸°‘É…™Ğ½İ¹•ÉÍ¡¥À°Á…Ñ ½¹™¥¹•µ•¹Ğ°¡…Í ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€€Œ¥¹Á¡½Ñ¼¥¸‘É…™ĞÌÁ¡½Ñ½}…¹‘¥‘…Ñ•Ì4(€€€‘É…™Ñ}‘…Ñ„€ôíô4(€€€ÑÉäè4(€€€€€€€‘É…™Ñ}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡‘É…™Ñ}É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤4(€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€Á¡½Ñ½}É•˜€ô9½¹”4(€€€™½ÈÀ¥¸‘É…™Ñ}‘…Ñ„¹•Ğ ‰Á¡½Ñ½}…¹‘¥‘…Ñ•Ìˆ°mt¤è4(€€€€€€€¥˜À¹•Ğ ‰Á¡½Ñ½}¥ˆ¤€ôôÁ¡½Ñ½}¥½ÈÀ¹•Ğ ‰Á¡½Ñ½}¡…Í ˆ¤€ôôÁ¡½Ñ½}¥è4(€€€€€€€€€€€Á¡½Ñ½}É•˜€ôÀ4(€€€€€€€€€€€‰É•…¬4(4(€€€¥˜¹½ĞÁ¡½Ñ½}É•˜è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€€ŒI•Í½±Ù”Á…Ñ Í…™•±ä4(€€€É•±…Ñ¥Ù•}Á…Ñ €ôÁ¡½Ñ½}É•˜¹•Ğ ‰É•±…Ñ¥Ù•}Á…Ñ ˆ°€ˆˆ¤4(€€€¥˜¹½ĞÉ•±…Ñ¥Ù•}Á…Ñ ½ÈA…Ñ ¡É•±…Ñ¥Ù•}Á…Ñ ¤¹¥Í}…‰Í½±ÕÑ” ¤½È€ˆ¸¸ˆ¥¸A…Ñ ¡É•±…Ñ¥Ù•}Á…Ñ ¤¹Á…ÉÑÌè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€ÑÉäè4(€€€€€€€™Õ±±}Á…Ñ €ô€¡A…Ñ ¡M!I}A!=Q=M}%H¤€¼É•±…Ñ¥Ù•}Á…Ñ ¤¹É•Í½±Ù” ¤4(€€€€€€€¥˜¹½ĞÍÑÈ¡™Õ±±}Á…Ñ ¤¹ÍÑ…ÉÑÍİ¥Ñ ¡ÍÑÈ¡A…Ñ ¡M!I}A!=Q=M}%H¤¹É•Í½±Ù” ¤¤¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€¥˜¹½Ğ™Õ±±}Á…Ñ ¹•á¥ÍÑÌ ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡ÍÑÈ¡™Õ±±}Á…Ñ ¤°…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°½¹‘¥Ñ¥½¹…°õQÉÕ”¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½•Ù¥‘•¹”¼ñ•Ù¥‘•¹•}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}•Ù¥‘•¹•}ÁÉ•Ù¥•Ü¡‘É…™Ñ}¥°•Ù¥‘•¹•}¥¤è4(€€€€ˆˆ‰M•ÕÉ”5¥±•…”Ù¥‘•¹”ÁÉ•Ù¥•Ü™½ÈÉ…™Ğ¸4(4(€€€½•Ì9=P…•ÁĞ±¥•¹ĞµÍÕÁÁ±¥•Á…Ñ ¸UÍ•Ì‘É…™Ñ}¥€¬•Ù¥‘•¹•}¥Ñ¼É•Í½±Ù”¸4(€€€Y…±¥‘…Ñ•ÌèÕÍ•ÈÁ•Éµ¥ÍÍ¥½¸°‘É…™Ğ½İ¹•ÉÍ¡¥À°Á…Ñ ½¹™¥¹•µ•¹Ğ°¡…Í ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€€Œ¥¹•Ù¥‘•¹”¥¸‘É…™ĞÌ•Ù¥‘•¹•}É•½É‘Ì4(€€€‘É…™Ñ}‘…Ñ„€ôíô4(€€€ÑÉäè4(€€€€€€€‘É…™Ñ}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡‘É…™Ñ}É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤4(€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€•Ù¥‘•¹”€ô9½¹”4(€€€™½È”¥¸‘É…™Ñ}‘…Ñ„¹•Ğ ‰•Ù¥‘•¹•}É•½É‘Ìˆ°mt¤è4(€€€€€€€¥˜”¹•Ğ ‰•Ù¥‘•¹•}¥ˆ¤€ôô•Ù¥‘•¹•}¥è4(€€€€€€€€€€€•Ù¥‘•¹”€ô”4(€€€€€€€€€€€‰É•…¬4(4(€€€¥˜¹½Ğ•Ù¥‘•¹”è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€€ŒI•Í½±Ù”Á…Ñ Í…™•±ä€¡É…™Ğ•Ù¥‘•¹”¥ÌÍÑ½É•¥¸Q}%H½…¤µ‘…¥±äµÉ•Á½ÉĞµ‘É…™ÑÌ¼¤4(€€€É•±…Ñ¥Ù•}Á…Ñ €ô•Ù¥‘•¹”¹•Ğ ‰™¥±•}É•±…Ñ¥Ù•}Á…Ñ ˆ°€ˆˆ¤4(€€€¥˜¹½ĞÉ•±…Ñ¥Ù•}Á…Ñ è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€€ŒÙ¥‘•¹”Á…Ñ Í¡½Õ±‰”Õ¹‘•ÈÉ…™Ğ•Ù¥‘•¹”‘¥É•Ñ½Éä¸4(€€€€ŒA•ÉÍ¥ÍÑ•É•½É‘Ìµ…äÍÑ½É”•¥Ñ¡•È„‰…É”™¥±•¹…µ”€¡É•±…Ñ¥Ù”Ñ¼Ñ¡”4(€€€€Œ‘É…™Ğµ¥±•…”‘¥È¤½È„™Õ±°Q}%HµÉ•±…Ñ¥Ù”Á…Ñ ì¹½Éµ…±¥é”‰½Ñ ¸4(€€€•áÁ•Ñ•‘}ÁÉ•™¥à€ô˜‰…¤µ‘…¥±äµÉ•Á½ÉĞµ‘É…™ÑÌ½í‘É…™Ñ}¥‘ô½µ¥±•…”¼ˆ4(€€€¥˜É•±…Ñ¥Ù•}Á…Ñ ¹ÍÑ…ÉÑÍİ¥Ñ  ‰…¤µ‘…¥±äµÉ•Á½ÉĞµ‘É…™ÑÌ¼ˆ¤è4(€€€€€€€¥˜¹½ĞÉ•±…Ñ¥Ù•}Á…Ñ ¹ÍÑ…ÉÑÍİ¥Ñ ¡•áÁ•Ñ•‘}ÁÉ•™¥à¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€É•Í½±Ù•‘}É•°€ôÉ•±…Ñ¥Ù•}Á…Ñ 4(€€€•±Í”è4(€€€€€€€¥˜€ˆ¼ˆ¥¸É•±…Ñ¥Ù•}Á…Ñ ½È€‰qpˆ¥¸É•±…Ñ¥Ù•}Á…Ñ ½È€ˆ¸¸ˆ¥¸A…Ñ ¡É•±…Ñ¥Ù•}Á…Ñ ¤¹Á…ÉÑÌè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€É•Í½±Ù•‘}É•°€ô•áÁ•Ñ•‘}ÁÉ•™¥à€¬É•±…Ñ¥Ù•}Á…Ñ 4(4(€€€¥˜A…Ñ ¡É•Í½±Ù•‘}É•°¤¹¥Í}…‰Í½±ÕÑ” ¤½È€ˆ¸¸ˆ¥¸A…Ñ ¡É•Í½±Ù•‘}É•°¤¹Á…ÉÑÌè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€ÑÉäè4(€€€€€€€™Õ±±}Á…Ñ €ô€¡A…Ñ ¡Q}%H¤€¼É•Í½±Ù•‘}É•°¤¹É•Í½±Ù” ¤4(€€€€€€€¥˜¹½ĞÍÑÈ¡™Õ±±}Á…Ñ ¤¹ÍÑ…ÉÑÍİ¥Ñ ¡ÍÑÈ¡A…Ñ ¡Q}%H¤¹É•Í½±Ù” ¤¤¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€¥˜¹½Ğ™Õ±±}Á…Ñ ¹•á¥ÍÑÌ ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡ÍÑÈ¡™Õ±±}Á…Ñ ¤°…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°½¹‘¥Ñ¥½¹…°õQÉÕ”¤4(4(4(ŒƒŠRŠRŠR A¡…Í”€àèÑÑ…¡µ•¹ĞAÉ•Á…É…Ñ¥½¸€¼5…¹¥™•ÍĞƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)‘•˜}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤è4(€€€É•ÑÕÉ¸ÑÑ…¡µ•¹Ñ5…¹¥™•ÍÑM•ÉÙ¥”¡‘ˆ ¤°M!I}A!=Q=M}%H°Q}%H°¥¹Ğ¡œ¹ÕÍ•Él‰¥‰t¤¤4(4(4)‘•˜}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤è4(€€€€ˆˆ‰M•ÉÙ•ÈµÍ¥‘”A¡…Í”€ÜY…±¥‘…Ñ¥½¸É•ÉÕ¸€¡É•…µ½¹±ä¤¸4(4(€€€A¡…Í”€à5UMP9=PÑÉÕÍĞ…¹ä™É½¹Ñ•¹…¹}ÁÉ½••™±…œìÑ¡¥Ì¥ÌÑ¡”½¹±ä4(€€€…Ñ”™½Èµ…¹¥™•ÍĞÁÉ•Á…É…Ñ¥½¸¸4(€€€€ˆˆˆ4(€€€‘É…™Ñ}‘…Ñ„€ôíô4(€€€ÑÉäè4(€€€€€€€‘É…™Ñ}‘…Ñ„€ô©Í½¸¹±½…‘Ì¡‘É…™Ñ}É½İl‰‘É…™Ñ}‘…Ñ„‰t½È€‰íôˆ¤4(€€€•á•ÁĞ€¡©Í½¸¹)M=9•½‘•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€‘É…™Ñ}‘…Ñ„€ôì‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆèQÉÕ”°€‰Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ìˆèl‰‘É…™Ñ}‘…Ñ…}½ÉÉÕÁÑ•‰uô4(€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¹Ù…±¥‘…Ñ¥½¹}•¹¥¹”¥µÁ½ÉĞY…±¥‘…Ñ¥½¹¹¥¹”°Y…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È4(€€€½¹Ñ•áĞ€ôY…±¥‘…Ñ¥½¹½¹Ñ•áÑ	Õ¥±‘•È¡‘ˆ ¤¤¹‰Õ¥±¡‘É…™Ñ}‘…Ñ„¤4(€€€É•ÑÕÉ¸Y…±¥‘…Ñ¥½¹¹¥¹” ¤¹Ù…±¥‘…Ñ” 4(€€€€€€€‘É…™Ñ}‘…Ñ„õ‘É…™Ñ}‘…Ñ„°4(€€€€€€€½¹Ñ•áĞõ½¹Ñ•áĞ°4(€€€€€€€‘É…™Ñ}Ù•ÉÍ¥½¸õ‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€¤4(4(4)‘•˜}µ…¹¥™•ÍÑ}•ÉÉ½É}É•ÍÁ½¹Í”¡•áŒ°‘•™…Õ±Ñ}ÍÑ…ÑÕÌôĞÈÈ¤è4(€€€¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°5…¹¥™•ÍÑY…±¥‘…Ñ¥½¹	±½­•‘ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÈÈ4(€€€¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°5…¹¥™•ÍÑ%¹Ñ•É¥ÑåÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÈÈ4(€€€¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°5…¹¥™•ÍÑMÑ…±•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÀä4(€€€¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°5…¹¥™•ÍÑ9½Ñ½Õ¹‘ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÀĞ4(€€€¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°5…¹¥™•ÍÑÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°‘•™…Õ±Ñ}ÍÑ…ÑÕÌ4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹¦f’îÛ––’–’Ç¢Ò”‰ô¤°€ÔÀÀ4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½ÁÉ•Á…É”µ…ÑÑ…¡µ•¹ÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}ÁÉ•Á…É•}…ÑÑ…¡µ•¹ÑÌ¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰AÉ•Á…É”€¡½ÈÉ•ÕÍ”¤„É•…‘äÑÑ…¡µ•¹Ğ5…¹¥™•ÍĞ™½È„½¹™¥Éµ•É…™Ğ¸4(4(€€€A¡…Í”€àÍÑ…Ñ”µ…¡¥¹”èÉ…™Ğ€´øY…±¥‘…Ñ¥½¸€´ø½¹™¥É´€´øAÉ•Á…É”€´øA¡…Í”€ä¸4(€€€€´=¹±äÍÑ…ÑÕÌ€ôô€½¹™¥Éµ•œ€¡…¹}ÁÉ•Á…É•}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍĞ¤4(€€€€´MIÉ•ÅÕ¥É•4(€€€€´M•ÉÙ•ÈµÍ¥‘”A¡…Í”€ÜÙ…±¥‘…Ñ¥½¸…Ñ”€¡…¹}ÁÉ½••€ôô…±Í”€´ø€ĞÈÈ¤4(€€€€´‘É…™Ñ}Ù•ÉÍ¥½¸½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ€¡ÍÑ…±”€´ø€ĞÀä¤4(€€€€´%‘•µÁ½Ñ•¹Ğè¥‘•¹Ñ¥…°™¥¹•ÉÁÉ¥¹ĞÉ•ÕÍ•ÌÑ¡”•á¥ÍÑ¥¹œÉ•…‘äµ…¹¥™•ÍĞ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}ÁÉ•Á…É•}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍĞ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv––’¦f’îÛ¾ò#¦r ½¹™¥Éµ•ƒ*Ûš’âS¦v{–>«¢¾ïšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”…¹•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€„ô¥¹Ğ¡‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾Wˆ°€‰½‘”ˆè€‰Ù•ÉÍ¥½¹}½¹™±¥Ğ‰ô¤°€ĞÀä4(4(€€€Ù…±¥‘…Ñ¥½¸€ô}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤4(€€€¥˜¹½ĞÙ…±¥‘…Ñ¥½¸¹…¹}ÁÉ½••è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè€‰A¡…Í”€Üƒš‚‡¦ª3šr«¦k¢ş¾ò#–¶c–r£šr«¢–ÏjII=K¾ò'¾ò3š^ƒšÎW––’¦f’îÛšâ–6Wˆ°4(€€€€€€€€€€€€‰½‘”ˆè€‰Ù…±¥‘…Ñ¥½¹}…¹¹½Ñ}ÁÉ½••ˆ°4(€€€€€€€€€€€€‰Ù…±¥‘…Ñ¥½¸ˆèÙ…±¥‘…Ñ¥½¸¹Ñ½}‘¥Ğ ¤°4(€€€€€€€ô¤°€ĞÈÈ4(4(€€€ÑÉäè4(€€€€€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€€€€€µ…¹¥™•ÍĞ€ôµ…¹¥™•ÍÑ}ÍÙŒ¹ÁÉ•Á…É”¡‘É…™Ñ}É½Ü°Ù…±¥‘…Ñ¥½¸¤4(€€€•á•ÁĞ5…¹¥™•ÍÑÉÉ½È…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸}µ…¹¥™•ÍÑ}•ÉÉ½É}É•ÍÁ½¹Í”¡•áŒ¤4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°€‰µ…¹¥™•ÍĞˆèµ…¹¥™•ÍÑô¤4(4(4(ŒƒŠRŠRŠR A¡…Í”€äè½Éµ…°M…Ù”€¼QÉ…¹Í…Ñ¥½¹…°½µµ¥ĞƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½™½Éµ…°µÍ…Ù”ˆ¤4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}™½Éµ…±}Í…Ù”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰½Éµ…°M…Ù”è‘•Ñ•Éµ¥¹¥ÍÑ¥…±±ä½µµ¥Ğ„½¹™¥Éµ•É…™Ğ€¬É•…‘ä5…¹¥™•ÍĞ¸4(4(€€€A¡…Í”€äÍÑ…Ñ”µ…¡¥¹”èÉ…™Ğ€´øY…±¥‘…Ñ¥½¸€´ø½¹™¥É´€´øAÉ•Á…É”€´ø½Éµ…°M…Ù”¸4(€€€€´=¹±äÍÑ…ÑÕÌ€ôô€½¹™¥Éµ•œ€¡…¹}™½Éµ…±}Í…Ù•}‘É…™Ğ¤4(€€€€´MIÉ•ÅÕ¥É•4(€€€€´M•ÉÙ•ÈµÍ¥‘”A¡…Í”€ÜÙ…±¥‘…Ñ¥½¸…Ñ”€¡…¹}ÁÉ½••€ôô…±Í”€´ø€ĞÈÈ¤4(€€€€´á…Ñ±äµ½¹”è„É…™Ğµ…ÁÌÑ¼…Ğµ½ÍĞ½¹”™½Éµ…°Í•ÉÙ¥•}É•Á½ÉĞ4(€€€€€€ ÈÀÀÉ•ÑÕÉ¹ÌÑ¡”•á¥ÍÑ¥¹œÉ•Á½ÉĞì€ÈÀÄ™¥ÉÍĞÉ•…Ñ¥½¸¤4(€€€€´±¥•¹Ğµ…ä½¹±äÍÕ‰µ¥Ğ‘É…™Ñ}Ù•ÉÍ¥½¸€¬µ…¹¥™•ÍÑ}¥€¡½ÁÑ¥µ¥ÍÑ¥Œ±½¬¤¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğœ¹ÕÍ•Èè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šr«fï–öT‰ô¤°€ĞÀÄ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^»š¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€€ŒMÑ…Ñ”µ…¡¥¹”è½¹±ä½¹™¥Éµ•€¡½È…±É•…‘äµÍ…Ù•¤‘É…™ÑÌµ…ä¡¥ĞÑ¡¥Ì¸4(€€€¥˜‘É…™Ñ}É½Ü¹•Ğ ‰ÍÑ…ÑÕÌˆ¤¹½Ğ¥¸ì‰½¹™¥Éµ•ˆ°€‰Í…Ù•‰ôè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€‰•ÉÉ½Èˆè€‰É…™Ğƒ*Ûš’â7–¢ºãš¶–ò?’şw–¶cˆ°4(€€€€€€€€€€€€‰½‘”ˆè€‰‘É…™Ñ}ÍÑ…Ñ•}•ÉÉ½Èˆ°4(€€€€€€€ô¤°€ĞÀä4(4(€€€€Œá…Ñ±äµ½¹”è…¸…±É•…‘äµÍ…Ù•É…™ĞÉ•ÑÕÉ¹Ì¥ÑÌ•á¥ÍÑ¥¹œ™½Éµ…°É•Á½ÉĞ4(€€€€Œ€ ÈÀÀ¤°•Ù•¸¥˜Ñ¡”±¥•¹ĞÉ•ÑÉ¥•Ì…™Ñ•È„±½ÍĞÉ•ÍÁ½¹Í”¸4(€€€¥˜‘É…™Ñ}É½Ü¹•Ğ ‰Í…Ù•‘}É•Á½ÉÑ}¥ˆ¤è4(€€€€€€€É•Á½ÉÑ}¥€ô¥¹Ğ¡‘É…™Ñ}É½İl‰Í…Ù•‘}É•Á½ÉÑ}¥‰t¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰…±É•…‘å}½µµ¥ÑÑ•ˆ°4(€€€€€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}¥ˆèÉ•Á½ÉÑ}¥°4(€€€€€€€€€€€€‰É•Á½ÉÑ}ÕÉ°ˆèÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤°4(€€€€€€€ô¤°€ÈÀÀ4(4(€€€¥˜¹½Ğ…¹}™½Éµ…±}Í…Ù•}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšvš¶–ò?’şw–¶c¾ò#¦r ½¹™¥Éµ•ƒ*Ûš’âS¦v{–>«¢¾ïšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€±¥•¹Ñ}µ…¹¥™•ÍÑ}¥€ô‘…Ñ„¹•Ğ ‰µ…¹¥™•ÍÑ}¥ˆ¤4(€€€€€€€¥˜±¥•¹Ñ}µ…¹¥™•ÍÑ}¥¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€±¥•¹Ñ}µ…¹¥™•ÍÑ}¥€ôÍÑÈ¡±¥•¹Ñ}µ…¹¥™•ÍÑ}¥¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€Ù…±¥‘…Ñ¥½¸€ô}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤4(€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€ÑÉäè4(€€€€€€€™½Éµ…±}ÍÙŒ€ô½Éµ…±M…Ù•M•ÉÙ¥” 4(€€€€€€€€€€€‘ˆ ¤°Q}%H°IA=IQ}QQ!59QM}%H°¥¹Ğ¡œ¹ÕÍ•Él‰¥‰t¤4(€€€€€€€€¤4(€€€€€€€É•ÍÕ±Ğ€ô™½Éµ…±}ÍÙŒ¹ÉÕ¸ 4(€€€€€€€€€€€‘É…™Ñ}É½Ü°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸°±¥•¹Ñ}µ…¹¥™•ÍÑ}¥°Ù…±¥‘…Ñ¥½¸°µ…¹¥™•ÍÑ}ÍÙŒ4(€€€€€€€€¤4(€€€•á•ÁĞ€¡½Éµ…±M…Ù•MÑ…±•ÉÉ½È°½Éµ…±M…Ù•MÑ…Ñ•ÉÉ½È°½Éµ…±M…Ù•½µµ¥Ñ½¹™±¥ÑÉÉ½È¤…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÀä4(€€€•á•ÁĞ€¡½Éµ…±M…Ù•%¹Ñ•É¥ÑåÉÉ½È°½Éµ…±M…Ù•Y…±¥‘…Ñ¥½¹	±½­•‘ÉÉ½È°½Éµ…±M…Ù•½µÁ±¥…¹•ÉÉ½È¤…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè•áŒ¹µ•ÍÍ…”°€‰½‘”ˆè•áŒ¹½‘•ô¤°€ĞÈÈ4(€€€•á•ÁĞ5…¹¥™•ÍÑÉÉ½È…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸}µ…¹¥™•ÍÑ}•ÉÉ½É}É•ÍÁ½¹Í”¡•áŒ¤4(4(€€€ÍÑ…ÑÕÍ}½‘”€ô€ÈÀÄ¥˜É•ÍÕ±Ñl‰ÍÑ…ÑÕÌ‰t€ôô€‰É•…Ñ•ˆ•±Í”€ÈÀÀ4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰ÍÑ…ÑÕÌˆèÉ•ÍÕ±Ñl‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}¥ˆèÉ•ÍÕ±Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t°4(€€€€€€€€‰É•Á½ÉÑ}ÕÉ°ˆèÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•ÍÕ±Ñl‰Í•ÉÙ¥•}É•Á½ÉÑ}¥‰t¤°4(€€€ô¤°ÍÑ…ÑÕÍ}½‘”4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½µ…¹¥™•ÍĞ¼ñµ…¹¥™•ÍÑ}¥ø½½µÁ±¥…¹”µÉ•Ù¥•Üˆ¤4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}½µÁ±¥…¹•}É•Ù¥•Ü¡‘É…™Ñ}¥°µ…¹¥™•ÍÑ}¥¤è4(€€€€ˆˆ‰!Õµ…¸½µÁ±¥…¹”É•Ù¥•Ü½˜„½½±”µ¥±•…”•Ù¥‘•¹”Í½ÕÉ”€¡A¡…Í”€à•áÑ•¹Í¥½¸¤¸4(4(€€€5¥¹¥µ…°‘•Í¥¸€¡Í•…±•A¡…Í”€äMQ@€Ô¤è½¹±ä…‘µ¥¸½µ…¹…•Èµ…äµ…É¬„4(€€€½µÁ±¥…¹•}É•Ù¥•İ}É•ÅÕ¥É•Í½ÕÉ”…ÌÉ•Ù¥•İ•¸A¡…Í”€à¼ä™±½İÌ¹•Ù•Èµ…É¬4(€€€É•Ù¥•İ•…ÕÑ½µ…Ñ¥…±±ä¸áÑ•É¹…°É½±•Ì€¼™¥¹…¹”­••ÀÕÉÉ•¹Ğ‰½Õ¹‘…É¥•Ì¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğœ¹ÕÍ•Èè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šr«fï–öT‰ô¤°€ĞÀÄ4(€€€¥˜¡…Í…ÑÑÈ¡œ¹ÕÍ•È°€‰­•åÌˆ¤è4(€€€€€€€œ¹ÕÍ•È€ô‘¥Ğ¡œ¹ÕÍ•È¤4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(€€€É½±”€ôœ¹ÕÍ•È¹•Ğ ‰É½±”ˆ°€ˆˆ¤4(€€€¥˜É½±”¹½Ğ¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•È‰ôè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹’îº‡B–F`¿î?B–>¿š&Ÿ¢†3–B#¢–º‡š~”‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^»š¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€€Œ½µÁ±¥…¹”É•Ù¥•Ü½¹±äµ…­•ÌÍ•¹Í”½¸„½¹™¥Éµ•É…™Ğè„É•½Á•¹•½È4(€€€€Œ…±É•…‘äµÍ…Ù•É…™ĞµÕÍĞ¹½Ğ±•Ğ…¸½±µ…¹¥™•ÍĞÉ•Ù¥•Ü‰”µ¥Í…ÁÁ±¥•4(€€€€Œ€¡Q€Ø€¼I•…‘ä´ùI•½Á•¸¥¹Ù…É¥…¹Ğ¤¸4(€€€¥˜‘É…™Ñ}É½Ü¹•Ğ ‰ÍÑ…ÑÕÌˆ¤€„ô€‰½¹™¥Éµ•ˆè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ*Ûš’â7–¢ºã–B#¢–º‡š~—ˆ°€‰½‘”ˆè€‰‘É…™Ñ}ÍÑ…Ñ•}•ÉÉ½È‰ô¤°€ĞÀä4(4(€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€‘‰Œ€ô‘ˆ ¤4(€€€µ…¹¥™•ÍĞ€ô‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´…¥}‘…¥±å}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑÌİ¡•É”µ…¹¥™•ÍÑ}¥€ô€ü…¹‘É…™Ñ}¥€ô€üˆ°4(€€€€€€€€¡µ…¹¥™•ÍÑ}¥°‘É…™Ñ}¥¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğµ…¹¥™•ÍĞè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰5…¹¥™•ÍĞƒ’â7–¶c–r£š"[’â7–Æ{’ê;¢¾”É…™Ğ‰ô¤°€ĞÀĞ4(4(€€€Í½ÕÉ•Ì€ô‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•ĞÍ½ÕÉ•}¥°½µÁ±¥…¹•}ÍÑ…ÑÕÌ…Ì½±‘}ÍÑ…ÑÕÌ°ÁÉ½Ù¥‘•È4(€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}Í½ÕÉ•Ì4(€€€€€€€İ¡•É”µ…¹¥™•ÍÑ}¥€ô€ü…¹½µÁ±¥…¹•}É•Ù¥•İ}É•ÅÕ¥É•€ô€Ä…¹½µÁ±¥…¹•}ÍÑ…ÑÕÌ€„ô€É•Ù¥•İ•œ4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡µ…¹¥™•ÍÑ}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜¹½ĞÍ½ÕÉ•Ìè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šÊ‡šr'–ú–º‡š~—j–B#¢¦†ä‰ô¤°€ĞÀÀ4(€€€É•Ù¥•İ•‘}…Ğ€ô¹½Ü ¤4(€€€™½ÈÍÉŒ¥¸Í½ÕÉ•Ìè4(€€€€€€€‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}Í½ÕÉ•Ì4(€€€€€€€€€€€Í•Ğ½µÁ±¥…¹•}ÍÑ…ÑÕÌ€ô€É•Ù¥•İ•œ°4(€€€€€€€€€€€€€€€½µÁ±¥…¹•}É•Ù¥•İ•‘}‰ä€ô€ü°4(€€€€€€€€€€€€€€€½µÁ±¥…¹•}É•Ù¥•İ•‘}…Ğ€ô€ü4(€€€€€€€€€€€İ¡•É”Í½ÕÉ•}¥€ô€ü4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡œ¹ÕÍ•È¹•Ğ ‰¥ˆ¤°É•Ù¥•İ•‘}…Ğ°ÍÉl‰Í½ÕÉ•}¥‰t¤°4(€€€€€€€€¤4(€€€‘‰Œ¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”…¥}‘…¥±å}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑÌÍ•ĞÕÁ‘…Ñ•‘}…Ğ€ô€üİ¡•É”µ…¹¥™•ÍÑ}¥€ô€üˆ°4(€€€€€€€€¡É•Ù¥•İ•‘}…Ğ°µ…¹¥™•ÍÑ}¥¤°4(€€€€¤4(€€€‘‰Œ¹½µµ¥Ğ ¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰ÕÁ‘…Ñ”ˆ°€‰…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍĞˆ°µ…¹¥™•ÍÑ}¥°€ˆˆ°4(€€€€€€€˜‹–B#¢–º‡š~—¦k¢şí±•¸¡Í½ÕÉ•Ì¥ôƒ¦†ç¾ò!íÍ½ÕÉ•ÍlÁulÁÉ½Ù¥‘•Èu÷¾ò'¾ò3š^Ÿ*ÛšíÍ½ÕÉ•ÍlÁul½±‘}ÍÑ…ÑÕÌuô€´øÉ•Ù¥•İ•“¾ò0ˆ4(€€€€€€€˜‰‘É…™Ñ}Ù•ÉÍ¥½¸õíµ…¹¥™•ÍÑl‘É…™Ñ}Ù•ÉÍ¥½¸uôÙ…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹Ğõíµ…¹¥™•ÍÑlÙ…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹Ğuôˆ°4(€€€€¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰É•Ù¥•İ•ˆè±•¸¡Í½ÕÉ•Ì¤°€‰É•Ù¥•İ•‘}…ĞˆèÉ•Ù¥•İ•‘}…Ñô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½µ…¹¥™•ÍĞˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}µ…¹¥™•ÍĞ¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰PÑÑ…¡µ•¹Ğ5…¹¥™•ÍĞÍÑ…Ñ”€¡É•…µ½¹±ä¤¸4(4(€€€I•ÑÕÉ¹ÌÕÉÉ•¹Ñ}…ÁÁ±¥…‰±•}µ…¹¥™•ÍĞèÑ¡”É•…‘äµ…¹¥™•ÍĞµ…Ñ¡¥¹œÑ¡”4(€€€ÕÉÉ•¹Ğ‘É…™Ñ}Ù•ÉÍ¥½¸€¬Ù…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹Ğ¸=±ÍÑ…±”½…¹•±±•½™…¥±•4(€€€µ…¹¥™•ÍÑÌ…É”É•ÑÕÉ¹•½¹±ä¥¸¡¥ÍÑ½Éä€¡¥¹±Õ‘•}¡¥ÍÑ½ÉäôÄ¤…¹…É”¹•Ù•È4(€€€ÁÉ•Í•¹Ñ•…ÌÕÉÉ•¹Ğ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¢ºÿ¦^»š¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€¥¹±Õ‘•}¡¥ÍÑ½Éä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰¥¹±Õ‘•}¡¥ÍÑ½Éäˆ¤€ôô€ˆÄˆ4(€€€Ù…±¥‘…Ñ¥½¸€ô}ÉÕ¹}Á¡…Í”İ}Ù…±¥‘…Ñ¥½¸¡‘É…™Ñ}É½Ü¤4(€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€ÕÉÉ•¹Ğ€ôµ…¹¥™•ÍÑ}ÍÙŒ¹•Ñ}ÕÉÉ•¹Ñ}µ…¹¥™•ÍĞ¡‘É…™Ñ}É½Ü°Ù…±¥‘…Ñ¥½¸¤4(€€€¡¥ÍÑ½Éä€ôµ…¹¥™•ÍÑ}ÍÙŒ¹•Ñ}µ…¹¥™•ÍÑ}¡¥ÍÑ½Éä¡‘É…™Ñ}¥¤¥˜¥¹±Õ‘•}¡¥ÍÑ½Éä•±Í”mt4(4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰‘É…™Ñ}¥ˆè‘É…™Ñ}¥°4(€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆè‘É…™Ñ}É½Ü¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ°€Ä¤°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè‘É…™Ñ}É½Ü¹•Ğ ‰ÍÑ…ÑÕÌˆ¤°4(€€€€€€€€‰Ù…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹ĞˆèÙ…±¥‘…Ñ¥½¸¹Ù…±¥‘…Ñ¥½¹}™¥¹•ÉÁÉ¥¹Ğ°4(€€€€€€€€‰ÕÉÉ•¹ĞˆèÕÉÉ•¹Ğ°4(€€€€€€€€‰¡¥ÍÑ½Éäˆè¡¥ÍÑ½Éä°4(€€€ô¤4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½µ…¹¥™•ÍĞ¼ñµ…¹¥™•ÍÑ}¥ø½…ÍÍ•Ğ¼ñ…ÍÍ•Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}…ÍÍ•Ñ}ÁÉ•Ù¥•Ü¡‘É…™Ñ}¥°µ…¹¥™•ÍÑ}¥°…ÍÍ•Ñ}¥¤è4(€€€€ˆˆ‰M•ÕÉ”ÁÉ•Á…É•…ÍÍ•ĞÁÉ•Ù¥•Ü€¡A¡…Í”€à¤¸4(4(€€€•ÁÑÌ=91d‘É…™Ñ}¥€¬µ…¹¥™•ÍÑ}¥€¬…ÍÍ•Ñ}¥€¡¹¼€ıÁ…Ñ ô¤¸M•ÉÙ•ÈÉ•Í½±Ù•Ì4(€€€Ù¥„°Ñ¡•¸•¹™½É•ÌQ}%H½¹™¥¹•µ•¹Ğ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€ÑÉäè4(€€€€€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€€€€€™Õ±±}Á…Ñ °½¹Ñ•¹Ñ}ÑåÁ”€ôµ…¹¥™•ÍÑ}ÍÙŒ¹•Ñ}…ÍÍ•Ğ¡‘É…™Ñ}¥°µ…¹¥™•ÍÑ}¥°…ÍÍ•Ñ}¥¤4(€€€•á•ÁĞ5…¹¥™•ÍÑ9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€•á•ÁĞ5…¹¥™•ÍÑÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡ÍÑÈ¡™Õ±±}Á…Ñ ¤°µ¥µ•ÑåÁ”õ½¹Ñ•¹Ñ}ÑåÁ”°…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°½¹‘¥Ñ¥½¹…°õQÉÕ”¤4(4(4)…ÁÀ¹‘•±•Ñ” ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½µ…¹¥™•ÍĞ¼ñµ…¹¥™•ÍÑ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}µ…¹¥™•ÍÑ}…¹•°¡‘É…™Ñ}¥°µ…¹¥™•ÍÑ}¥¤è4(€€€€ˆˆ‰…¹•°„€¡¹½¸µÉ•…‘ä¤5…¹¥™•ÍĞèµ…É¬…¹•±±•€¬‘•±•Ñ”¥ÑÌÍÑ…¥¹œ½¹±ä¸4(4(€€€=É¥¥¹…°]½É¬=É‘•ÈÁ¡½Ñ½Ì€¼A¡…Í”€Í•Ù¥‘•¹”€¼™½Éµ…°…ÑÑ…¡µ•¹ÑÌ…É”4(€€€¹•Ù•ÈÑ½Õ¡•¸I•ÅÕ¥É•ÌMI€¬…¹}ÁÉ•Á…É”€¡½¹™¥Éµ•°¹½¸µÉ•…µ½¹±ä¤¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}ÁÉ•Á…É•}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍĞ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšvšN7’ös¦f’îÛšâ–6T‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€µ…¹¥™•ÍÑ}ÍÙŒ€ô}…ÑÑ…¡µ•¹Ñ}µ…¹¥™•ÍÑ}Í•ÉÙ¥” ¤4(€€€€€€€µ…¹¥™•ÍĞ€ôµ…¹¥™•ÍÑ}ÍÙŒ¹…¹•°¡‘É…™Ñ}¥°µ…¹¥™•ÍÑ}¥¤4(€€€•á•ÁĞ5…¹¥™•ÍÑÉÉ½È…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸}µ…¹¥™•ÍÑ}•ÉÉ½É}É•ÍÁ½¹Í”¡•áŒ°‘•™…Õ±Ñ}ÍÑ…ÑÕÌôĞÀÀ¤4(4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰µ…¹¥™•ÍĞˆèµ…¹¥™•ÍÑô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½ÕÁ‘…Ñ”µİ½É­•Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÕÁ‘…Ñ•}İ½É­•È¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÁ‘…Ñ”İ½É­•ÈÑÉ…Ù•°¥¹™¼€¡½É¥¥¸°½Ù•É¹¥¡Ñ}ÍÑ…ä°ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸¤¸4(4(€€€QÉ¥•ÉÌÉ½ÕÑ”¥¹Ù…±¥‘…Ñ¥½¸€¡±•…ÉÌ½±µ¥±•…”½•Ù¥‘•¹”¤¸4(€€€I•ÅÕ¥É•ÌMIÑ½­•¸€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ó¾ò#¦r ‘É…™Ğƒ*Ûš’âSšr'ò[¢úGšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€ÕÍ•É}¥€ô‘…Ñ„¹•Ğ ‰ÕÍ•É}¥ˆ¤4(€€€€€€€½É¥¥¸€ô‘…Ñ„¹•Ğ ‰½É¥¥¸ˆ¤4(€€€€€€€½É¥¥¹}Í½ÕÉ”€ô‘…Ñ„¹•Ğ ‰½É¥¥¹}Í½ÕÉ”ˆ°€‰ÕÍ•É}¥¹ÁÕĞˆ¤4(€€€€€€€½Ù•É¹¥¡Ñ}ÍÑ…ä€ô‘…Ñ„¹•Ğ ‰½Ù•É¹¥¡Ñ}ÍÑ…äˆ¤4(€€€€€€€ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ô‘…Ñ„¹•Ğ ‰ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸ˆ°€‰Í•±™}‘É¥Ù”ˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€¥˜¹½ĞÕÍ•É}¥è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹òë–ÂDÕÍ•É}¥‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€€€€€€Œ¥¹…¹ÕÁ‘…Ñ”İ½É­•È4(€€€€€€€ÕÁ‘…Ñ•€ô…±Í”4(€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€¥˜Ü¹ÕÍ•É}¥€ôôÕÍ•É}¥è4(€€€€€€€€€€€€€€€¥˜½É¥¥¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¸€ô½É¥¥¸4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¹}Í½ÕÉ”€ô½É¥¥¹}Í½ÕÉ”4(€€€€€€€€€€€€€€€€€€€Ü¹½É¥¥¹}½¹™¥Éµ•€ôQÉÕ”¥˜½É¥¥¹}Í½ÕÉ”€ôô€‰ÕÍ•É}¥¹ÁÕĞˆ•±Í”Ü¹½É¥¥¹}½¹™¥Éµ•4(€€€€€€€€€€€€€€€¥˜½Ù•É¹¥¡Ñ}ÍÑ…ä¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€€€€€€€€€Ü¹½Ù•É¹¥¡Ñ}ÍÑ…ä€ô½Ù•É¹¥¡Ñ}ÍÑ…ä4(€€€€€€€€€€€€€€€¥˜ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€€€€€€€€€Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ•€ôQÉÕ”4(€€€€€€€€€€€€€€€€Œ%¹Ù…±¥‘…Ñ”É½ÕÑ”€¡±•…ÉÌ½±µ¥±•…”½•Ù¥‘•¹”¤€´ÍÑ…Ñ¥Œµ•Ñ¡½4(€€€€€€€€€€€€€€€QÉ…Ù•±M•ÉÙ¥”¹¥¹Ù…±¥‘…Ñ•}İ½É­•É}É½ÕÑ”¡Ü¤4(€€€€€€€€€€€€€€€‰É•…¬4(4(€€€€€€€¥˜¹½ĞÕÁ‘…Ñ•è4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–Ş—’ös’êë–Fc’â7–r£š¶É…™Ğƒ’â´‰ô¤°€ĞÀĞ4(4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€Œ•ĞÕÁ‘…Ñ•‘É…™Ñ}Ù•ÉÍ¥½¸™É½´‘…Ñ…‰…Í”4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°€‰µ•ÍÍ…”ˆè€‹–Ş—’ös’êë–Fc’ş‡š¿–ŞËšnÓšZÃ¾ò3¦3¢/–ŞË–’ÇšV#¦r¦7šZÃ¢º‡º\‰ô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4)…ÁÀ¹•Ğ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½ÍÑ…™˜ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÍÑ…™™}Í•…É  ¤è4(€€€€ˆˆ‰M•…É …Ñ¥Ù”¥¹Ñ•É¹…°ÍÑ…™˜Ñ¼…‘…ÌÉ…™Ğİ½É­•ÉÌ¸4(4(€€€I•…µ½¹±ä¸=¹±ä…‘µ¥¸½µ…¹…•È½•µÁ±½å•”…É”™¥•±ÍÑ…™˜…¹‘¥‘…Ñ•Ì¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€Ä€ô€¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€±¥­”€ô˜ˆ•íÅô”ˆ4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰M1P¥°¹…µ”°•µ…¥°°É½±”I=4ÕÍ•ÉÌ€ˆ4(€€€€€€€€‰]!I¥Í}…Ñ¥Ù”€ô€Ä9É½±”%8€ …‘µ¥¸œ°€µ…¹…•Èœ°€•µÁ±½å•”œ¤€ˆ4(€€€€€€€€‰9€¡¹…µ”1%-€ü=H•µ…¥°1%-€ü¤=IH	d¹…µ”1%5%P€ÈÀˆ°4(€€€€€€€€¡±¥­”°±¥­”¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€‰ÍÑ…™˜ˆèmì‰¥ˆèÉl‰¥‰t°€‰¹…µ”ˆèÉl‰¹…µ”‰t°€‰•µ…¥°ˆèÉl‰•µ…¥°‰t°€‰É½±”ˆèÉl‰É½±”‰uô™½ÈÈ¥¸É½İÍt°4(€€€ô¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½…‘µİ½É­•Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}…‘‘}İ½É­•È¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰‘…¸¥¹Ñ•É¹…°ÍÑ…™˜µ•µ‰•È…Ì„É…™Ğİ½É­•È¸4(4(€€€I•ÅÕ¥É•ÌMI€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸=¹±ä…±±½İ•½¸‘É…™ĞÍÑ…ÑÕÌ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ó¾ò#¦r ‘É…™Ğƒ*Ûš’âSšr'ò[¢úGšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€ÕÍ•É}¥€ô¥¹Ğ¡‘…Ñ„¹•Ğ ‰ÕÍ•É}¥ˆ¤¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€ÕÍ•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰M1P¥°¹…µ”°•µ…¥°°É½±”I=4ÕÍ•ÉÌ]!I¥€ô€ü9¥Í}…Ñ¥Ù”€ô€Äˆ°€¡ÕÍ•É}¥°¤4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½ĞÕÍ•Èè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹R£š"ß’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€¥˜ÕÍ•Él‰É½±”‰t¹½Ğ¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•Èˆ°€‰•µÁ±½å•”‰ôè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹¢¾—R£š"ß’â7šb¿–>¿šŞï–*ƒj–¦£–Fc–Ş”‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€€€€€¥˜…¹ä¡Ü¹ÕÍ•É}¥€ôôÕÍ•É}¥™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌ¤è4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹¢¾—–Ş—’ös’êë–Fc–ŞË–r É…™Ğƒ’â´‰ô¤°€ĞÀÀ4(€€€€€€€‘É…™Ğ¹İ½É­•ÉÌ¹…ÁÁ•¹ 4(€€€€€€€€€€€]½É­•ÉQÉ…Ù•°¡ÕÍ•É}¥õÕÍ•É}¥°¹…µ”õÕÍ•Él‰¹…µ”‰t°ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸ô‰Í•±™}‘É¥Ù”ˆ¤4(€€€€€€€€¤4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°€‰µ•ÍÍ…”ˆè˜‹–ŞËšŞï–*€íÕÍ•Él¹…µ”uô‰ô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½É•µ½Ù”µİ½É­•Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}É•µ½Ù•}İ½É­•È¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰I•µ½Ù”„İ½É­•È™É½´Ñ¡”É…™Ğ¸4(4(€€€I•ÅÕ¥É•ÌMI€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸=¹±ä…±±½İ•½¸‘É…™ĞÍÑ…ÑÕÌ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ó¾ò#¦r ‘É…™Ğƒ*Ûš’âSšr'ò[¢úGšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€ÕÍ•É}¥€ô¥¹Ğ¡‘…Ñ„¹•Ğ ‰ÕÍ•É}¥ˆ¤¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€€€€€É•µ½Ù•€ô…±Í”4(€€€€€€€™½È¤°Ü¥¸•¹Õµ•É…Ñ”¡‘É…™Ğ¹İ½É­•ÉÌ¤è4(€€€€€€€€€€€¥˜Ü¹ÕÍ•É}¥€ôôÕÍ•É}¥è4(€€€€€€€€€€€€€€€‘É…™Ğ¹İ½É­•ÉÌ¹Á½À¡¤¤4(€€€€€€€€€€€€€€€É•µ½Ù•€ôQÉÕ”4(€€€€€€€€€€€€€€€‰É•…¬4(€€€€€€€¥˜¹½ĞÉ•µ½Ù•è4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–Ş—’ös’êë–Fc’â7–r£š¶É…™Ğƒ’â´‰ô¤°€ĞÀĞ4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°€‰µ•ÍÍ…”ˆè€‹–ŞËï¦f“–Ş—’ös’êë–F`‰ô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½É•…±Õ±…Ñ”µµ¥±•…”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}É•…±Õ±…Ñ•}µ¥±•…”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰I•…±Õ±…Ñ”µ¥±•…”™½ÈÍ•±™}‘É¥Ù”İ½É­•ÉÌ…¹€¡É”¥•¹•É…Ñ”•Ù¥‘•¹”¸4(4(€€€I•ÕÍ•ÌÑ¡”Í…µ”Í•ÉÙ•ÈµÍ¥‘”A¡…Í”€È¼Í¼Í±½¥Œ…ÌÑ¡”¡…Ğ…Ñ¥½¸è4(€€€€´A¡…Í”€Èè‘•ÍÑ¥¹…Ñ¥½¸™É½´Í•ÉÙ¥•}½É‘•È¹Í¥Ñ•}…‘‘É•ÍÌ€¬ÑÉ…Ù•°™¥•±Ù•É¥™¥…Ñ¥½¸4(€€€€´A¡…Í”€Íè½½±”I½ÕÑ•Ìµ¥±•…”Ù¥„5¥±•…•M•ÉÙ¥”4(€€€€´A¡…Í”€ÍèMÑ…Ñ¥Œ5…ÁÌ•Ù¥‘•¹”Ù¥„5¥±•…•Ù¥‘•¹•M•ÉÙ¥”€¡Á•ÉÍ¥ÍÑ•Ñ¼É…™Ğ•Ù¥‘•¹•}É•½É‘Ì¤4(4(€€€I•ÅÕ¥É•ÌMI€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸½•Ì9=PÑ½Õ ™½Éµ…°É•Á½ÉÑÌ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ó¾ò#¦r ‘É…™Ğƒ*Ûš’âSšr'ò[¢úGšv¦fC¾ò$‰ô¤°€ĞÀÌ4(4(€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô9½¹”4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€¥˜‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡‘…Ñ…l‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(4(€€€€€€€€ŒA¡…Í”€Èè‘•ÍÑ¥¹…Ñ¥½¸™É½´Í•ÉÙ¥•}½É‘•ÈÍ¥Ñ•}…‘‘É•ÍÌ4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡‘É…™Ğ¹Í•ÉÙ¥•}½É‘•É}¥¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–Ş—–6W’â7–¶c–r ‰ô¤°€ĞÀĞ4(€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ€ô€¡½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t½È€ˆˆ¤¥˜½É‘•È•±Í”€ˆˆ4(€€€€€€€•µÁ}É•Í½±Ù•È€ôµÁ±½å••I•Í½±ÕÑ¥½¹M•ÉÙ¥”¡‘ˆ ¤°œ¹ÕÍ•Él‰¥‰t°œ¹ÕÍ•Él‰¹…µ”‰t¤4(€€€€€€€ÑÉ…Ù•±}ÍÙŒ€ôQÉ…Ù•±M•ÉÙ¥”¡‘ˆ ¤°•µÁ}É•Í½±Ù•È¤4(€€€€€€€ÑÉ…Ù•±}ÍÙŒ¹Í•Ñ}‘•ÍÑ¥¹…Ñ¥½¹}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ°Í¥Ñ•}…‘‘É•ÍÌ¤4(4(€€€€€€€€ŒY•É¥™äÑÉ…Ù•°™¥•±‘Ì™¥ÉÍĞ€¡Í…µ”…Ñ”…Ì¡…ĞA¡…Í”€È¤4(€€€€€€€µ¥ÍÍ¥¹œ°ÑÉ…Ù•±}µ•ÍÍ…•Ì€ôÑÉ…Ù•±}ÍÙŒ¹Ù•É¥™å}ÑÉ…Ù•±}™¥•±‘Ì 4(€€€€€€€€€€€‘É…™Ğ¹İ½É­•ÉÌ°‰½½°¡Í¥Ñ•}…‘‘É•ÍÌ¹ÍÑÉ¥À ¤¤4(€€€€€€€€¤4(€€€€€€€¥˜µ¥ÍÍ¥¹œè4(€€€€€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ôQÉÕ”4(€€€€€€€€€€€‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì€ô±¥ÍĞ¡Í•Ğ¡‘É…™Ğ¹Ù•É¥™¥…Ñ¥½¹}™¥•±‘Ì¤ğÍ•Ğ¡µ¥ÍÍ¥¹œ¤¤4(€€€€€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè€‹òë–ÂG–ş¢š–ë¢†3’ş‡š¿¾ò3š^ƒšÎW¢º‡º_¦3¢,ˆ°4(€€€€€€€€€€€€€€€€‰µ¥ÍÍ¥¹œˆèÍ½ÉÑ•¡Í•Ğ¡µ¥ÍÍ¥¹œ¤¤°4(€€€€€€€€€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°4(€€€€€€€€€€€ô¤°€ĞÈÈ4(4(€€€€€€€É½ÕÑ•Í}…Á¥}­•ä€ô•Ñ}½½±•}É½ÕÑ•Í}…Á¥}­•ä ¤4(€€€€€€€¥˜¹½ĞÉ½ÕÑ•Í}…Á¥}­•äè4(€€€€€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€„ô€‰ÍÕ•ÍÌˆè4(€€€€€€€€€€€€€€€€€€€Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€ô€‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆ4(€€€€€€€€€€€€€€€€€€€Ü¹É½ÕÑ•}•ÉÉ½È€ô€‰½½±”I½ÕÑ•ÌA$­•ä¹½Ğ½¹™¥ÕÉ•ˆ4(€€€€€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€€€€€‰½¬ˆè…±Í”°4(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè€‹šr7–*‡–f£šr«¦7ö¸½½±”I½ÕÑ•ÌA$­•ç¾ò3š^ƒšÎW¢º‡º_¦3¢,ˆ°4(€€€€€€€€€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°4(€€€€€€€€€€€ô¤°€ĞÈÈ4(4(€€€€€€€™É½´…¥}‘…¥±å}É•Á½ÉĞ¥µÁ½ÉĞ½½±•MÑ…Ñ¥5…ÁÍM•ÉÙ¥”°5¥±•…•Ù¥‘•¹•M•ÉÙ¥”4(4(€€€€€€€€ŒA¡…Í”€Íèµ¥±•…”Ù¥„½½±”I½ÕÑ•Ì4(€€€€€€€É½ÕÑ•Í}ÍÙŒ€ô½½±•I½ÕÑ•ÍM•ÉÙ¥”¡É½ÕÑ•Í}…Á¥}­•ä¤4(€€€€€€€µ¥±•…•}ÍÙŒ€ô5¥±•…•M•ÉÙ¥”¡É½ÕÑ•Í}ÍÙŒ¤4(€€€€€€€€ŒáÁ±¥¥Ñ±ä¥¹Ù…±¥‘…Ñ”…¡•É½ÕÑ•Ì™¥ÉÍĞèÑ¡¥Ì•¹‘Á½¥¹Ğ¥Ì„µ…¹Õ…°4(€€€€€€€€Œ€‰É•…±Õ±…Ñ”ˆÉ•ÅÕ•ÍĞ°Í¼Ñ¡”5¥±•…•M•ÉÙ¥”…¡”Õ…ÉµÕÍĞ¹½Ğ4(€€€€€€€€ŒÍ¡½ÉĞµ¥ÉÕ¥Ğİ¥Ñ ÍÑ…±”½É¥¥¸½½Ù•É¹¥¡Ğ‘…Ñ„¸4(€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆè4(€€€€€€€€€€€€€€€QÉ…Ù•±M•ÉÙ¥”¹¥¹Ù…±¥‘…Ñ•}İ½É­•É}É½ÕÑ”¡Ü¤4(€€€€€€€µ¥±•…•}ÍÙŒ¹…±Õ±…Ñ•}™½É}…±°¡‘É…™Ğ¹İ½É­•ÉÌ¤4(4(€€€€€€€€ŒA¡…Í”€Íè•Ù¥‘•¹”Ù¥„MÑ…Ñ¥Œ5…ÁÌ€¡Á•ÉÍ¥ÍĞÑ¼É…™Ğ•Ù¥‘•¹•}É•½É‘Ì¤4(€€€€€€€ÍÑ…Ñ¥}µ…ÁÍ}­•ä€ô•Ñ}½½±•}ÍÑ…Ñ¥}µ…ÁÍ}…Á¥}­•ä ¤4(€€€€€€€¥˜ÍÑ…Ñ¥}µ…ÁÍ}­•äè4(€€€€€€€€€€€•Ù¥‘•¹•}ÍÙŒ€ô5¥±•…•Ù¥‘•¹•M•ÉÙ¥” 4(€€€€€€€€€€€€€€€½½±•MÑ…Ñ¥5…ÁÍM•ÉÙ¥”¡ÍÑ…Ñ¥}µ…ÁÍ}­•ä¤°4(€€€€€€€€€€€€€€€½Ì¹Á…Ñ ¹©½¥¸¡Q}%H°€‰…¤µ‘…¥±äµÉ•Á½ÉĞµ‘É…™ÑÌˆ¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆ…¹Ü¹É½ÕÑ•}ÍÑ…ÑÕÌ€ôô€‰ÍÕ•ÍÌˆè4(€€€€€€€€€€€€€€€€€€€É•½É€ô•Ù¥‘•¹•}ÍÙŒ¹•¹•É…Ñ•}•Ù¥‘•¹” 4(€€€€€€€€€€€€€€€€€€€€€€€Ü°4(€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õ‘É…™Ğ¹Í•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ”õ‘É…™Ğ¹É•Á½ÉÑ}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€€€€€•¹•É…Ñ•‘}‰äõœ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ñ}•Ù¥‘•¹•}É•½É‘Ìõ‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì°4(€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì€ô5¥±•…•Ù¥‘•¹•M•ÉÙ¥”¹ÕÁÍ•ÉÑ}•Ù¥‘•¹•}É•½É 4(€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ğ¹•Ù¥‘•¹•}É•½É‘Ì°É•½É4(€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}•Ù¥‘•¹•}¥€ôÉ•½É¹•Ù¥‘•¹•}¥4(€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}•Ù¥‘•¹•}Á…Ñ €ôÉ•½É¹™¥±•}É•±…Ñ¥Ù•}Á…Ñ 4(€€€€€€€€€€€€€€€€€€€Ü¹µ¥±•…•}Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•€ôÉ•½É¹•Ù¥‘•¹•}ÍÑ…ÑÕÌ€ôô€‰Ù•É¥™¥…Ñ¥½¹}É•ÅÕ¥É•ˆ4(4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(4(€€€€€€€İ½É­•ÉÍ}½ÕĞ€ômt4(€€€€€€€™½ÈÜ¥¸‘É…™Ğ¹İ½É­•ÉÌè4(€€€€€€€€€€€¥˜Ü¹ÑÉ…¹ÍÁ½ÉÑ…Ñ¥½¸€ôô€‰Í•±™}‘É¥Ù”ˆè4(€€€€€€€€€€€€€€€İ½É­•ÉÍ}½ÕĞ¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€€€€€€€€€‰ÕÍ•É}¥ˆèÜ¹ÕÍ•É}¥°4(€€€€€€€€€€€€€€€€€€€€‰¹…µ”ˆèÜ¹¹…µ”°4(€€€€€€€€€€€€€€€€€€€€‰É½ÕÑ•}ÍÑ…ÑÕÌˆèÜ¹É½ÕÑ•}ÍÑ…ÑÕÌ°4(€€€€€€€€€€€€€€€€€€€€‰½¹•}İ…å}µ¥±•ÌˆèÜ¹½¹•}İ…å}µ¥±•Ì°4(€€€€€€€€€€€€€€€€€€€€‰É•Á½ÉÑ•‘}µ¥±•ÌˆèÜ¹É•Á½ÉÑ•‘}µ¥±•Ì°4(€€€€€€€€€€€€€€€€€€€€‰µ¥±•…•}•Ù¥‘•¹•}¥ˆèÜ¹µ¥±•…•}•Ù¥‘•¹•}¥°4(€€€€€€€€€€€€€€€€€€€€‰É½ÕÑ•}•ÉÉ½ÈˆèÜ¹É½ÕÑ•}•ÉÉ½È°4(€€€€€€€€€€€€€€€ô¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì4(€€€€€€€€€€€€‰½¬ˆèQÉÕ”°4(€€€€€€€€€€€€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°4(€€€€€€€€€€€€‰µ•ÍÍ…”ˆè€‹¦3¢/¢º‡º_–º3š"@ˆ°4(€€€€€€€€€€€€‰İ½É­•ÉÌˆèİ½É­•ÉÍ}½ÕĞ°4(€€€€€€€ô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½ÕÁ‘…Ñ”µ…ÉÉ¥Ù…°µ‘•Á…ÉÑÕÉ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÕÁ‘…Ñ•}…ÉÉ¥Ù…±}‘•Á…ÉÑÕÉ”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÁ‘…Ñ”…ÉÉ¥Ù…°½‘•Á…ÉÑÕÉ”Ñ¥µ•Ì€¡ÕÍ•È½Ù•ÉÉ¥‘”¤¸4(4(€€€M•ÑÌ…ÉÉ¥Ù…±}Ñ¥µ•}Í½ÕÉ”½‘•Á…ÉÑÕÉ•}Ñ¥µ•}Í½ÕÉ”€ôÕÍ•É}¥¹ÁÕĞ¸4(€€€I•ÅÕ¥É•ÌMI€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”€ô‘…Ñ„¹•Ğ ‰…ÉÉ¥Ù…±}Ñ¥µ”ˆ¤4(€€€€€€€‘•Á…ÉÑÕÉ•}Ñ¥µ”€ô‘…Ñ„¹•Ğ ‰‘•Á…ÉÑÕÉ•}Ñ¥µ”ˆ¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(€€€€€€€¥˜…ÉÉ¥Ù…±}Ñ¥µ”¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€‘É…™Ğ¹…ÉÉ¥Ù…±}Ñ¥µ”€ô…ÉÉ¥Ù…±}Ñ¥µ”4(€€€€€€€€€€€‘É…™Ğ¹…ÉÉ¥Ù…±}Ñ¥µ•}Í½ÕÉ”€ô€‰ÕÍ•É}¥¹ÁÕĞˆ4(€€€€€€€¥˜‘•Á…ÉÑÕÉ•}Ñ¥µ”¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€‘É…™Ğ¹‘•Á…ÉÑÕÉ•}Ñ¥µ”€ô‘•Á…ÉÑÕÉ•}Ñ¥µ”4(€€€€€€€€€€€‘É…™Ğ¹‘•Á…ÉÑÕÉ•}Ñ¥µ•}Í½ÕÉ”€ô€‰ÕÍ•É}¥¹ÁÕĞˆ4(4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰uô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½…¤½‘…¥±äµÉ•Á½ÉĞ½‘É…™Ğ¼ñ¥¹Ğé‘É…™Ñ}¥ø½ÕÁ‘…Ñ”µİ½É¬µ¥Ñ•´ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}ÕÁ‘…Ñ•}İ½É­}¥Ñ•´¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰UÁ‘…Ñ”İ½É¬¥Ñ•µÌ€¡…‘½ÕÁ‘…Ñ”½É•µ½Ù”¤¸4(4(€€€I•ÅÕ¥É•ÌMI€¬½ÁÑ¥µ¥ÍÑ¥Œ±½­¥¹œ¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv¦f@‰ô¤°€ĞÀÌ4(4(€€€É•ÅÕ¥É•}…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ˜ ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ’â7–¶c–r ‰ô¤°€ĞÀĞ4(4(€€€¥˜¹½Ğ…¹}•‘¥Ñ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšv’ş»šRçš¶É…™Ğ‰ô¤°€ĞÀÌ4(4(€€€ÑÉäè4(€€€€€€€‘…Ñ„€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€€€€€…Ñ¥½¸€ô‘…Ñ„¹•Ğ ‰…Ñ¥½¸ˆ°€‰É•Á±…”ˆ¤€€Œ…‘½ÕÁ‘…Ñ”½É•µ½Ù”½É•Á±…”4(€€€€€€€İ½É­}¥Ñ•µÌ€ô‘…Ñ„¹•Ğ ‰İ½É­}¥Ñ•µÌˆ°mt¤4(€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô‘…Ñ„¹•Ğ ‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆ¤4(€€€€€€€¥˜•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€•áÁ•Ñ•‘}Ù•ÉÍ¥½¸€ô¥¹Ğ¡•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^ƒšV#¢¾ßšÆ‰ô¤°€ĞÀÀ4(4(€€€ÑÉäè4(€€€€€€€‘É…™Ğ€ôÍÙŒ¹Á…ÉÍ•}‘É…™Ñ}‘…Ñ„¡‘É…™Ñ}É½Ü¤4(4(€€€€€€€¥˜…Ñ¥½¸€ôô€‰É•Á±…”ˆè4(€€€€€€€€€€€‘É…™Ğ¹İ½É­}¥Ñ•µÌ€ôİ½É­}¥Ñ•µÌ4(€€€€€€€•±¥˜…Ñ¥½¸€ôô€‰…‘ˆè4(€€€€€€€€€€€‘É…™Ğ¹İ½É­}¥Ñ•µÌ¹•áÑ•¹¡İ½É­}¥Ñ•µÌ¤4(€€€€€€€•±¥˜…Ñ¥½¸€ôô€‰ÕÁ‘…Ñ”ˆè4(€€€€€€€€€€€™½È¥Ñ•´¥¸İ½É­}¥Ñ•µÌè4(€€€€€€€€€€€€€€€™½È¤°•á¥ÍÑ¥¹œ¥¸•¹Õµ•É…Ñ”¡‘É…™Ğ¹İ½É­}¥Ñ•µÌ¤è4(€€€€€€€€€€€€€€€€€€€¥˜•á¥ÍÑ¥¹œ¹•Ğ ‰•ÅÕ¥Áµ•¹Ğˆ¤€ôô¥Ñ•´¹•Ğ ‰•ÅÕ¥Áµ•¹Ğˆ¤è4(€€€€€€€€€€€€€€€€€€€€€€€‘É…™Ğ¹İ½É­}¥Ñ•µÍm¥t€ô¥Ñ•´4(€€€€€€€€€€€€€€€€€€€€€€€‰É•…¬4(€€€€€€€•±¥˜…Ñ¥½¸€ôô€‰É•µ½Ù”ˆè4(€€€€€€€€€€€É•µ½Ù•}•ÅÕ¥Áµ•¹ÑÌ€ôí¥Ñ•´¹•Ğ ‰•ÅÕ¥Áµ•¹Ğˆ¤™½È¥Ñ•´¥¸İ½É­}¥Ñ•µÍô4(€€€€€€€€€€€‘É…™Ğ¹İ½É­}¥Ñ•µÌ€ômÜ™½ÈÜ¥¸‘É…™Ğ¹İ½É­}¥Ñ•µÌ¥˜Ü¹•Ğ ‰•ÅÕ¥Áµ•¹Ğˆ¤¹½Ğ¥¸É•µ½Ù•}•ÅÕ¥Áµ•¹ÑÍt4(4(€€€€€€€ÍÙŒ¹Í…Ù•}‘É…™Ğ¡‘É…™Ñ}¥°‘É…™Ğ°•áÁ•Ñ•‘}Ù•ÉÍ¥½¸õ•áÁ•Ñ•‘}Ù•ÉÍ¥½¸¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€ÕÁ‘…Ñ•‘}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰‘É…™Ñ}Ù•ÉÍ¥½¸ˆèÕÁ‘…Ñ•‘}É½İl‰‘É…™Ñ}Ù•ÉÍ¥½¸‰t°€‰İ½É­}¥Ñ•µÍ}½Õ¹Ğˆè±•¸¡‘É…™Ğ¹İ½É­}¥Ñ•µÌ¥ô¤4(€€€•á•ÁĞÉ…™ÑY•ÉÍ¥½¹½¹™±¥Ğè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‰É…™Ğƒ&#šr³–Ëª¾ò3¢¾ß–"ßšZÃ–B;¦7¢¾T‰ô¤°€ĞÀä4(4(4(ŒƒŠRŠRŠR A¡…Í”€ØèI•Ù¥•Ü•¹Ñ•ÈA…”I½ÕÑ•ÌƒŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠRŠR 4(4)…ÁÀ¹•Ğ ˆ½…¤µ‘…¥±äµÉ•Á½ÉĞ½‘É…™ÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™ÑÍ}Á…” ¤è4(€€€€ˆˆ‰I•Ù¥•Ü•¹Ñ•È€´É…™Ğ1¥ÍĞÁ…”¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰…¥}‘…¥±å}É•Á½ÉÑ}‘É…™ÑÌ¹¡Ñµ°ˆ°4(€€€€€€€ÍÉ™}Ñ½­•¸õ…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ™}Ñ½­•¸ ¤°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½…¤µ‘…¥±äµÉ•Á½ÉĞ½‘É…™ÑÌ¼ñ¥¹Ğé‘É…™Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}‘•Ñ…¥±}Á…”¡‘É…™Ñ}¥¤è4(€€€€ˆˆ‰I•Ù¥•Ü•¹Ñ•È€´É…™Ğ•Ñ…¥°½AÉ•Ù¥•ÜÁ…”¸ˆˆˆ4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€ÍÙŒ€ô}…¥}‘…¥±å}É•Á½ÉÑ}Í•ÉÙ¥” ¤4(€€€‘É…™Ñ}É½Ü€ôÍÙŒ¹•Ñ}‘É…™Ğ¡‘É…™Ñ}¥¤4(€€€¥˜¹½Ğ‘É…™Ñ}É½Üè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ğ¡œ¹ÕÍ•È°‘É…™Ñ}É½Ü¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰…¥}‘…¥±å}É•Á½ÉÑ}‘É…™Ñ}‘•Ñ…¥°¹¡Ñµ°ˆ°4(€€€€€€€‘É…™Ñ}¥õ‘É…™Ñ}¥°4(€€€€€€€ÍÉ™}Ñ½­•¸õ…¥}‘…¥±å}É•Á½ÉÑ}ÍÉ™}Ñ½­•¸ ¤°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÑÑ¥¹Ì½µ•¹ÔµÁ•Éµ¥ÍÍ¥½¹Ìˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)…‘µ¥¹}É•ÅÕ¥É•4)‘•˜µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ì ¤è4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€µ•¹Õ}­•åÌ€ôm¥Ñ•µl‰­•ä‰t™½ÈÉ½ÕÀ¥¸59U}AI5%MM%=9}I=UAL™½È¥Ñ•´¥¸É½ÕÁl‰¥Ñ•µÌ‰ut4(€€€€€€€…Ñ¥½¹}Á…¥ÉÌ€ôl4(€€€€€€€€€€€€¡¥Ñ•µl‰­•ä‰t°…Ñ¥½¹}­•ä¤4(€€€€€€€€€€€™½ÈÉ½ÕÀ¥¸I=1}Q%=9}AI5%MM%=9}I=UAL4(€€€€€€€€€€€™½È¥Ñ•´¥¸É½ÕÁl‰¥Ñ•µÌ‰t4(€€€€€€€€€€€™½È…Ñ¥½¹}­•ä¥¸¥Ñ•µl‰…Ñ¥½¹Ì‰t4(€€€€€€€t4(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ô¹½Ü ¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´É½±•}µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ìˆ¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´É½±•}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¹Ìˆ¤4(€€€€€€€™½ÈÉ½±”¥¸I=1}=AQ%=9Lè4(€€€€€€€€€€€™½Èµ•¹Õ}­•ä¥¸µ•¹Õ}­•åÌè4(€€€€€€€€€€€€€€€•¹…‰±•€ô€Ä¥˜É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰µ•¹Õ}}íÉ½±•õ}}íµ•¹Õ}­•åôˆ¤€ôô€ˆÄˆ•±Í”€À4(€€€€€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼É½±•}µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ì€ 4(€€€€€€€€€€€€€€€€€€€€€€€É½±”°µ•¹Õ}­•ä°¥Í}•¹…‰±•°ÕÁ‘…Ñ•‘}‰ä°ÕÁ‘…Ñ•‘}…Ğ4(€€€€€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€€€€€¡É½±”°µ•¹Õ}­•ä°•¹…‰±•°œ¹ÕÍ•Él‰¥‰t°Ñ¥µ•ÍÑ…µÀ¤°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€™½ÈÉ•Í½ÕÉ•}­•ä°…Ñ¥½¹}­•ä¥¸…Ñ¥½¹}Á…¥ÉÌè4(€€€€€€€€€€€€€€€•¹…‰±•€ô€Ä¥˜É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰…Ñ¥½¹}}íÉ½±•õ}}íÉ•Í½ÕÉ•}­•åõ}}í…Ñ¥½¹}­•åôˆ¤€ôô€ˆÄˆ•±Í”€À4(€€€€€€€€€€€€€€€¥˜…Ñ¥½¹}­•ä€ôô€‰Ù¥•Üˆ…¹É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰µ•¹Õ}}íÉ½±•õ}}íÉ•Í½ÕÉ•}­•åôˆ¤€ôô€ˆÄˆè4(€€€€€€€€€€€€€€€€€€€•¹…‰±•€ô€Ä4(€€€€€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼É½±•}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¹Ì€ 4(€€€€€€€€€€€€€€€€€€€€€€€É½±”°É•Í½ÕÉ•}­•ä°…Ñ¥½¹}­•ä°¥Í}•¹…‰±•°ÕÁ‘…Ñ•‘}‰ä°ÕÁ‘…Ñ•‘}…Ğ4(€€€€€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€€€€€¡É½±”°É•Í½ÕÉ•}­•ä°…Ñ¥½¹}­•ä°•¹…‰±•°œ¹ÕÍ•Él‰¥‰t°Ñ¥µ•ÍÑ…µÀ¤°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰ÕÁ‘…Ñ”ˆ°€‰µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ìˆ°9½¹”°€‹Îïîšv¦f@ˆ°€‹’ş»šRç¢>s–6W–J3šN7’ösšv¦f@ˆ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€œ¹}µ•¹Õ}Á•Éµ¥ÍÍ¥½¹}½Ù•ÉÉ¥‘•Ì€ôì4(€€€€€€€€€€€€¡É½±”°µ•¹Õ}­•ä¤èÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰µ•¹Õ}}íÉ½±•õ}}íµ•¹Õ}­•åôˆ¤€ôô€ˆÄˆ4(€€€€€€€€€€€™½ÈÉ½±”¥¸I=1}=AQ%=9L4(€€€€€€€€€€€™½Èµ•¹Õ}­•ä¥¸µ•¹Õ}­•åÌ4(€€€€€€€ô4(€€€€€€€œ¹}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¹}½Ù•ÉÉ¥‘•Ì€ôì4(€€€€€€€€€€€€¡É½±”°É•Í½ÕÉ•}­•ä°…Ñ¥½¹}­•ä¤è€ 4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰…Ñ¥½¹}}íÉ½±•õ}}íÉ•Í½ÕÉ•}­•åõ}}í…Ñ¥½¹}­•åôˆ¤€ôô€ˆÄˆ4(€€€€€€€€€€€€€€€½È€ 4(€€€€€€€€€€€€€€€€€€€…Ñ¥½¹}­•ä€ôô€‰Ù¥•Üˆ4(€€€€€€€€€€€€€€€€€€€…¹É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰µ•¹Õ}}íÉ½±•õ}}íÉ•Í½ÕÉ•}­•åôˆ¤€ôô€ˆÄˆ4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€¤4(€€€€€€€€€€€™½ÈÉ½±”¥¸I=1}=AQ%=9L4(€€€€€€€€€€€™½ÈÉ•Í½ÕÉ•}­•ä°…Ñ¥½¹}­•ä¥¸…Ñ¥½¹}Á…¥ÉÌ4(€€€€€€€ô4(€€€€€€€™±…Í  ‹šv¦fC¦7ö»–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ìˆ¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰µ•¹Õ}Á•Éµ¥ÍÍ¥½¹Ì¹¡Ñµ°ˆ°4(€€€€€€€É½±•}½ÁÑ¥½¹ÌõI=1}=AQ%=9L°4(€€€€€€€µ•¹Õ}É½ÕÁÌõ59U}AI5%MM%=9}I=UAL°4(€€€€€€€…Ñ¥½¹}É½ÕÁÌõI=1}Q%=9}AI5%MM%=9}I=UAL°4(€€€€€€€…Ñ¥½¹}±…‰•±ÌõQ%=9}1	1L°4(€€€€€€€Á•Éµ¥ÍÍ¥½¹}ÑÉ••}É½ÕÁÌõÁ•Éµ¥ÍÍ¥½¹}ÑÉ••}É½ÕÁÌ ¤°4(€€€€€€€ÕÍ•ÉÍ}‰å}É½±”õì4(€€€€€€€€€€€É½±”è‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•Ğ¥°¹…µ”°•µ…¥°°¥Í}…Ñ¥Ù”™É½´ÕÍ•ÉÌİ¡•É”É½±”€ô€ü½É‘•È‰ä¥Í}…Ñ¥Ù”‘•ÍŒ°¹…µ”ˆ°4(€€€€€€€€€€€€€€€€¡É½±”°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€€€€€€€€€™½ÈÉ½±”¥¸I=1}=AQ%=9L4(€€€€€€€ô°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÑÑ¥¹Ì½‘…Ñ…‰…Í”ˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘…Ñ…‰…Í•}½¹Í½±” ¤è4(€€€ÍÅ°€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÅ°ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆ•±Í”€ˆˆ4(€€€É•ÍÕ±Ğ€ô9½¹”4(€€€½±Õµ¹Ì€ômt4(€€€É½İÌ€ômt4(€€€É½İ}½Õ¹Ğ€ô9½¹”4(€€€ÍÑ…Ñ•µ•¹Ñ}­¥¹€ôÍÅ±}ÍÑ…Ñ•µ•¹Ñ}­¥¹¡ÍÅ°¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€¥˜¹½ĞÍÅ°è4(€€€€€€€€€€€™±…Í  ‹¢¾ß¢úO–”ME0ƒ¢¾·–>—ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€•±¥˜€ˆìˆ¥¸ÍÅ°¹ÉÍÑÉ¥À ˆìˆ¤è4(€€€€€€€€€€€™±…Í  ‹’âë’ê¦f7’ö;¢¾¿šN7’ös¦;¦f§¾ò3š¾?š²‡–>«¢÷š&Ÿ¢†3’âšv„ME0ƒ¢¾·–>—ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€É•…‘}ÅÕ•Éä€ô¥Í}É•…‘}ÍÅ°¡ÍÅ°¤4(€€€€€€€€€€€½¹™¥Éµ•€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰½¹™¥Éµ}İÉ¥Ñ”ˆ¤€ôô€ˆÄˆ4(€€€€€€€€€€€¥˜¹½ĞÉ•…‘}ÅÕ•Éä…¹¹½Ğ½¹™¥Éµ•è4(€€€€€€€€€€€€€€€™±…Í  ‹š&Ÿ¢†3šVÃš6»šnÓšRç¢¾·–>—–&7¾ò3¢¾ß–#–.û¦'†»¢º“ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ”¡ÍÅ°¤4(€€€€€€€€€€€€€€€€€€€¥˜ÕÉÍ½È¹‘•ÍÉ¥ÁÑ¥½¸è4(€€€€€€€€€€€€€€€€€€€€€€€½±Õµ¹Ì€ôm‘•ÍÉ¥ÁÑ¥½¹lÁt™½È‘•ÍÉ¥ÁÑ¥½¸¥¸ÕÉÍ½È¹‘•ÍÉ¥ÁÑ¥½¹t4(€€€€€€€€€€€€€€€€€€€€€€€É½İÌ€ôÕÉÍ½È¹™•Ñ¡µ…¹ä ÔÀÀ¤4(€€€€€€€€€€€€€€€€€€€€€€€É•ÍÕ±Ğ€ô€‰ÅÕ•Éäˆ4(€€€€€€€€€€€€€€€€€€€€€€€¥˜ÕÉÍ½È¹™•Ñ¡½¹” ¤¥Ì¹½Ğ9½¹”è4(€€€€€€€€€€€€€€€€€€€€€€€€€€€™±…Í  ‹š~—¢¾‹îOšzs¢Ú¢ş€ÔÀÀƒ¢†3¾ò3’îšbû’ë–&4€ÔÀÀƒ¢†3ˆ°€‰İ…É¹¥¹œˆ¤4(€€€€€€€€€€€€€€€€€€€€€€€¥˜¹½ĞÉ•…‘}ÅÕ•Éäè4(€€€€€€€€€€€€€€€€€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÅ°ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘…Ñ…‰…Í”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€9½¹”°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ME0½¹Í½±”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÅ±lèÔÀÁt°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€€€€€€€€€€€€€™±…Í  ‰ME0ƒ–ŞËš&Ÿ¢†3ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€€€€€€€€€É½İ}½Õ¹Ğ€ôÕÉÍ½È¹É½İ½Õ¹Ğ4(€€€€€€€€€€€€€€€€€€€€€€€É•ÍÕ±Ğ€ô€‰İÉ¥Ñ”ˆ4(€€€€€€€€€€€€€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÅ°ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘…Ñ…‰…Í”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€9½¹”°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ME0½¹Í½±”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÅ±lèÔÀÁt°4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€€€€€€€€€™±…Í  ‰ME0ƒ–ŞËš&Ÿ¢†3ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€€€€€•á•ÁĞÍÅ±¥Ñ”Ì¹ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€€€€€€€€€™±…Í ¡˜‰ME0ƒš&Ÿ¢†3–’Ç¢Ò—¾òií•ÉÉ½Éôˆ°€‰•ÉÉ½Èˆ¤4(€€€Ñ…‰±•Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ¹…µ”°ÑåÁ”4(€€€€€€€™É½´ÍÅ±¥Ñ•}µ…ÍÑ•È4(€€€€€€€İ¡•É”ÑåÁ”¥¸€ Ñ…‰±”œ°€Ù¥•Üœ¤…¹¹…µ”¹½Ğ±¥­”€ÍÅ±¥Ñ•|”œ4(€€€€€€€½É‘•È‰äÑåÁ”°¹…µ”4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ñ…‰±•}‘¥Ñ¥½¹…Éä€ômt4(€€€™½ÈÑ…‰±”¥¸Ñ…‰±•Ìè4(€€€€€€€ÅÕ½Ñ•‘}¹…µ”€ô€œˆœ€¬Ñ…‰±•l‰¹…µ”‰t¹É•Á±…” œˆœ°€œˆˆœ¤€¬€œˆœ4(€€€€€€€Ñ…‰±•}‘¥Ñ¥½¹…Éä¹…ÁÁ•¹ 4(€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€‰¹…µ”ˆèÑ…‰±•l‰¹…µ”‰t°4(€€€€€€€€€€€€€€€€‰ÑåÁ”ˆèÑ…‰±•l‰ÑåÁ”‰t°4(€€€€€€€€€€€€€€€€‰½±Õµ¹Ìˆè‘ˆ ¤¹•á•ÕÑ”¡˜‰ÁÉ…µ„Ñ…‰±•}¥¹™¼¡íÅÕ½Ñ•‘}¹…µ•ô¤ˆ¤¹™•Ñ¡…±° ¤°4(€€€€€€€€€€€ô4(€€€€€€€€¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰‘…Ñ…‰…Í•}½¹Í½±”¹¡Ñµ°ˆ°4(€€€€€€€ÍÅ°õÍÅ°°4(€€€€€€€ÍÑ…Ñ•µ•¹Ñ}­¥¹õÍÑ…Ñ•µ•¹Ñ}­¥¹°4(€€€€€€€É•ÍÕ±ĞõÉ•ÍÕ±Ğ°4(€€€€€€€½±Õµ¹Ìõ½±Õµ¹Ì°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€É½İ}½Õ¹ĞõÉ½İ}½Õ¹Ğ°4(€€€€€€€Ñ…‰±•ÌõÑ…‰±•Ì°4(€€€€€€€Ñ…‰±•}‘¥Ñ¥½¹…ÉäõÑ…‰±•}‘¥Ñ¥½¹…Éä°4(€€€€¤4(4(4)‘•˜•áÑÉ…Ñ}­¹½İ±•‘•}Á‘™}Ñ•áĞ¡Á‘™}Á…Ñ ¤è4(€€€ÑÉäè4(€€€€€€€É•…‘•È€ôA‘™I•…‘•È¡Á‘™}Á…Ñ °ÍÑÉ¥Ğõ…±Í”¤4(€€€€€€€Ñ•áÑ}Á…ÉÑÌ€ômt4(€€€€€€€Ñ•áÑ}±•¹Ñ €ô€À4(€€€€€€€™½ÈÁ…”¥¸É•…‘•È¹Á…•Ìè4(€€€€€€€€€€€Á…•}Ñ•áĞ€ô€¡Á…”¹•áÑÉ…Ñ}Ñ•áĞ ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€€€€€¥˜¹½ĞÁ…•}Ñ•áĞè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€É•µ…¥¹¥¹œ€ô-9=]1}5a}aQIQ}QaP€´Ñ•áÑ}±•¹Ñ 4(€€€€€€€€€€€¥˜É•µ…¥¹¥¹œ€ğô€Àè4(€€€€€€€€€€€€€€€‰É•…¬4(€€€€€€€€€€€Á…•}Ñ•áĞ€ôÁ…•}Ñ•áÑléÉ•µ…¥¹¥¹t4(€€€€€€€€€€€Ñ•áÑ}Á…ÉÑÌ¹…ÁÁ•¹¡Á…•}Ñ•áĞ¤4(€€€€€€€€€€€Ñ•áÑ}±•¹Ñ €¬ô±•¸¡Á…•}Ñ•áĞ¤€¬€Ä4(€€€€€€€É•ÑÕÉ¸€‰q¸ˆ¹©½¥¸¡Ñ•áÑ}Á…ÉÑÌ¥lé-9=]1}5a}aQIQ}QaQt4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€É•ÑÕÉ¸€ˆˆ4(4(4)‘•˜Í…Ù•}­¹½İ±•‘•}Á‘™}ÕÁ±½…¡ÕÁ±½…‘•¤è4(€€€¥˜¹½ĞÕÁ±½…‘•½È¹½ĞÕÁ±½…‘•¹™¥±•¹…µ”è4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¦'š.§¢š’â+’òƒjAƒšZš†ˆ¤4(€€€½É¥¥¹…±}™¥±•¹…µ”€ô½Ì¹Á…Ñ ¹‰…Í•¹…µ”¡ÕÁ±½…‘•¹™¥±•¹…µ”¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½Ğ½É¥¥¹…±}™¥±•¹…µ”¹±½İ•È ¤¹•¹‘Íİ¥Ñ  ˆ¹Á‘˜ˆ¤è4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹~—¢¾–êOn»–&7’îšR¿š2AƒšZš†ˆ¤4(€€€¡•…‘•È€ôÕÁ±½…‘•¹ÍÑÉ•…´¹É•… Ô¤4(€€€ÕÁ±½…‘•¹ÍÑÉ•…´¹Í••¬ À¤4(€€€¥˜¡•…‘•È€„ôˆˆ•A´ˆè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹šZ’îÛ––ºç’â7šb¿šr'šV#jAƒšZš†ˆ¤4(€€€ÍÑ½É•‘}™¥±•¹…µ”€ô˜‰íÍ•É•ÑÌ¹Ñ½­•¹}¡•à ÄØ¥ô¹Á‘˜ˆ4(€€€½Ì¹µ…­•‘¥ÉÌ¡-9=]1}	M}%H°•á¥ÍÑ}½¬õQÉÕ”¤4(€€€‘•ÍÑ¥¹…Ñ¥½¸€ô½Ì¹Á…Ñ ¹©½¥¸¡-9=]1}	M}%H°ÍÑ½É•‘}™¥±•¹…µ”¤4(€€€ÑÉäè4(€€€€€€€ÕÁ±½…‘•¹Í…Ù”¡‘•ÍÑ¥¹…Ñ¥½¸¤4(€€€€€€€™¥±•}Í¥é”€ô½Ì¹Á…Ñ ¹•ÑÍ¥é”¡‘•ÍÑ¥¹…Ñ¥½¸¤4(€€€€€€€¥˜™¥±•}Í¥é”€ø-9=]1}5a}A}	eQLè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰AƒšZš†’â7¢÷¢Ú¢ş€ÔÀ5ˆ¤4(€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€‰½É¥¥¹…±}™¥±•¹…µ”ˆè½É¥¥¹…±}™¥±•¹…µ”°4(€€€€€€€€€€€€‰ÍÑ½É•‘}™¥±•¹…µ”ˆèÍÑ½É•‘}™¥±•¹…µ”°4(€€€€€€€€€€€€‰‘•ÍÑ¥¹…Ñ¥½¸ˆè‘•ÍÑ¥¹…Ñ¥½¸°4(€€€€€€€€€€€€‰™¥±•}Í¥é”ˆè™¥±•}Í¥é”°4(€€€€€€€€€€€€‰•áÑÉ…Ñ•‘}Ñ•áĞˆè•áÑÉ…Ñ}­¹½İ±•‘•}Á‘™}Ñ•áĞ¡‘•ÍÑ¥¹…Ñ¥½¸¤°4(€€€€€€€ô4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½Ì¹É•µ½Ù”¡‘•ÍÑ¥¹…Ñ¥½¸¤4(€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€Á…ÍÌ4(€€€€€€€É…¥Í”4(4(4)‘•˜­¹½İ±•‘•}•áÁ¥Éå}™É½µ}™½É´ ¤è4(€€€Ù…±Õ”€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰•áÁ¥É•Í}½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½ĞÙ…±Õ”è4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡Ù…±Õ”¤¹¥Í½™½Éµ…Ğ ¤4(€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–"Ãšrš^—šrš‚ó–ò?š^ƒšV#ˆ¤™É½´•ÉÉ½È4(4(4)‘•˜­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤è4(€€€É•ÑÕÉ¸½Ì¹Á…Ñ ¹©½¥¸¡-9=]1}	M}%H°‘½Õµ•¹Ñl‰ÍÑ½É•‘}™¥±•¹…µ”‰t¤4(4(4)‘•˜­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´­¹½İ±•‘•}‘½Õµ•¹ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡‘½Õµ•¹Ñ}¥°¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ‘½Õµ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÑÕÉ¸‘½Õµ•¹Ğ4(4(4)‘•˜­¹½İ±•‘•}Ù•ÉÍ¥½¹}½É|ĞÀĞ¡Ù•ÉÍ¥½¹}¥¤è4(€€€Ù•ÉÍ¥½¸€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ìİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡Ù•ÉÍ¥½¹}¥°¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½ĞÙ•ÉÍ¥½¸è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÑÕÉ¸Ù•ÉÍ¥½¸4(4(4)‘•˜­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤è4(€€€É•ÑÕÉ¸½Ì¹Á…Ñ ¹©½¥¸¡-9=]1}	M}%H°Ù•ÉÍ¥½¹l‰ÍÑ½É•‘}™¥±•¹…µ”‰t¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½­¹½İ±•‘”µ‰…Í”ˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜­¹½İ±•‘•}‰…Í” ¤è4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€Í…Ù•€ôÍ…Ù•}­¹½İ±•‘•}Á‘™}ÕÁ±½…¡É•ÅÕ•ÍĞ¹™¥±•Ì¹•Ğ ‰‘½Õµ•¹Ğˆ¤¤4(€€€€€€€€€€€•áÁ¥É•Í}½¸€ô­¹½İ±•‘•}•áÁ¥Éå}™É½µ}™½É´ ¤4(€€€€€€€€€€€Ñ¥Ñ±”€ô€ 4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€€€€€€€€€½È½Ì¹Á…Ñ ¹ÍÁ±¥Ñ•áĞ¡Í…Ù•‘l‰½É¥¥¹…±}™¥±•¹…µ”‰t¥lÁt4(€€€€€€€€€€€€€€€½È€‹šr«–F÷–B7šZš†Œˆ4(€€€€€€€€€€€€¥lèÈÀÁt4(€€€€€€€€€€€…Ñ•½Éä€ô€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ•½Éäˆ°€ˆˆ¤¹ÍÑÉ¥À ¤½È€‹–Û’îXˆ¥lèàÁt4(€€€€€€€€€€€‘•ÍÉ¥ÁÑ¥½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¥lèÈÀÀÁt4(€€€€€€€€€€€¥Í}Á¥¹¹•€ô€Ä¥˜É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¥Í}Á¥¹¹•ˆ¤€ôô€ˆÄˆ•±Í”€À4(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ€ô¹½Ü ¤4(€€€€€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼­¹½İ±•‘•}‘½Õµ•¹ÑÌ€ 4(€€€€€€€€€€€€€€€€€€€Ñ¥Ñ±”°…Ñ•½Éä°‘•ÍÉ¥ÁÑ¥½¸°½É¥¥¹…±}™¥±•¹…µ”°ÍÑ½É•‘}™¥±•¹…µ”°4(€€€€€€€€€€€€€€€€€€€½¹Ñ•¹Ñ}ÑåÁ”°™¥±•}Í¥é”°ÕÁ±½…‘•‘}‰ä°ÕÁ±½…‘•‘}…Ğ°ÕÁ‘…Ñ•‘}…Ğ°4(€€€€€€€€€€€€€€€€€€€¥Í}Á¥¹¹•°•áÁ¥É•Í}½¸°Í•…É¡}Ñ•áĞ°Ñ•áÑ}¥¹‘•á•‘}…Ğ°ÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸4(€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€…ÁÁ±¥…Ñ¥½¸½Á‘˜œ°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€Ä¤4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€Ñ¥Ñ±”°4(€€€€€€€€€€€€€€€€€€€…Ñ•½Éä°4(€€€€€€€€€€€€€€€€€€€‘•ÍÉ¥ÁÑ¥½¸°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰ÍÑ½É•‘}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰™¥±•}Í¥é”‰t°4(€€€€€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€€€€€¥Í}Á¥¹¹•°4(€€€€€€€€€€€€€€€€€€€•áÁ¥É•Í}½¸°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰•áÑÉ…Ñ•‘}Ñ•áĞ‰t°4(€€€€€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘½Õµ•¹Ñ}¥€ôÕÉÍ½È¹±…ÍÑÉ½İ¥4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì€ 4(€€€€€€€€€€€€€€€€€€€‘½Õµ•¹Ñ}¥°Ù•ÉÍ¥½¹}¹Õµ‰•È°½É¥¥¹…±}™¥±•¹…µ”°ÍÑ½É•‘}™¥±•¹…µ”°4(€€€€€€€€€€€€€€€€€€€™¥±•}Í¥é”°•áÑÉ…Ñ•‘}Ñ•áĞ°¡…¹•}¹½Ñ”°ÕÁ±½…‘•‘}‰ä°ÕÁ±½…‘•‘}…Ğ4(€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€Ä°€ü°€ü°€ü°€ü°€Ÿ–"w–/&#šr°œ°€ü°€ü¤4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€‘½Õµ•¹Ñ}¥°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰ÍÑ½É•‘}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰™¥±•}Í¥é”‰t°4(€€€€€€€€€€€€€€€€€€€Í…Ù•‘l‰•áÑÉ…Ñ•‘}Ñ•áĞ‰t°4(€€€€€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€±½}…Ñ¥½¸ ‰É•…Ñ”ˆ°€‰­¹½İ±•‘•}‘½Õµ•¹Ğˆ°‘½Õµ•¹Ñ}¥°Ñ¥Ñ±”°˜‹–"Æï¾òií…Ñ•½Éåôˆ¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€¥˜€‰Í…Ù•ˆ¥¸±½…±Ì ¤è4(€€€€€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€€€€€½Ì¹É•µ½Ù”¡Í…Ù•‘l‰‘•ÍÑ¥¹…Ñ¥½¸‰t¤4(€€€€€€€€€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€€€€€€€€€Á…ÍÌ4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€¥˜€‰Í…Ù•ˆ¥¸±½…±Ì ¤è4(€€€€€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€€€€€½Ì¹É•µ½Ù”¡Í…Ù•‘l‰‘•ÍÑ¥¹…Ñ¥½¸‰t¤4(€€€€€€€€€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€€€€€€€€€Á…ÍÌ4(€€€€€€€€€€€É…¥Í”4(€€€€€€€™±…Í  ‹~—¢¾–êOšZš†–ŞË’â+’òƒˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(4(€€€­•åİ½É€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€…Ñ•½Éä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰…Ñ•½Éäˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€•áÁ¥Éå}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰•áÁ¥Éäˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜•áÁ¥Éå}ÍÑ…ÑÕÌ¹½Ğ¥¸ì‰•áÁ¥É•ˆ°€‰Í½½¸ˆ°€‰ÕÉÉ•¹Ğ‰ôè4(€€€€€€€•áÁ¥Éå}ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€Ñ½‘…å}Ù…±Õ”€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡…ÁÁ}Ñ¥µ•é½¹” ¤¤¹‘…Ñ” ¤4(€€€ÕÁ½µ¥¹}Ù…±Õ”€ôÑ½‘…å}Ù…±Õ”€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÌÀ¤4(€€€±…ÕÍ•Ì€ômt4(€€€Á…É…µÌ€ômt4(€€€¥˜­•åİ½Éè4(€€€€€€€Á…ÑÑ•É¸€ô˜ˆ•í­•åİ½É‘ô”ˆ4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆ¡­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹Ñ¥Ñ±”±¥­”€ü½È­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹‘•ÍÉ¥ÁÑ¥½¸±¥­”€ü€ˆ4(€€€€€€€€€€€€‰½È­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹½É¥¥¹…±}™¥±•¹…µ”±¥­”€ü½È­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹Í•…É¡}Ñ•áĞ±¥­”€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡mÁ…ÑÑ•É¸°Á…ÑÑ•É¸°Á…ÑÑ•É¸°Á…ÑÑ•É¹t¤4(€€€¥˜…Ñ•½Éäè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹…Ñ•½Éä€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡…Ñ•½Éä¤4(€€€¥˜•áÁ¥Éå}ÍÑ…ÑÕÌ€ôô€‰•áÁ¥É•ˆè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹•áÁ¥É•Í}½¸€ğ€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡Ñ½‘…å}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤¤4(€€€•±¥˜•áÁ¥Éå}ÍÑ…ÑÕÌ€ôô€‰Í½½¸ˆè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹•áÁ¥É•Í}½¸€øô€ü…¹­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹•áÁ¥É•Í}½¸€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡mÑ½‘…å}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤°ÕÁ½µ¥¹}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¥t¤4(€€€•±¥˜•áÁ¥Éå}ÍÑ…ÑÕÌ€ôô€‰ÕÉÉ•¹Ğˆè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆ¡­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹•áÁ¥É•Í}½¸¥Ì¹Õ±°½È­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹•áÁ¥É•Í}½¸€ø€ü¤ˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÕÁ½µ¥¹}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤¤4(€€€İ¡•É•}±…ÕÍ”€ô˜‰İ¡•É”ìœ…¹€œ¹©½¥¸¡±…ÕÍ•Ì¥ôˆ¥˜±…ÕÍ•Ì•±Í”€ˆˆ4(€€€‘½Õµ•¹ÑÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ­¹½İ±•‘•}‘½Õµ•¹ÑÌ¸¨°ÕÍ•ÉÌ¹¹…µ”…ÌÕÁ±½…‘•É}¹…µ”4(€€€€€€€™É½´­¹½İ±•‘•}‘½Õµ•¹ÑÌ4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹ÕÁ±½…‘•‘}‰ä4(€€€€€€€íİ¡•É•}±…ÕÍ•ô4(€€€€€€€½É‘•È‰ä­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹¥Í}Á¥¹¹•‘•ÍŒ°­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹ÕÁ‘…Ñ•‘}…Ğ‘•ÍŒ°­¹½İ±•‘•}‘½Õµ•¹ÑÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€…Ñ•½É¥•Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ‘¥ÍÑ¥¹Ğ…Ñ•½Éä™É½´­¹½İ±•‘•}‘½Õµ•¹ÑÌ½É‘•È‰ä…Ñ•½Éäˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ù•ÉÍ¥½¹}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì¸¨°ÕÍ•ÉÌ¹¹…µ”…ÌÕÁ±½…‘•É}¹…µ”4(€€€€€€€™É½´­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì¹ÕÁ±½…‘•‘}‰ä4(€€€€€€€½É‘•È‰ä­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì¹‘½Õµ•¹Ñ}¥°­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì¹Ù•ÉÍ¥½¹}¹Õµ‰•È‘•ÍŒ4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ù•ÉÍ¥½¹Í}‰å}‘½Õµ•¹Ğ€ôíô4(€€€™½ÈÙ•ÉÍ¥½¸¥¸Ù•ÉÍ¥½¹}É½İÌè4(€€€€€€€Ù•ÉÍ¥½¹Í}‰å}‘½Õµ•¹Ğ¹Í•Ñ‘•™…Õ±Ğ¡Ù•ÉÍ¥½¹l‰‘½Õµ•¹Ñ}¥‰t°mt¤¹…ÁÁ•¹¡Ù•ÉÍ¥½¸¤4(€€€­¹½İ±•‘•}µ•ÑÉ¥Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ½Õ¹Ğ ¨¤…ÌÑ½Ñ…°°4(€€€€€€€€€€€€€€ÍÕ´¡…Í”İ¡•¸•áÁ¥É•Í}½¸€ğ€üÑ¡•¸€Ä•±Í”€À•¹¤…Ì•áÁ¥É•°4(€€€€€€€€€€€€€€ÍÕ´¡…Í”İ¡•¸•áÁ¥É•Í}½¸€øô€ü…¹•áÁ¥É•Í}½¸€ğô€üÑ¡•¸€Ä•±Í”€À•¹¤…Ì•áÁ¥É¥¹}Í½½¸°4(€€€€€€€€€€€€€€ÍÕ´¡Ù¥•İ}½Õ¹Ğ¤…ÌÙ¥•İÌ°4(€€€€€€€€€€€€€€ÍÕ´¡‘½İ¹±½…‘}½Õ¹Ğ¤…Ì‘½İ¹±½…‘Ì4(€€€€€€€™É½´­¹½İ±•‘•}‘½Õµ•¹ÑÌ4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡Ñ½‘…å}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤°Ñ½‘…å}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤°ÕÁ½µ¥¹}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰­¹½İ±•‘•}‰…Í”¹¡Ñµ°ˆ°4(€€€€€€€‘½Õµ•¹ÑÌõ‘½Õµ•¹ÑÌ°4(€€€€€€€…Ñ•½É¥•ÌõmÉ½İl‰…Ñ•½Éä‰t™½ÈÉ½Ü¥¸…Ñ•½É¥•Ít°4(€€€€€€€­•åİ½Éõ­•åİ½É°4(€€€€€€€Í•±•Ñ•‘}…Ñ•½Éäõ…Ñ•½Éä°4(€€€€€€€Í•±•Ñ•‘}•áÁ¥Éäõ•áÁ¥Éå}ÍÑ…ÑÕÌ°4(€€€€€€€Ñ½‘…å}‘…Ñ”õÑ½‘…å}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€ÕÁ½µ¥¹}‘…Ñ”õÕÁ½µ¥¹}Ù…±Õ”¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€Ù•ÉÍ¥½¹Í}‰å}‘½Õµ•¹ĞõÙ•ÉÍ¥½¹Í}‰å}‘½Õµ•¹Ğ°4(€€€€€€€­¹½İ±•‘•}µ•ÑÉ¥Ìõ­¹½İ±•‘•}µ•ÑÉ¥Ì°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½­¹½İ±•‘”µ‰…Í”¼ñ¥¹Ğé‘½Õµ•¹Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}­¹½İ±•‘•}‘½Õµ•¹Ğ¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤4(€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹¥Í™¥±”¡­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌÍ•ĞÙ¥•İ}½Õ¹Ğ€ôÙ¥•İ}½Õ¹Ğ€¬€Äİ¡•É”¥€ô€üˆ°€¡‘½Õµ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ‘½Õµ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€½¹‘¥Ñ¥½¹…°õQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½­¹½İ±•‘”µ‰…Í”¼ñ¥¹Ğé‘½Õµ•¹Ñ}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}­¹½İ±•‘•}‘½Õµ•¹Ğ¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤4(€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹¥Í™¥±”¡­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌÍ•Ğ‘½İ¹±½…‘}½Õ¹Ğ€ô‘½İ¹±½…‘}½Õ¹Ğ€¬€Äİ¡•É”¥€ô€üˆ°€¡‘½Õµ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ‘½Õµ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€½¹‘¥Ñ¥½¹…°õQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½­¹½İ±•‘”µ‰…Í”¼ñ¥¹Ğé‘½Õµ•¹Ñ}¥ø½•‘¥Ğˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•‘¥Ñ}­¹½İ±•‘•}‘½Õµ•¹Ğ¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤4(€€€Ñ¥Ñ±”€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¥lèÈÀÁt4(€€€¥˜¹½ĞÑ¥Ñ±”è4(€€€€€€€™±…Í  ‹šZš†š‚¦Šc’â7¢÷’âë¦ëˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(€€€ÑÉäè4(€€€€€€€•áÁ¥É•Í}½¸€ô­¹½İ±•‘•}•áÁ¥Éå}™É½µ}™½É´ ¤4(€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(€€€…Ñ•½Éä€ô€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ•½Éäˆ°€ˆˆ¤¹ÍÑÉ¥À ¤½È€‹–Û’îXˆ¥lèàÁt4(€€€‘•ÍÉ¥ÁÑ¥½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¥lèÈÀÀÁt4(€€€¥Í}Á¥¹¹•€ô€Ä¥˜É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¥Í}Á¥¹¹•ˆ¤€ôô€ˆÄˆ•±Í”€À4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌ4(€€€€€€€Í•ĞÑ¥Ñ±”€ô€ü°…Ñ•½Éä€ô€ü°‘•ÍÉ¥ÁÑ¥½¸€ô€ü°¥Í}Á¥¹¹•€ô€ü°•áÁ¥É•Í}½¸€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡Ñ¥Ñ±”°…Ñ•½Éä°‘•ÍÉ¥ÁÑ¥½¸°¥Í}Á¥¹¹•°•áÁ¥É•Í}½¸°¹½Ü ¤°‘½Õµ•¹Ñ}¥¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰ÕÁ‘…Ñ”ˆ°€‰­¹½İ±•‘•}‘½Õµ•¹Ğˆ°‘½Õµ•¹Ñ}¥°Ñ¥Ñ±”°˜‹–:šZ’îÛ¾òií‘½Õµ•¹Ñl½É¥¥¹…±}™¥±•¹…µ”uôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹šZš†’ş‡š¿–ŞËšnÓšZÃˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½­¹½İ±•‘”µ‰…Í”¼ñ¥¹Ğé‘½Õµ•¹Ñ}¥ø½Ù•ÉÍ¥½¹Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÕÁ±½…‘}­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¸¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤4(€€€ÑÉäè4(€€€€€€€Í…Ù•€ôÍ…Ù•}­¹½İ±•‘•}Á‘™}ÕÁ±½…¡É•ÅÕ•ÍĞ¹™¥±•Ì¹•Ğ ‰‘½Õµ•¹Ğˆ¤¤4(€€€€€€€Ù•ÉÍ¥½¹}¹Õµ‰•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ½…±•Í”¡µ…à¡Ù•ÉÍ¥½¹}¹Õµ‰•È¤°€À¤€¬€Ä…ÌÙ…±Õ”™É½´­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ìİ¡•É”‘½Õµ•¹Ñ}¥€ô€üˆ°4(€€€€€€€€€€€€¡‘½Õµ•¹Ñ}¥°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¥l‰Ù…±Õ”‰t4(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ô¹½Ü ¤4(€€€€€€€¡…¹•}¹½Ñ”€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¡…¹•}¹½Ñ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¥lèÔÀÁt4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ì€ 4(€€€€€€€€€€€€€€€‘½Õµ•¹Ñ}¥°Ù•ÉÍ¥½¹}¹Õµ‰•È°½É¥¥¹…±}™¥±•¹…µ”°ÍÑ½É•‘}™¥±•¹…µ”°4(€€€€€€€€€€€€€€€™¥±•}Í¥é”°•áÑÉ…Ñ•‘}Ñ•áĞ°¡…¹•}¹½Ñ”°ÕÁ±½…‘•‘}‰ä°ÕÁ±½…‘•‘}…Ğ4(€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€‘½Õµ•¹Ñ}¥°4(€€€€€€€€€€€€€€€Ù•ÉÍ¥½¹}¹Õµ‰•È°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰ÍÑ½É•‘}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰™¥±•}Í¥é”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰•áÑÉ…Ñ•‘}Ñ•áĞ‰t°4(€€€€€€€€€€€€€€€¡…¹•}¹½Ñ”°4(€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€¤°4(€€€€€€€€¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌ4(€€€€€€€€€€€Í•Ğ½É¥¥¹…±}™¥±•¹…µ”€ô€ü°ÍÑ½É•‘}™¥±•¹…µ”€ô€ü°™¥±•}Í¥é”€ô€ü°Í•…É¡}Ñ•áĞ€ô€ü°4(€€€€€€€€€€€€€€€Ñ•áÑ}¥¹‘•á•‘}…Ğ€ô€ü°ÕÉÉ•¹Ñ}Ù•ÉÍ¥½¸€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€Í…Ù•‘l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰ÍÑ½É•‘}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰™¥±•}Í¥é”‰t°4(€€€€€€€€€€€€€€€Í…Ù•‘l‰•áÑÉ…Ñ•‘}Ñ•áĞ‰t°4(€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€Ù•ÉÍ¥½¹}¹Õµ‰•È°4(€€€€€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀ°4(€€€€€€€€€€€€€€€‘½Õµ•¹Ñ}¥°4(€€€€€€€€€€€€¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€‰ÕÁ‘…Ñ”ˆ°4(€€€€€€€€€€€€‰­¹½İ±•‘•}‘½Õµ•¹Ğˆ°4(€€€€€€€€€€€‘½Õµ•¹Ñ}¥°4(€€€€€€€€€€€‘½Õµ•¹Ñl‰Ñ¥Ñ±”‰t°4(€€€€€€€€€€€˜‹’â+’ò€ÙíÙ•ÉÍ¥½¹}¹Õµ‰•É÷¾òií¡…¹•}¹½Ñ”½ÈÍ…Ù•‘l½É¥¥¹…±}™¥±•¹…µ”uôˆ°4(€€€€€€€€¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€¥˜€‰Í…Ù•ˆ¥¸±½…±Ì ¤è4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€½Ì¹É•µ½Ù”¡Í…Ù•‘l‰‘•ÍÑ¥¹…Ñ¥½¸‰t¤4(€€€€€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€€€€€Á…ÍÌ4(€€€€€€€É…¥Í”4(€€€™±…Í ¡˜‹šZš†–ŞËšnÓšZÃ’âèÙíÙ•ÉÍ¥½¹}¹Õµ‰•É÷ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(4(4)…ÁÀ¹•Ğ ˆ½­¹½İ±•‘”µ‰…Í”½Ù•ÉÍ¥½¹Ì¼ñ¥¹ĞéÙ•ÉÍ¥½¹}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¸¡Ù•ÉÍ¥½¹}¥¤è4(€€€Ù•ÉÍ¥½¸€ô­¹½İ±•‘•}Ù•ÉÍ¥½¹}½É|ĞÀĞ¡Ù•ÉÍ¥½¹}¥¤4(€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹¥Í™¥±”¡­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌÍ•ĞÙ¥•İ}½Õ¹Ğ€ôÙ¥•İ}½Õ¹Ğ€¬€Äİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡Ù•ÉÍ¥½¹l‰‘½Õµ•¹Ñ}¥‰t°¤°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õÙ•ÉÍ¥½¹l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€½¹‘¥Ñ¥½¹…°õQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½­¹½İ±•‘”µ‰…Í”½Ù•ÉÍ¥½¹Ì¼ñ¥¹ĞéÙ•ÉÍ¥½¹}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¸¡Ù•ÉÍ¥½¹}¥¤è4(€€€Ù•ÉÍ¥½¸€ô­¹½İ±•‘•}Ù•ÉÍ¥½¹}½É|ĞÀĞ¡Ù•ÉÍ¥½¹}¥¤4(€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹¥Í™¥±”¡­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”­¹½İ±•‘•}‘½Õµ•¹ÑÌÍ•Ğ‘½İ¹±½…‘}½Õ¹Ğ€ô‘½İ¹±½…‘}½Õ¹Ğ€¬€Äİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡Ù•ÉÍ¥½¹l‰‘½Õµ•¹Ñ}¥‰t°¤°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õÙ•ÉÍ¥½¹l‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€½¹‘¥Ñ¥½¹…°õQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½­¹½İ±•‘”µ‰…Í”¼ñ¥¹Ğé‘½Õµ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}­¹½İ±•‘•}‘½Õµ•¹Ğ¡‘½Õµ•¹Ñ}¥¤è4(€€€‘½Õµ•¹Ğ€ô­¹½İ±•‘•}‘½Õµ•¹Ñ}½É|ĞÀĞ¡‘½Õµ•¹Ñ}¥¤4(€€€Ù•ÉÍ¥½¹}Á…Ñ¡Ì€ôì4(€€€€€€€­¹½İ±•‘•}Ù•ÉÍ¥½¹}Á…Ñ ¡Ù•ÉÍ¥½¸¤4(€€€€€€€™½ÈÙ•ÉÍ¥½¸¥¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•ĞÍÑ½É•‘}™¥±•¹…µ”™É½´­¹½İ±•‘•}‘½Õµ•¹Ñ}Ù•ÉÍ¥½¹Ìİ¡•É”‘½Õµ•¹Ñ}¥€ô€üˆ°4(€€€€€€€€€€€€¡‘½Õµ•¹Ñ}¥°¤°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€ô4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´­¹½İ±•‘•}‘½Õµ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡‘½Õµ•¹Ñ}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰‘•±•Ñ”ˆ°€‰­¹½İ±•‘•}‘½Õµ•¹Ğˆ°‘½Õµ•¹Ñ}¥°‘½Õµ•¹Ñl‰Ñ¥Ñ±”‰t°‘½Õµ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€Ù•ÉÍ¥½¹}Á…Ñ¡Ì¹…‘¡­¹½İ±•‘•}‘½Õµ•¹Ñ}Á…Ñ ¡‘½Õµ•¹Ğ¤¤4(€€€™½ÈÁ…Ñ ¥¸Ù•ÉÍ¥½¹}Á…Ñ¡Ìè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½Ì¹É•µ½Ù”¡Á…Ñ ¤4(€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€Á…ÍÌ4(€€€™±…Í  ‹~—¢¾–êOšZš†–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰­¹½İ±•‘•}‰…Í”ˆ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½½µÁ…¹äµ¥¹™¼ˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜½µÁ…¹å}¥¹™¼ ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆ…¹¹½Ğ…¹}µ…¹…•}½µÁ…¹å}¥¹™¼ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€™½È­•ä¥¸€ ‰¹…µ”ˆ°€‰…‘‘É•ÍÌˆ°€‰•µ…¥°ˆ°€‰Á¡½¹”ˆ°€‰É•¥ÍÑÉ…Ñ¥½¹}¹Õµ‰•Èˆ°€‰•¥¸ˆ¤è4(€€€€€€€€€€€Í•Ñ}Í•ÑÑ¥¹œ¡˜‰½µÁ…¹å}í­•åôˆ°É•ÅÕ•ÍĞ¹™½É´¹•Ğ¡˜‰½µÁ…¹å}í­•åôˆ°€ˆˆ¤¹ÍÑÉ¥À ¤¤4(€€€€€€€Ñ¥µ•é½¹•}¹…µ”€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…ÁÁ}Ñ¥µ•é½¹”ˆ°U1Q}Q%5i=9¤¹ÍÑÉ¥À ¤½ÈU1Q}Q%5i=94(€€€€€€€ÑÉäè4(€€€€€€€€€€€i½¹•%¹™¼¡Ñ¥µ•é½¹•}¹…µ”¤4(€€€€€€€•á•ÁĞi½¹•%¹™½9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€™±…Í  ‹Îïîš^Û–2ëš^ƒšV#¾ò3¢¾ß’öÿR£Æï’òğµ•É¥„½¡¥…¼ƒjš^Û–2ë–B7Ãˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰½µÁ…¹å}¥¹™¼ˆ¤¤4(€€€€€€€Í•Ñ}Í•ÑÑ¥¹œ ‰…ÁÁ}Ñ¥µ•é½¹”ˆ°Ñ¥µ•é½¹•}¹…µ”¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€™½ÈÕÁ±½…‘•¥¸ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤è4(€€€€€€€€€€€€€€€Í…Ù•}¹…µ•‘}…ÑÑ…¡µ•¹Ğ¡ÕÁ±½…‘•°=5A9e}QQ!59Q}%H°€‰½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌˆ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰½µÁ…¹å}¥¹™¼ˆ¤¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹–³–>ã’ş‡š¿–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰½µÁ…¹å}¥¹™¼ˆ¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰½µÁ…¹å}¥¹™¼¹¡Ñµ°ˆ°4(€€€€€€€½µÁ…¹äõ•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤°4(€€€€€€€Ñ¥µ•é½¹•}¹…µ”õ•Ñ}Ñ¥µ•é½¹•}¹…µ” ¤°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌ ¤°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½½µÁ…¹äµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}½µÁ…¹å}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€½Ì¹Á…Ñ ¹©½¥¸¡=5A9e}QQ!59Q}%H°…ÑÑ…¡µ•¹Ñl‰ÍÑ½É•‘}™¥±•¹…µ”‰t¤°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½½µÁ…¹äµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}½µÁ…¹å}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÑÕÉ¸Í…™•}…ÑÑ…¡µ•¹Ñ}É•ÍÁ½¹Í” 4(€€€€€€€½Ì¹Á…Ñ ¹©½¥¸¡=5A9e}QQ!59Q}%H°…ÑÑ…¡µ•¹Ñl‰ÍÑ½É•‘}™¥±•¹…µ”‰t¤°4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½½µÁ…¹äµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}½µÁ…¹å}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€ÑÉäè4(€€€€€€€½Ì¹É•µ½Ù”¡½Ì¹Á…Ñ ¹©½¥¸¡=5A9e}QQ!59Q}%H°…ÑÑ…¡µ•¹Ñl‰ÍÑ½É•‘}™¥±•¹…µ”‰t¤¤4(€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€Á…ÍÌ4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´½µÁ…¹å}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–³–>ã¦f’îÛ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰½µÁ…¹å}¥¹™¼ˆ¤¤4(4(4)…ÁÀ¹•Ğ ˆ½Í•ÑÑ¥¹Ì½½µÁ…¹äˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜±•…å}½µÁ…¹å}Í•ÑÑ¥¹Ì ¤è4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÍåÍÑ•µ}Í•ÑÑ¥¹Ìˆ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½¥¹Ù½¥•Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¥¹Ù½¥•Ì ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}¥¹Ù½¥•Ì ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Á…¥‘}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…¥‘}ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰İ½É­}½É‘•É}ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€¥˜İ½É­}½É‘•É}ÍÑ…ÑÕÌ¹½Ğ¥¸ìˆˆ°€‰½Á•¸ˆ°€‰±½Í•‰ôè4(€€€€€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€±…ÕÍ•Ì€ôm…•ÍÍ}±…ÕÍ•t4(€€€Á…É…µÌ€ô±¥ÍĞ¡…•ÍÍ}Á…É…µÌ¤4(€€€½É‘•É}¥‘Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡Ù…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰½É‘•É}¥ˆ¤¥˜Ù…±Õ”¤¤4(€€€Í¥Ñ•Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡Ù…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰Í¥Ñ”ˆ¤¥˜Ù…±Õ”¤¤4(€€€½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ”¡˜‰Í•±•Ğ‘¥ÍÑ¥¹ĞÍ•ÉÙ¥•}½É‘•ÉÌ¹¥°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”™É½´¥¹Ù½¥•Ì©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥õ¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥õ¥¹Ù½¥•Ì¹±¥•¹Ñ}¥İ¡•É”í…•ÍÍ}±…ÕÍ•ô½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È‘•ÍŒˆ°…•ÍÍ}Á…É…µÌ¤¹™•Ñ¡…±° ¤4(€€€™½È½±Õµ¸°Ù…±Õ•Ì¥¸l ‰Í•ÉÙ¥•}½É‘•ÉÌ¹¥ˆ°½É‘•É}¥‘Ì¤°€ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”ˆ°Í¥Ñ•Ì¥tè4(€€€€€€€¥˜Ù…±Õ•Ìè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰í½±Õµ¹ô¥¸€¡ìœ°œ¹©½¥¸ œüœ™½È|¥¸Ù…±Õ•Ì¥ô¤ˆ¤4(€€€€€€€€€€€Á…É…µÌ¹•áÑ•¹¡Ù…±Õ•Ì¤4(€€€¥˜İ½É­}½É‘•É}ÍÑ…ÑÕÌè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡İ½É­}½É‘•É}ÍÑ…ÑÕÌ¤4(€€€¥˜Á…¥‘}ÍÑ…ÑÕÌ€ôô€‰Á…¥ˆè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°ˆ¤4(€€€•±¥˜Á…¥‘}ÍÑ…ÑÕÌ€ôô€‰Õ¹Á…¥ˆè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹Õ±°ˆ¤4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ¥¹Ù½¥•Ì¸¨°±¥•¹ÑÌ¹¹…µ”…Ì±¥•¹Ñ}¹…µ”°±¥•¹ÑÌ¹Í¡½ÉÑ}¹…µ”…Ì±¥•¹Ñ}Í¡½ÉÑ}¹…µ”°4(€€€€€€€€€€€€€€±¥•¹ÑÌ¹±¥•¹Ñ}¹Õµ‰•È°ÕÍ•ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È…ÌÍ•ÉÙ¥•}½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ…ÌÍ•ÉÙ¥•}½É‘•É}ÍÑ…ÑÕÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È…ÌÍ•ÉÙ¥•}±¥•¹Ñ}½É‘•É}¹Õµ‰•È4(€€€€€€€™É½´¥¹Ù½¥•Ì4(€€€€€€€©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ô¥¹Ù½¥•Ì¹±¥•¹Ñ}¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô¥¹Ù½¥•Ì¹É•…Ñ•‘}‰ä4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ô¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”‘•ÍŒ°¥¹Ù½¥•Ì¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ñ½Ñ…±Ì€ôíÉ½İl‰¥‰tè¥¹Ù½¥•}Ñ½Ñ…±Ì¡É½İl‰¥‰t¤™½ÈÉ½Ü¥¸É½İÍô4(€€€ÍÕµµ…Éå}Ñ½Ñ…±Ì€ôíô4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€ÕÉÉ•¹ä€ôÉ½İl‰ÕÉÉ•¹ä‰t½È€‰UMˆ4(€€€€€€€ÍÕµµ…Éå}Ñ½Ñ…±ÍmÕÉÉ•¹åt€ôÍÕµµ…Éå}Ñ½Ñ…±Ì¹•Ğ¡ÕÉÉ•¹ä°€À¤€¬Ñ½Ñ…±ÍmÉ½İl‰¥‰uul‰Ñ½Ñ…°‰t4(€€€ÍÕµµ…Éå}Ñ½Ñ…±Ì€ô‘¥Ğ¡Í½ÉÑ•¡ÍÕµµ…Éå}Ñ½Ñ…±Ì¹¥Ñ•µÌ ¤¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰¥¹Ù½¥•Ì¹¡Ñµ°ˆ°4(€€€€€€€¥¹Ù½¥•ÌõÉ½İÌ°4(€€€€€€€Ñ½Ñ…±ÌõÑ½Ñ…±Ì°4(€€€€€€€ÍÕµµ…Éå}Ñ½Ñ…±ÌõÍÕµµ…Éå}Ñ½Ñ…±Ì°4(€€€€€€€±…‰•±ÌõMQQUM}1	1L°4(€€€€€€€½É‘•É}¥‘Ìõ½É‘•É}¥‘Ì°Í¥Ñ•ÌõÍ¥Ñ•Ì°½É‘•É}½ÁÑ¥½¹Ìõ½ÁÑ¥½¹Ì°4(€€€€€€€Í¥Ñ•}½ÁÑ¥½¹Ìõmì‰±¥•¹Ñ}¹…µ”ˆè¹…µ•ô™½È¹…µ”¥¸Í½ÉÑ•¡íÉ½İl‰±¥•¹Ñ}¹…µ”‰t™½ÈÉ½Ü¥¸½ÁÑ¥½¹Ì¥˜É½İl‰±¥•¹Ñ}¹…µ”‰uô¥t°4(€€€€€€€Á…¥‘}ÍÑ…ÑÕÌõÁ…¥‘}ÍÑ…ÑÕÌ°4(€€€€€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌõİ½É­}½É‘•É}ÍÑ…ÑÕÌ°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½¥¹Ù½¥•Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¥¹Ù½¥•}ÅÕ•Éä ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}¥¹Ù½¥•Ì ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€±¥•¹Ñ}Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰±¥•¹Ğˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€Í¡½ÉÑ}Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Í¡½ÉÑ}¹…µ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰İ½É­}½É‘•É}ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€¥˜İ½É­}½É‘•É}ÍÑ…ÑÕÌ¹½Ğ¥¸ìˆˆ°€‰½Á•¸ˆ°€‰±½Í•‰ôè4(€€€€€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€É•¥½¹}½‘”°½Õ¹ÑÉå}½‘”°½Õ¹ÑÉ¥•Ì°É•¥½¹Ì°±½…Ñ¥½¹}±…ÕÍ•Ì°±½…Ñ¥½¹}Á…É…µÌ€ôÉ•Á½ÉÑ}±½…Ñ¥½¹}™¥±Ñ•ÉÌ ¤4(€€€…Ù…¥±…‰±•}ÍÑ…ÑÕÍ•Ì€ôÍ•Ğ¡MQQUM}1	1L¹­•åÌ ¤¤4(€€€Í•±•Ñ•‘}ÍÑ…ÑÕÍ•Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰ÍÑ…ÑÕÌˆ¤¥˜Ù…±Õ”¥¸…Ù…¥±…‰±•}ÍÑ…ÑÕÍ•Ít4(€€€Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰Á…¥‘}ÍÑ…ÑÕÌˆ¤¥˜Ù…±Õ”¥¸ì‰Á…¥ˆ°€‰Õ¹Á…¥‰õt4(€€€ÁÉ½©•Ñ}½ÁÑ¥½¹Ì€ôÉ•Á½ÉÑ}ÁÉ½©•Ñ}½ÁÑ¥½¹Ì ¤4(€€€…Ù…¥±…‰±•}ÁÉ½©•Ñ}¥‘Ì€ôíÍÑÈ¡ÁÉ½©•Ñl‰¥‰t¤™½ÈÁÉ½©•Ğ¥¸ÁÉ½©•Ñ}½ÁÑ¥½¹Íô4(€€€Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰ÁÉ½©•Ñ}¥ˆ¤¥˜Ù…±Õ”¥¸…Ù…¥±…‰±•}ÁÉ½©•Ñ}¥‘Ít4(€€€•™™•Ñ¥Ù•}ÁÉ½©•Ñ}¥‘Ì€ôÍ•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ì½ÈmÍÑÈ¡ÁÉ½©•Ñl‰¥‰t¤™½ÈÁÉ½©•Ğ¥¸ÁÉ½©•Ñ}½ÁÑ¥½¹Ít4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€±…ÕÍ•Ì€ôm…•ÍÍ}±…ÕÍ•t4(€€€Á…É…µÌ€ô±¥ÍĞ¡…•ÍÍ}Á…É…µÌ¤4(€€€±…ÕÍ•Ì¹•áÑ•¹¡±½…Ñ¥½¹}±…ÕÍ•Ì¤4(€€€Á…É…µÌ¹•áÑ•¹¡±½…Ñ¥½¹}Á…É…µÌ¤4(€€€¥˜İ½É­}½É‘•É}ÍÑ…ÑÕÌè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡İ½É­}½É‘•É}ÍÑ…ÑÕÌ¤4(€€€¥˜Í•±•Ñ•‘}ÍÑ…ÑÕÍ•Ìè4(€€€€€€€Á±…•¡½±‘•ÉÌ€ô€ˆ°ˆ¹©½¥¸ ˆüˆ™½È|¥¸Í•±•Ñ•‘}ÍÑ…ÑÕÍ•Ì¤4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ¥¸€¡íÁ±…•¡½±‘•ÉÍô¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡Í•±•Ñ•‘}ÍÑ…ÑÕÍ•Ì¤4(€€€•±Í”è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œˆ¤4(€€€¥˜Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•Ì…¹±•¸¡Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•Ì¤€ğ€Èè4(€€€€€€€¥˜Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•ÍlÁt€ôô€‰Á…¥ˆè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°ˆ¤4(€€€€€€€•±¥˜Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•ÍlÁt€ôô€‰Õ¹Á…¥ˆè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹Õ±°ˆ¤4(€€€¥˜±¥•¹Ñ}Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆ¡±¥•¹ÑÌ¹¹…µ”±¥­”€ü½È±¥•¹ÑÌ¹±¥•¹Ñ}¹Õµ‰•È±¥­”€ü¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•í±¥•¹Ñ}Åô”ˆ°˜ˆ•í±¥•¹Ñ}Åô”‰t¤4(€€€¥˜Í¡½ÉÑ}Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰±¥•¹ÑÌ¹Í¡½ÉÑ}¹…µ”±¥­”€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡˜ˆ•íÍ¡½ÉÑ}Åô”ˆ¤4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€¥˜•™™•Ñ¥Ù•}ÁÉ½©•Ñ}¥‘Ìè4(€€€€€€€Á±…•¡½±‘•ÉÌ€ô€ˆ°ˆ¹©½¥¸ ˆüˆ™½È|¥¸•™™•Ñ¥Ù•}ÁÉ½©•Ñ}¥‘Ì¤4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥¥¸€¡íÁ±…•¡½±‘•ÉÍô¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡•™™•Ñ¥Ù•}ÁÉ½©•Ñ}¥‘Ì¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ¥¹Ù½¥•Ì¹¥°¥¹Ù½¥•Ì¹¥¹Ù½¥•}¹Õµ‰•È°¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”°¥¹Ù½¥•Ì¹ÕÉÉ•¹ä°¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ°¥¹Ù½¥•Ì¹Á…¥‘}…Ğ°4(€€€€€€€€€€€€€€±¥•¹ÑÌ¹±¥•¹Ñ}¹Õµ‰•È°±¥•¹ÑÌ¹¹…µ”…Ì±¥•¹Ñ}¹…µ”°±¥•¹ÑÌ¹Í¡½ÉÑ}¹…µ”°4(€€€€€€€€€€€€€€¥¹Ù½¥•}¥Ñ•µÌ¹‘•ÍÉ¥ÁÑ¥½¸…ÌÁÉ½©•Ñ}¹…µ”°¥¹Ù½¥•}¥Ñ•µÌ¹…µ½Õ¹Ğ°¥¹Ù½¥•}¥Ñ•µÌ¹Ñ…á}É…Ñ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ…ÌÍ•ÉÙ¥•}½É‘•É}ÍÑ…ÑÕÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È…ÌÍ•ÉÙ¥•}±¥•¹Ñ}½É‘•É}¹Õµ‰•È4(€€€€€€€™É½´¥¹Ù½¥•}¥Ñ•µÌ4(€€€€€€€©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥4(€€€€€€€©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ô¥¹Ù½¥•Ì¹±¥•¹Ñ}¥4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ô¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”‘•ÍŒ°¥¹Ù½¥•Ì¹¥‘•ÍŒ°¥¹Ù½¥•}¥Ñ•µÌ¹¥…ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•Á½ÉÑ}É½İÌ€ômt4(€€€ÍÕ‰Ñ½Ñ…°€ôÑ…á}Ñ½Ñ…°€ôÉ…¹‘}Ñ½Ñ…°€ô€À4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€…µ½Õ¹Ğ€ô™±½…Ğ¡É½İl‰…µ½Õ¹Ğ‰t½È€À¤4(€€€€€€€Ñ…à€ô…µ½Õ¹Ğ€¨™±½…Ğ¡É½İl‰Ñ…á}É…Ñ”‰t½È€À¤€¼€ÄÀÀ4(€€€€€€€±¥¹•}Ñ½Ñ…°€ô…µ½Õ¹Ğ€¬Ñ…à4(€€€€€€€ÍÕ‰Ñ½Ñ…°€¬ô…µ½Õ¹Ğ4(€€€€€€€Ñ…á}Ñ½Ñ…°€¬ôÑ…à4(€€€€€€€É…¹‘}Ñ½Ñ…°€¬ô±¥¹•}Ñ½Ñ…°4(€€€€€€€É•Á½ÉÑ}É½İÌ¹…ÁÁ•¹¡ì‰É½ÜˆèÉ½Ü°€‰Ñ…àˆèÑ…à°€‰±¥¹•}Ñ½Ñ…°ˆè±¥¹•}Ñ½Ñ…±ô¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰É•Á½ÉÑÌ¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ•Á½ÉÑ}É½İÌ°4(€€€€€€€ÁÉ½©•Ñ}½ÁÑ¥½¹ÌõÁÉ½©•Ñ}½ÁÑ¥½¹Ì°4(€€€€€€€Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ìõ•™™•Ñ¥Ù•}ÁÉ½©•Ñ}¥‘Ì°4(€€€€€€€±¥•¹Ñ}Äõ±¥•¹Ñ}Ä°4(€€€€€€€Í¡½ÉÑ}ÄõÍ¡½ÉÑ}Ä°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€İ½É­}½É‘•É}ÍÑ…ÑÕÌõİ½É­}½É‘•É}ÍÑ…ÑÕÌ°4(€€€€€€€Í•±•Ñ•‘}ÍÑ…ÑÕÍ•ÌõÍ•±•Ñ•‘}ÍÑ…ÑÕÍ•Ì½ÈmÙ…±Õ”™½ÈÙ…±Õ”¥¸MQQUM}1	1L¥˜Ù…±Õ”€„ô€‰Ù½¥‰t°4(€€€€€€€Í•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•ÌõÍ•±•Ñ•‘}Á…¥‘}ÍÑ…ÑÕÍ•Ì½Èl‰Á…¥ˆ°€‰Õ¹Á…¥‰t°4(€€€€€€€ÍÕ‰Ñ½Ñ…°õÍÕ‰Ñ½Ñ…°°4(€€€€€€€Ñ…á}Ñ½Ñ…°õÑ…á}Ñ½Ñ…°°4(€€€€€€€É…¹‘}Ñ½Ñ…°õÉ…¹‘}Ñ½Ñ…°°4(€€€€€€€±…‰•±ÌõMQQUM}1	1L°4(€€€€€€€½Õ¹ÑÉ¥•Ìõ½Õ¹ÑÉ¥•Ì°4(€€€€€€€É•¥½¹ÌõÉ•¥½¹Ì°4(€€€€€€€É•¥½¹}½‘”õÉ•¥½¹}½‘”°4(€€€€€€€½Õ¹ÑÉå}½‘”õ½Õ¹ÑÉå}½‘”°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•Á½ÉÑ}•¹Ñ•È ¤è4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}ÅÕ•Éäˆ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½Í•ÉÙ¥”µ½É‘•ÉÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}½É‘•É}ÅÕ•Éä ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€½É‘•É}¹Õµ‰•È€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰½É‘•É}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€Í¥Ñ•}¹…µ”€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Í¥Ñ•}¹…µ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆ¡Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü½È½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È±¥­”€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”‰t¤4(€€€¥˜½É‘•É}¹Õµ‰•Èè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡˜ˆ•í½É‘•É}¹Õµ‰•Éô”ˆ¤4(€€€¥˜Í¥Ñ•}¹…µ”è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡˜ˆ•íÍ¥Ñ•}¹…µ•ô”ˆ¤4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÉÑ}‘…Ñ”€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÉÑ}‘…Ñ”€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€¥˜ÍÑ…ÑÕÌè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÍÑ…ÑÕÌ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¸¨°4(€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ì¹¹…µ”…Ìİ½É­}½É‘•É}ÑåÁ•}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€½…±•Í”¡½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È¤…Ì‰Õå•É}•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹É•¥½¹}¹…µ”°½Õ¹ÑÉå}é ¹É•¥½¹}¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹É•¥½¹}½‘”¤…ÌÉ•¥½¹}¹…µ”°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹ĞÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥¤…ÌÉ•Á½ÉÑ}½Õ¹Ğ°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹Ğ¥¹Ù½¥•Ì¹¥¤…Ì¥¹Ù½¥•}½Õ¹Ğ°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹Ğ…Í”İ¡•¸¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°Ñ¡•¸¥¹Ù½¥•Ì¹¥•¹¤…ÌÁ…¥‘}¥¹Ù½¥•}½Õ¹Ğ°4(€€€€€€€€€€€€€€½…±•Í”¡ÍÕ´¡…Í”İ¡•¸•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œÑ¡•¸•áÁ•¹Í•Ì¹…µ½Õ¹Ğ•±Í”€À•¹¤°€À¤…Ì…ÁÁÉ½Ù•‘}•áÁ•¹Í•}Ñ½Ñ…°4(€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€±•™Ğ©½¥¸İ½É­}½É‘•É}ÑåÁ•Ì½¸İ½É­}½É‘•É}ÑåÁ•Ì¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹İ½É­}½É‘•É}ÑåÁ•}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸µ…¹Õ™…ÑÕÉ•ÉÌ…Ì½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ½¸½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹µ…¹Õ™…ÑÕÉ•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌ½¸Í•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€±•™Ğ©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥…¹¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€±•™Ğ©½¥¸•áÁ•¹Í•Ì½¸•áÁ•¹Í•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹É•…Ñ•‘}…Ğ‘•ÍŒ°Í•ÉÙ¥•}½É‘•ÉÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€mÕÉÉ•¹Ñ}±…¹Õ…” ¤°€©Á…É…µÍt°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}ÅÕ•Éä¹¡Ñµ°ˆ°4(€€€€€€€½É‘•ÉÌõÉ½İÌ°4(€€€€€€€ÄõÄ°4(€€€€€€€½É‘•É}¹Õµ‰•Èõ½É‘•É}¹Õµ‰•È°4(€€€€€€€Í¥Ñ•}¹…µ”õÍ¥Ñ•}¹…µ”°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€ÍÑ…ÑÕÌõÍÑ…ÑÕÌ°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½‰Õå•ÉÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‰Õå•É}ÅÕ•Éä ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€É•¥½¹}½‘”°½Õ¹ÑÉå}½‘”°½Õ¹ÑÉ¥•Ì°É•¥½¹Ì°|°|€ôÉ•Á½ÉÑ}±½…Ñ¥½¹}™¥±Ñ•ÉÌ ¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€¡‰Õå•ÉÌ¹‰Õå•É}¹Õµ‰•È±¥­”€ü½È‰Õå•ÉÌ¹¹…µ”±¥­”€ü½È½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤±¥­”€ü½È‰Õå•ÉÌ¹½¹Ñ…Ñ}¹…µ”±¥­”€ü4(€€€€€€€€€€€€½È‰Õå•ÉÌ¹½¹Ñ…Ñ}‘•Ñ…¥±Ì±¥­”€ü½È‰Õå•ÉÌ¹•µ…¥°±¥­”€ü½È‰Õå•ÉÌ¹‘•Ñ…¥±•‘}…‘‘É•ÍÌ±¥­”€ü4(€€€€€€€€€€€€½È‰Õå•ÉÌ¹•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È±¥­”€ü½È‰Õå•ÉÌ¹Í¥Ñ•}Í¥é”±¥­”€ü¤4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”‰t€¨€ä¤4(€€€¥˜É•¥½¹}½‘”è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•á¥ÍÑÌ€¡Í•±•Ğ€Ä™É½´½Õ¹ÑÉ¥•Ìİ¡•É”½Õ¹ÑÉ¥•Ì¹½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉ¥•Ì¹É•¥½¹}½‘”€ô€ü¤ˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡É•¥½¹}½‘”¤4(€€€¥˜½Õ¹ÑÉå}½‘”è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡½Õ¹ÑÉå}½‘”¤4(€€€É½İÌ€ô‰Õå•É}µ…Á}É½İÌ ˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¤°Á…É…µÌ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰‰Õå•É}ÅÕ•Éä¹¡Ñµ°ˆ°4(€€€€€€€‰Õå•ÉÌõÉ½İÌ°4(€€€€€€€½Õ¹ÑÉ¥•Ìõ½Õ¹ÑÉ¥•Ì°4(€€€€€€€É•¥½¹ÌõÉ•¥½¹Ì°4(€€€€€€€ÄõÄ°4(€€€€€€€É•¥½¹}½‘”õÉ•¥½¹}½‘”°4(€€€€€€€½Õ¹ÑÉå}½‘”õ½Õ¹ÑÉå}½‘”°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½Í•ÉÙ¥”µÉ•Á½ÉÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}É•Á½ÉÑ}ÅÕ•Éä ¤è4(€€€€ŒY¥•Ü…•ÍÌ¥Ì‘É¥Ù•¸‰äÑ¡”Á•Éµ¥ÍÍ¥½¸µ…ÑÉ¥à€£–Ş—’ösš^—š*”€øƒš~—r,¤¥¹ÍÑ•…½˜4(€€€€Œ…¸¥¹Ñ•É¹…°µ½¹±ä…Ñ”°Í¼•áÑ•É¹…°…½Õ¹ÑÌÉ…¹Ñ•ƒš~—r,…¸ÕÍ”Ñ¡¥ÌÁ…”¸4(€€€¥˜¹½Ğ€¡¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤½È¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Í•ÉÙ¥•}É•Á½ÉÑÌˆ°€‰Ù¥•Üˆ¤¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€½É‘•É}¥‘Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰½É‘•É}¥ˆ¤¤¤4(€€€Í¥Ñ•Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰Í¥Ñ”ˆ¤¤¤4(€€€İ½É­•É}¥‘Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰İ½É­•É}¥ˆ¤¤¤4(€€€½É‘•É}¥‘Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸½É‘•É}¥‘Ì¥˜Ù…±Õ•t4(€€€Í¥Ñ•Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸Í¥Ñ•Ì¥˜Ù…±Õ•t4(€€€İ½É­•É}¥‘Ì€ômÙ…±Õ”™½ÈÙ…±Õ”¥¸İ½É­•É}¥‘Ì¥˜Ù…±Õ•t4(€€€±…ÕÍ•Ì°Á…É…µÌ€ôÍ•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤4(€€€¥˜¹½Ğ±…ÕÍ•Ìè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆÄ€ô€Äˆ¤4(€€€™½È½±Õµ¸°Ù…±Õ•Ì¥¸l ‰Í•ÉÙ¥•}½É‘•ÉÌ¹¥ˆ°½É‘•É}¥‘Ì¤°€ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”ˆ°Í¥Ñ•Ì¥tè4(€€€€€€€¥˜Ù…±Õ•Ìè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰í½±Õµ¹ô¥¸€¡ìœ°œ¹©½¥¸ œüœ™½È|¥¸Ù…±Õ•Ì¥ô¤ˆ¤4(€€€€€€€€€€€Á…É…µÌ¹•áÑ•¹¡Ù…±Õ•Ì¤4(€€€¥˜İ½É­•É}¥‘Ìè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰•á¥ÍÑÌ€¡Í•±•Ğ€Ä™É½´Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌµ…Ñ¡•‘}İ½É­•Èİ¡•É”µ…Ñ¡•‘}İ½É­•È¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥…¹µ…Ñ¡•‘}İ½É­•È¹ÕÍ•É}¥¥¸€¡ìœ°œ¹©½¥¸ œüœ™½È|¥¸İ½É­•É}¥‘Ì¥ô¤¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡İ½É­•É}¥‘Ì¤4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆ¡Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü½È½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤±¥­”€ü½ÈÍ•ÉÙ¥•}É•Á½ÉÑÌ¹…‰¥¹•Ñ}¹Õµ‰•È±¥­”€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”‰t¤4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}É•Á½ÉÑÌ¸¨°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹É•¥½¹}¹…µ”°½Õ¹ÑÉå}é ¹É•¥½¹}¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹É•¥½¹}½‘”¤…ÌÉ•¥½¹}¹…µ”°4(€€€€€€€€€€€€€€É½ÕÁ}½¹…Ğ¡ÕÍ•ÉÌ¹¹…µ”°€œ°€œ¤…Ìİ½É­•É}¹…µ•Ì4(€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ½¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÕÍ•É}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€½É‘•È‰ä½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤‘•ÍŒ°Í•ÉÙ¥•}É•Á½ÉÑÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€mÕÉÉ•¹Ñ}±…¹Õ…” ¤°€©Á…É…µÍt°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€•áÑ•É¹…±}Ù¥•Ü€ô¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤4(€€€¥˜•áÑ•É¹…±}Ù¥•Üè4(€€€€€€€€ŒM½Á”Ñ¡”™¥±Ñ•È‘É½Á‘½İ¹ÌÑ¼Ñ¡”•áÑ•É¹…°…½Õ¹ĞÌÙ¥Í¥‰±”½É‘•ÉÌ4(€€€€€€€€ŒÍ¼Ñ¡”½ÁÑ¥½¹ÌÑ¡•µÍ•±Ù•Ì‘½¸Ğ±•…¬½Ñ¡•È±¥•¹ÑÌœ‘…Ñ„¸4(€€€€€€€½ÁÑ}±…ÕÍ•Ì°½ÁÑ}Á…É…µÌ€ôÍ•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ ‰¼ˆ¤4(€€€€€€€½ÁÑ¥½¹}İ¡•É”€ô€ˆ…¹€ˆ¹©½¥¸¡½ÁÑ}±…ÕÍ•Ì¤¥˜½ÁÑ}±…ÕÍ•Ì•±Í”€ˆÄ€ô€Äˆ4(€€€€€€€½É‘•É}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜‰Í•±•Ğ‘¥ÍÑ¥¹Ğ¼¹¥°¼¹½É‘•É}¹Õµ‰•È™É½´Í•ÉÙ¥•}½É‘•ÉÌ¼©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹Í•ÉÙ¥•}½É‘•É}¥õ¼¹¥İ¡•É”í½ÁÑ¥½¹}İ¡•É•ô½É‘•È‰ä¼¹½É‘•É}¹Õµ‰•È‘•ÍŒˆ°4(€€€€€€€€€€€½ÁÑ}Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€€€€€Í¥Ñ•}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜‰Í•±•Ğ‘¥ÍÑ¥¹Ğ¼¹±¥•¹Ñ}¹…µ”™É½´Í•ÉÙ¥•}½É‘•ÉÌ¼©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹Í•ÉÙ¥•}½É‘•É}¥õ¼¹¥İ¡•É”¼¹±¥•¹Ñ}¹…µ”€„ô€œœ…¹í½ÁÑ¥½¹}İ¡•É•ô½É‘•È‰ä¼¹±¥•¹Ñ}¹…µ”ˆ°4(€€€€€€€€€€€½ÁÑ}Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€€€€€İ½É­•É}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜ˆˆˆ4(€€€€€€€€€€€Í•±•Ğ‘¥ÍÑ¥¹ĞÔ¹¥°Ô¹¹…µ”™É½´ÕÍ•ÉÌÔ4(€€€€€€€€€€€©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌÜ½¸Ü¹ÕÍ•É}¥õÔ¹¥4(€€€€€€€€€€€©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹¥õÜ¹É•Á½ÉÑ}¥4(€€€€€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ¼½¸¼¹¥€ôÈ¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€€€€€İ¡•É”í½ÁÑ¥½¹}İ¡•É•ô½É‘•È‰äÔ¹¹…µ”°Ô¹¥4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€½ÁÑ}Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€•±Í”è4(€€€€€€€½É‘•É}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ‘¥ÍÑ¥¹Ğ¼¹¥°¼¹½É‘•É}¹Õµ‰•È™É½´Í•ÉÙ¥•}½É‘•ÉÌ¼©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹Í•ÉÙ¥•}½É‘•É}¥õ¼¹¥½É‘•È‰ä¼¹½É‘•É}¹Õµ‰•È‘•ÍŒˆ¤¹™•Ñ¡…±° ¤4(€€€€€€€Í¥Ñ•}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ‘¥ÍÑ¥¹Ğ¼¹±¥•¹Ñ}¹…µ”™É½´Í•ÉÙ¥•}½É‘•ÉÌ¼©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹Í•ÉÙ¥•}½É‘•É}¥õ¼¹¥İ¡•É”¼¹±¥•¹Ñ}¹…µ”€„ô€œœ½É‘•È‰ä¼¹±¥•¹Ñ}¹…µ”ˆ¤¹™•Ñ¡…±° ¤4(€€€€€€€İ½É­•É}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ‘¥ÍÑ¥¹ĞÔ¹¥°Ô¹¹…µ”™É½´ÕÍ•ÉÌÔ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌÜ½¸Ü¹ÕÍ•É}¥õÔ¹¥©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹¥õÜ¹É•Á½ÉÑ}¥½É‘•È‰äÔ¹¹…µ”°Ô¹¥ˆ¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}ÅÕ•Éä¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€ÄõÄ°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€½É‘•É}¥‘Ìõ½É‘•É}¥‘Ì°Í¥Ñ•ÌõÍ¥Ñ•Ì°İ½É­•É}¥‘Ìõİ½É­•É}¥‘Ì°4(€€€€€€€½É‘•É}½ÁÑ¥½¹Ìõ½É‘•É}½ÁÑ¥½¹Ì°4(€€€€€€€Í¥Ñ•}½ÁÑ¥½¹ÌõÍ¥Ñ•}½ÁÑ¥½¹Ì°4(€€€€€€€İ½É­•É}½ÁÑ¥½¹Ìõİ½É­•É}½ÁÑ¥½¹Ì°4(€€€€€€€•áÑ•É¹…±}Ù¥•Üõ•áÑ•É¹…±}Ù¥•Ü°4(€€€€¤4(4(4)‘•˜±…‰½É}É•Á½ÉÑ}•¹ÑÉ¥•Ì¡‘…Ñ•}™É½´ôˆˆ°‘…Ñ•}Ñ¼ôˆˆ°İ½É­•É}¥ôˆˆ°‘…Ñ•}µ½‘”ô‰…ÑÕ…°ˆ¤è4(€€€¥˜‘…Ñ•}µ½‘”€ôô€‰…ÑÑ•¹‘…¹”ˆè4(€€€€€€€É•Á½ÉÑ}‘…Ñ•}•áÁÈ€ô€‰‘…Ñ”¡½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤¤ˆ4(€€€•±¥˜‘…Ñ•}µ½‘”€ôô€‰É•Á½ÉĞˆè4(€€€€€€€É•Á½ÉÑ}‘…Ñ•}•áÁÈ€ô€‰‘…Ñ”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤ˆ4(€€€•±Í”è4(€€€€€€€É•Á½ÉÑ}‘…Ñ•}•áÁÈ€ô€‰½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤ˆ4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰íÉ•Á½ÉÑ}‘…Ñ•}•áÁÉô€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰íÉ•Á½ÉÑ}‘…Ñ•}•áÁÉô€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€¥˜İ½É­•É}¥…¹ÍÑÈ¡İ½É­•É}¥¤¹¥Í‘¥¥Ğ ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰ÕÍ•ÉÌ¹¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡¥¹Ğ¡İ½É­•É}¥¤¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€İ¥Ñ İ½É­•É}½Õ¹ÑÌ…Ì€ 4(€€€€€€€€€€€Í•±•ĞÉ•Á½ÉÑ}¥°½Õ¹Ğ ¨¤…Ìİ½É­•É}½Õ¹Ğ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ4(€€€€€€€€€€€É½ÕÀ‰äÉ•Á½ÉÑ}¥4(€€€€€€€€¤4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥…ÌÉ•Á½ÉÑ}¥°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”°4(€€€€€€€€€€€€€€íÉ•Á½ÉÑ}‘…Ñ•}•áÁÉô…Ì…ÑÕ…±}İ½É­}‘…Ñ”°4(€€€€€€€€€€€€€€íÉ•Á½ÉÑ}‘…Ñ•}•áÁÉô…Ì…ÑÑ•¹‘…¹•}‘…Ñ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹ÑÉ…Ù•±}¡½ÕÉÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹‘É¥Ù¥¹}µ¥±•Ì…Ìİ½É­•É}‘É¥Ù¥¹}µ¥±•Ì°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÑÉ…Ù•±}µ½‘”…Ìİ½É­•É}ÑÉ…Ù•±}µ½‘”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÑÉ…Ù•±}¡½ÕÉÌ…Ìİ½É­•É}ÑÉ…Ù•±}¡½ÕÉÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ…Ìİ½É­•É}ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÉÉ¥Ù…±}Ñ¥µ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}É•Á½ÉÑÌ¹‘•Á…ÉÑÕÉ•}Ñ¥µ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹¥…ÌÍ•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€ÕÍ•ÉÌ¹¥…Ìİ½É­•É}¥°4(€€€€€€€€€€€€€€ÕÍ•ÉÌ¹¹…µ”…Ìİ½É­•É}¹…µ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹É…‘•}¹…µ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹‰…Í•}Í…±…Éä°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹µ•…±}‘…¥±å}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹…É}…±±½İ…¹•}µ•Ñ¡½°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹…É}µ¥±•…•}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹ÍÑ…¹‘…É‘}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹½Ù•ÉÑ¥µ•}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹¡½±¥‘…å}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€½…±•Í”¡İ½É­•É}½Õ¹ÑÌ¹İ½É­•É}½Õ¹Ğ°€Ä¤…Ìİ½É­•É}½Õ¹Ğ4(€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ½¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÕÍ•É}¥4(€€€€€€€±•™Ğ©½¥¸•µÁ±½å••}É…‘•Ì½¸•µÁ±½å••}É…‘•Ì¹¥€ôÕÍ•ÉÌ¹•µÁ±½å••}É…‘•}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸İ½É­•É}½Õ¹ÑÌ½¸İ½É­•É}½Õ¹ÑÌ¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä…ÑÕ…±}İ½É­}‘…Ñ”‘•ÍŒ°ÕÍ•ÉÌ¹¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€•¹ÑÉ¥•Ì€ômt4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€¡½ÕÉÌ€ôÍÁ±¥Ñ}É•Á½ÉÑ}±…‰½É}¡½ÕÉÌ¡É½Ü°É½İl‰İ½É­•É}½Õ¹Ğ‰t¤4(€€€€€€€•¹ÑÉä€ô‘¥Ğ¡É½Ü¤4(€€€€€€€•¹ÑÉä¹ÕÁ‘…Ñ”¡¡½ÕÉÌ¤4(€€€€€€€•¹ÑÉål‰İ½É­}¡½ÕÉÌ‰t€ô¡½ÕÉÍl‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t€¬¡½ÕÉÍl‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t€¬¡½ÕÉÍl‰¡½±¥‘…å}¡½ÕÉÌ‰t4(€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡•¹ÑÉä¤4(€€€É•ÑÕÉ¸•¹ÑÉ¥•Ì4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½±…‰½Èµ¡½ÕÉÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜±…‰½É}¡½ÕÉÍ}É•Á½ÉĞ ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}±…‰½É}Á…åÉ½±±}É•Á½ÉÑÌ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€İ½É­•É}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰İ½É­•É}¥ˆ°€ˆˆ¤4(€€€…¹}™¥±Ñ•É}İ½É­•ÉÌ€ô¹½Éµ…±¥é•‘}É½±” ¤¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•Èˆ°€‰™¥¹…¹”‰ô4(€€€•™™•Ñ¥Ù•}İ½É­•É}¥€ôİ½É­•É}¥¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”ÍÑÈ¡œ¹ÕÍ•Él‰¥‰t¤4(€€€İ½É­•ÉÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ¥°¹…µ”4(€€€€€€€™É½´ÕÍ•ÉÌ4(€€€€€€€İ¡•É”É½±”¥¸€ µ…¹…•Èœ°€™¥¹…¹”œ°€•µÁ±½å•”œ°€•áÑ•É¹…±}•µÁ±½å•”œ¤4(€€€€€€€½É‘•È‰ä¹…µ”4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”mt4(€€€É½İÌ€ô±…‰½É}É•Á½ÉÑ}•¹ÑÉ¥•Ì¡‘…Ñ•}™É½´°‘…Ñ•}Ñ¼°•™™•Ñ¥Ù•}İ½É­•É}¥¤4(€€€Ñ½Ñ…±Ì€ôì4(€€€€€€€€‰İ½É­}¡½ÕÉÌˆèÍÕ´¡É½İl‰İ½É­}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰ÍÑ…¹‘…É‘}¡½ÕÉÌˆèÍÕ´¡É½İl‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌˆèÍÕ´¡É½İl‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰½Ù•ÉÑ¥µ•}¡½ÕÉÌˆèÍÕ´¡É½İl‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰¡½±¥‘…å}¡½ÕÉÌˆèÍÕ´¡É½İl‰¡½±¥‘…å}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€ô4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰±…‰½É}¡½ÕÉÍ}É•Á½ÉĞ¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€Ñ½Ñ…±ÌõÑ½Ñ…±Ì°4(€€€€€€€İ½É­•ÉÌõİ½É­•ÉÌ°4(€€€€€€€İ½É­•É}¥õ¥¹Ğ¡•™™•Ñ¥Ù•}İ½É­•É}¥¤¥˜ÍÑÈ¡•™™•Ñ¥Ù•}İ½É­•É}¥¤¹¥Í‘¥¥Ğ ¤•±Í”€ˆˆ°4(€€€€€€€…¹}™¥±Ñ•É}İ½É­•ÉÌõ…¹}™¥±Ñ•É}İ½É­•ÉÌ°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€¤4(4(4)‘•˜ÕÉÉ•¹Ñ}Á…åÉ½±±}Á•É¥½‘}ÍÑ…ÉĞ¡Ñ½‘…äõ9½¹”¤è4(€€€Ñ½‘…ä€ôÑ½‘…ä½È‘…Ñ”¹Ñ½‘…ä ¤4(€€€ÑÉäè4(€€€€€€€å±•}ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡•Ñ}Í•ÑÑ¥¹œ ‰Á…åÉ½±±}å±•}ÍÑ…ÉĞˆ°€ˆÈÀÈØ´ÀÜ´ÀØˆ¤¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€å±•}ÍÑ…ÉĞ€ô‘…Ñ” ÈÀÈØ°€Ü°€Ø¤4(€€€¥˜Ñ½‘…ä€ğå±•}ÍÑ…ÉĞè4(€€€€€€€É•ÑÕÉ¸å±•}ÍÑ…ÉĞ4(€€€•±…ÁÍ•‘}Á•É¥½‘Ì€ô€¡Ñ½‘…ä€´å±•}ÍÑ…ÉĞ¤¹‘…åÌ€¼¼€ÄĞ4(€€€É•ÑÕÉ¸å±•}ÍÑ…ÉĞ€¬Ñ¥µ•‘•±Ñ„¡‘…åÌõ•±…ÁÍ•‘}Á•É¥½‘Ì€¨€ÄĞ¤4(4(4)‘•˜Á…åÉ½±±}Á•É¥½‘}‘…Ñ•Ì¡Á•É¥½‘}ÍÑ…ÉĞ¤è4(€€€Á•É¥½‘}•¹€ôÁ•É¥½‘}ÍÑ…ÉĞ€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÄÌ¤4(€€€Á…å}‘…Ñ”€ôÁ•É¥½‘}•¹€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÄĞ¤4(€€€É•ÑÕÉ¸Á•É¥½‘}•¹°Á…å}‘…Ñ”4(4(4)‘•˜•™™•Ñ¥Ù•}Á…åÉ½±±}İ½É­•É}¥ ¤è4(€€€É•ÑÕÉ¸€ˆˆ¥˜¹½Éµ…±¥é•‘}É½±” ¤¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•Èˆ°€‰™¥¹…¹”‰ô•±Í”ÍÑÈ¡œ¹ÕÍ•Él‰¥‰t¤4(4(4)‘•˜Á…åÉ½±±}É½İÍ}™½É}É…¹”¡Á•É¥½‘}ÍÑ…ÉĞ°Á•É¥½‘}•¹°Á…å}‘…Ñ”°İ½É­•É}¥ôˆˆ°‘…Ñ•}µ½‘”ô‰…ÑÑ•¹‘…¹”ˆ°‘•Ñ…¥°õ…±Í”¤è4(€€€ÍÕ‰Í¥‘å}Í•ÑÑ¥¹Ì€ôÁ…åÉ½±±}ÍÕ‰Í¥‘å}Í•ÑÑ¥¹Ì ¤4(€€€É½İÌ€ô±…‰½É}É•Á½ÉÑ}•¹ÑÉ¥•Ì¡Á•É¥½‘}ÍÑ…ÉĞ¹¥Í½™½Éµ…Ğ ¤°Á•É¥½‘}•¹¹¥Í½™½Éµ…Ğ ¤°İ½É­•É}¥°‘…Ñ•}µ½‘”õ‘…Ñ•}µ½‘”¤4(€€€Á…åÉ½±°€ôíô4(4(€€€‘•˜•¹ÍÕÉ•}İ½É­•È¡É½Ü¤è4(€€€€€€€­•ä€ô€¡É½İl‰İ½É­•É}¥‰t°É½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰t°É½İl‰Í•ÉÙ¥•}½É‘•É}¥‰t¤¥˜‘•Ñ…¥°•±Í”É½İl‰İ½É­•É}¥‰t4(€€€€€€€É•ÑÕÉ¸Á…åÉ½±°¹Í•Ñ‘•™…Õ±Ğ¡­•ä°ì4(€€€€€€€€€€€€¨¨¡ì‰…ÑÑ•¹‘…¹•}‘…Ñ”ˆèÉ½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰t°€‰Í•ÉÙ¥•}½É‘•É}¥ˆèÉ½İl‰Í•ÉÙ¥•}½É‘•É}¥‰t°4(€€€€€€€€€€€€€€€€‰½É‘•É}¹Õµ‰•ÈˆèÉ½İl‰½É‘•É}¹Õµ‰•È‰t°€‰±¥•¹Ñ}¹…µ”ˆèÉ½İl‰±¥•¹Ñ}¹…µ”‰t°€‰É•Á½ÉÑ}¥‘ÌˆèÍ•Ğ ¥ô¥˜‘•Ñ…¥°•±Í”íô¤°4(€€€€€€€€€€€€‰İ½É­•É}¥ˆèÉ½İl‰İ½É­•É}¥‰t°€‰İ½É­•É}¹…µ”ˆèÉ½İl‰İ½É­•É}¹…µ”‰t°4(€€€€€€€€€€€€‰É…‘•}¹…µ”ˆèÉ½İl‰É…‘•}¹…µ”‰t½È€‹šr«š2–ºhˆ°4(€€€€€€€€€€€€‰‰…Í•}Í…±…Éäˆè™±½…Ğ¡É½İl‰‰…Í•}Í…±…Éä‰t½È€À¤°4(€€€€€€€€€€€€‰µ•…±}‘…¥±å}…µ½Õ¹Ğˆè™±½…Ğ¡É½İl‰µ•…±}‘…¥±å}…µ½Õ¹Ğ‰t½È€À¤°4(€€€€€€€€€€€€‰…É}…±±½İ…¹•}µ•Ñ¡½ˆè€‰µ¥±•…”ˆ°4(€€€€€€€€€€€€‰…É}µ¥±•…•}É…Ñ”ˆè™±½…Ğ¡É½İl‰…É}µ¥±•…•}É…Ñ”‰t½È€À¤°4(€€€€€€€€€€€€‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”ˆè™±½…Ğ 4(€€€€€€€€€€€€€€€É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”‰t4(€€€€€€€€€€€€€€€¥˜É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”‰t¥Ì¹½Ğ9½¹”•±Í”€ÄÔ4(€€€€€€€€€€€€¤°4(€€€€€€€€€€€€‰ÍÑ…¹‘…É‘}É…Ñ”ˆè™±½…Ğ¡É½İl‰ÍÑ…¹‘…É‘}¡½ÕÉ±å}É…Ñ”‰t½È€À¤°4(€€€€€€€€€€€€‰ÑÉ…¹ÍÁ½ÉÑ}É…Ñ”ˆè™±½…Ğ¡É½İl‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉ±å}É…Ñ”‰t½È€À¤°4(€€€€€€€€€€€€‰½Ù•ÉÑ¥µ•}É…Ñ”ˆè™±½…Ğ¡É½İl‰½Ù•ÉÑ¥µ•}¡½ÕÉ±å}É…Ñ”‰t½È€À¤°4(€€€€€€€€€€€€‰¡½±¥‘…å}É…Ñ”ˆè™±½…Ğ¡É½İl‰¡½±¥‘…å}¡½ÕÉ±å}É…Ñ”‰t½È€À¤°4(€€€€€€€€€€€€‰ÍÑ…¹‘…É‘}¡½ÕÉÌˆè€À°€‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌˆè€À°€‰½Ù•ÉÑ¥µ•}¡½ÕÉÌˆè€À°4(€€€€€€€€€€€€‰¡½±¥‘…å}¡½ÕÉÌˆè€À°€‰…ÑÑ•¹‘…¹•}‘…Ñ•ÌˆèÍ•Ğ ¤°€‰‘É¥Ù¥¹}µ¥±•Ìˆè€À°4(€€€€€€€€€€€€‰Í•±™}‘É¥Ù•}ÑÉ…Ù•±}¡½ÕÉÌˆè€À°€‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌˆè€À°4(€€€€€€€€€€€€‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌˆè€À°€‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•Ìˆè€À°4(€€€€€€€€€€€€‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğˆè€À°4(€€€€€€€ô¤4(4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€İ½É­•È€ô•¹ÍÕÉ•}İ½É­•È¡É½Ü¤4(€€€€€€€¥˜‘•Ñ…¥°è4(€€€€€€€€€€€İ½É­•Él‰É•Á½ÉÑ}¥‘Ì‰t¹…‘¡É½İl‰É•Á½ÉÑ}¥‰t¤4(€€€€€€€™½È­•ä¥¸€ ‰ÍÑ…¹‘…É‘}¡½ÕÉÌˆ°€‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌˆ°€‰½Ù•ÉÑ¥µ•}¡½ÕÉÌˆ°€‰¡½±¥‘…å}¡½ÕÉÌˆ¤è4(€€€€€€€€€€€İ½É­•Ém­•åt€¬ôÉ½İm­•åt4(€€€€€€€¥˜É½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰tè4(€€€€€€€€€€€İ½É­•Él‰…ÑÑ•¹‘…¹•}‘…Ñ•Ì‰t¹…‘¡É½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰t¤4(€€€€€€€ÑÉ…Ù•±}µ½‘”€ôÉ½İl‰İ½É­•É}ÑÉ…Ù•±}µ½‘”‰t½È€‰±•…äˆ4(€€€€€€€¥˜ÑÉ…Ù•±}µ½‘”¥¸ì‰Í•±™}‘É¥Ù”ˆ°€‰±•…ä‰ôè4(€€€€€€€€€€€İ½É­•Él‰‘É¥Ù¥¹}µ¥±•Ì‰t€¬ô™±½…Ğ¡É½İl‰İ½É­•É}‘É¥Ù¥¹}µ¥±•Ì‰t½È€À¤4(€€€€€€€€€€€İ½É­•Él‰Í•±™}‘É¥Ù•}ÑÉ…Ù•±}¡½ÕÉÌ‰t€¬ô™±½…Ğ¡É½İl‰İ½É­•É}ÑÉ…Ù•±}¡½ÕÉÌ‰t½È€À¤4(€€€€€€€¥˜ÑÉ…Ù•±}µ½‘”€ôô€‰™½±±½İ¥¹œˆè4(€€€€€€€€€€€İ½É­•Él‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌ‰t€¬ô™±½…Ğ¡É½İl‰İ½É­•É}ÑÉ…Ù•±}¡½ÕÉÌ‰t½È€À¤4(€€€€€€€¥˜ÑÉ…Ù•±}µ½‘”€ôô€‰É•¹Ñ…±}‘É¥Ù”ˆè4(€€€€€€€€€€€İ½É­•Él‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌ‰t€¬ô™±½…Ğ¡É½İl‰İ½É­•É}ÑÉ…Ù•±}¡½ÕÉÌ‰t½È€À¤4(€€€€€€€€€€€İ½É­•Él‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•Ì‰t€¬ô™±½…Ğ¡É½İl‰İ½É­•É}‘É¥Ù¥¹}µ¥±•Ì‰t½È€À¤4(4(€€€‘…Ñ•}•áÁÈ€ô€‰‘…Ñ”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤ˆ¥˜‘…Ñ•}µ½‘”€ôô€‰É•Á½ÉĞˆ•±Í”€‰‘…Ñ”¡½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤¤ˆ4(€€€±…ÕÍ•Ì€ôm˜‰í‘…Ñ•}•áÁÉô€øô€üˆ°˜‰í‘…Ñ•}•áÁÉô€ğô€üˆ°€‰Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}İÉ¥Ñ•É}¥¥Ì¹½Ğ¹Õ±°‰t4(€€€Á…É…µÌ€ômÁ•É¥½‘}ÍÑ…ÉĞ¹¥Í½™½Éµ…Ğ ¤°Á•É¥½‘}•¹¹¥Í½™½Éµ…Ğ ¥t4(€€€¥˜İ½É­•É}¥…¹ÍÑÈ¡İ½É­•É}¥¤¹¥Í‘¥¥Ğ ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰ÕÍ•ÉÌ¹¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡¥¹Ğ¡İ½É­•É}¥¤¤4(€€€İÉ¥Ñ•É}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÕÍ•ÉÌ¹¥…Ìİ½É­•É}¥°ÕÍ•ÉÌ¹¹…µ”…Ìİ½É­•É}¹…µ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹É…‘•}¹…µ”°•µÁ±½å••}É…‘•Ì¹‰…Í•}Í…±…Éä°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹µ•…±}‘…¥±å}…µ½Õ¹Ğ°•µÁ±½å••}É…‘•Ì¹ÍÑ…¹‘…É‘}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹…É}…±±½İ…¹•}µ•Ñ¡½°•µÁ±½å••}É…‘•Ì¹…É}µ¥±•…•}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉ±å}É…Ñ”°•µÁ±½å••}É…‘•Ì¹½Ù•ÉÑ¥µ•}¡½ÕÉ±å}É…Ñ”°4(€€€€€€€€€€€€€€•µÁ±½å••}É…‘•Ì¹¡½±¥‘…å}¡½ÕÉ±å}É…Ñ”°½Õ¹Ğ¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹¥¤…ÌÉ•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ4(€€€€€€€€€€€€€€í˜œ°í‘…Ñ•}•áÁÉô…Ì…ÑÑ•¹‘…¹•}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹¥…ÌÉ•Á½ÉÑ}¥œ¥˜‘•Ñ…¥°•±Í”€œô4(€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}İÉ¥Ñ•É}¥4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€±•™Ğ©½¥¸•µÁ±½å••}É…‘•Ì½¸•µÁ±½å••}É…‘•Ì¹¥€ôÕÍ•ÉÌ¹•µÁ±½å••}É…‘•}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€É½ÕÀ‰äÕÍ•ÉÌ¹¥ìœ°Í•ÉÙ¥•}É•Á½ÉÑÌ¹¥œ¥˜‘•Ñ…¥°•±Í”€œô4(€€€€€€€€ˆˆˆ°Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€™½ÈÉ½Ü¥¸İÉ¥Ñ•É}É½İÌè4(€€€€€€€¥˜‘•Ñ…¥°è4(€€€€€€€€€€€İ½É­•È€ô•¹ÍÕÉ•}İ½É­•È¡É½Ü¤4(€€€€€€€€€€€İ½É­•Él‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t€¬ô¥¹Ğ¡É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t½È€À¤4(€€€€€€€€€€€İ½É­•Él‰É•Á½ÉÑ}¥‘Ì‰t¹…‘¡É½İl‰É•Á½ÉÑ}¥‰t¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€•¹ÍÕÉ•}İ½É­•È¡É½Ü¥l‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t€ô¥¹Ğ¡É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t½È€À¤4(4(€€€Á…åÉ½±±}É½İÌ€ômt4(€€€™½Èİ½É­•È¥¸Á…åÉ½±°¹Ù…±Õ•Ì ¤è4(€€€€€€€…ÑÑ•¹‘…¹•}‘…åÌ€ô±•¸¡İ½É­•Él‰…ÑÑ•¹‘…¹•}‘…Ñ•Ì‰t¤4(€€€€€€€ÍÑ…¹‘…É‘}Á…ä€ôİ½É­•Él‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t€¨İ½É­•Él‰ÍÑ…¹‘…É‘}É…Ñ”‰t4(€€€€€€€½Ù•ÉÑ¥µ•}Á…ä€ôİ½É­•Él‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t€¨İ½É­•Él‰½Ù•ÉÑ¥µ•}É…Ñ”‰t4(€€€€€€€¡½±¥‘…å}Á…ä€ôİ½É­•Él‰¡½±¥‘…å}¡½ÕÉÌ‰t€¨İ½É­•Él‰¡½±¥‘…å}É…Ñ”‰t4(€€€€€€€Í•±™}‘É¥Ù•}…±±½İ…¹”€ôİ½É­•Él‰‘É¥Ù¥¹}µ¥±•Ì‰t€¨İ½É­•Él‰…É}µ¥±•…•}É…Ñ”‰t4(€€€€€€€™½±±½İ¥¹}…±±½İ…¹”€ôİ½É­•Él‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌ‰t€¨İ½É­•Él‰ÑÉ…¹ÍÁ½ÉÑ}É…Ñ”‰t4(€€€€€€€É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”€ôİ½É­•Él‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌ‰t€¨İ½É­•Él‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”‰t4(€€€€€€€İ½É­•Él‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t€ôİ½É­•Él‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌ‰t€¬İ½É­•Él‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌ‰t4(€€€€€€€ÑÉ…¹ÍÁ½ÉÑ}Á…ä€ô™½±±½İ¥¹}…±±½İ…¹”€¬É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”4(€€€€€€€…É}…±±½İ…¹”€ôÍ•±™}‘É¥Ù•}…±±½İ…¹”4(€€€€€€€µ•…±}…±±½İ…¹”€ô…ÑÑ•¹‘…¹•}‘…åÌ€¨İ½É­•Él‰µ•…±}‘…¥±å}…µ½Õ¹Ğ‰t4(€€€€€€€É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”€ôİ½É­•Él‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t€¨ÍÕ‰Í¥‘å}Í•ÑÑ¥¹Íl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”‰t4(€€€€€€€ÍÕ‰Í¥‘å}Ñ½Ñ…°€ô…É}…±±½İ…¹”€¬µ•…±}…±±½İ…¹”€¬É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”4(€€€€€€€Ñ½Ñ…±}Á…ä€ôİ½É­•Él‰‰…Í•}Í…±…Éä‰t€¬ÍÑ…¹‘…É‘}Á…ä€¬ÑÉ…¹ÍÁ½ÉÑ}Á…ä€¬½Ù•ÉÑ¥µ•}Á…ä€¬¡½±¥‘…å}Á…ä€¬ÍÕ‰Í¥‘å}Ñ½Ñ…°4(€€€€€€€Á…åÉ½±±}É½İÌ¹…ÁÁ•¹¡ì¨©İ½É­•È°€‰…ÑÑ•¹‘…¹•}‘…åÌˆè…ÑÑ•¹‘…¹•}‘…åÌ°€‰ÍÑ…¹‘…É‘}Á…äˆèÍÑ…¹‘…É‘}Á…ä°4(€€€€€€€€€€€€‰ÑÉ…¹ÍÁ½ÉÑ}Á…äˆèÑÉ…¹ÍÁ½ÉÑ}Á…ä°€‰½Ù•ÉÑ¥µ•}Á…äˆè½Ù•ÉÑ¥µ•}Á…ä°€‰¡½±¥‘…å}Á…äˆè¡½±¥‘…å}Á…ä°4(€€€€€€€€€€€€‰Í•±™}‘É¥Ù•}…±±½İ…¹”ˆèÍ•±™}‘É¥Ù•}…±±½İ…¹”°€‰™½±±½İ¥¹}…±±½İ…¹”ˆè™½±±½İ¥¹}…±±½İ…¹”°4(€€€€€€€€€€€€‰É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”ˆèÉ•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”°€‰…É}…±±½İ…¹”ˆè…É}…±±½İ…¹”°4(€€€€€€€€€€€€‰µ•…±}…±±½İ…¹”ˆèµ•…±}…±±½İ…¹”°4(€€€€€€€€€€€€‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”ˆèÉ•Á½ÉÑ}İÉ¥Ñ¥¹}™•”°€‰ÍÕ‰Í¥‘å}Ñ½Ñ…°ˆèÍÕ‰Í¥‘å}Ñ½Ñ…°°€‰Ñ½Ñ…±}Á…äˆèÑ½Ñ…±}Á…åô¤4(€€€Á…åÉ½±±}É½İÌ¹Í½ÉĞ¡­•äõ±…µ‰‘„É½ÜèÉ½İl‰İ½É­•É}¹…µ”‰t¤4(€€€¥˜‘•Ñ…¥°è4(€€€€€€€Á…åÉ½±±}É½İÌ¹Í½ÉĞ¡­•äõ±…µ‰‘„É½Üè€¡É½İl‰İ½É­•É}¹…µ”‰t°É½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰t°É½İl‰½É‘•É}¹Õµ‰•È‰t¤¤4(€€€€€€€µ•…±}‘…åÌ°‰…Í•}É½İÌ€ôÍ•Ğ ¤°íô4(€€€€€€€™½ÈÉ½Ü¥¸Á…åÉ½±±}É½İÌè4(€€€€€€€€€€€‰…Í•}É½İÌ¹Í•Ñ‘•™…Õ±Ğ¡É½İl‰İ½É­•É}¥‰t°É½Ü¹½Áä ¤¤4(€€€€€€€€€€€É½İl‰Ñ½Ñ…±}Á…ä‰t€´ôÉ½İl‰‰…Í•}Í…±…Éä‰t4(€€€€€€€€€€€É½İl‰‰…Í•}Í…±…Éä‰t€ô€À4(€€€€€€€€€€€­•ä€ô€¡É½İl‰İ½É­•É}¥‰t°É½İl‰…ÑÑ•¹‘…¹•}‘…Ñ”‰t¤4(€€€€€€€€€€€¥˜É½İl‰…ÑÑ•¹‘…¹•}‘…åÌ‰tè4(€€€€€€€€€€€€€€€¥˜­•ä¥¸µ•…±}‘…åÌè4(€€€€€€€€€€€€€€€€€€€™½È™¥•±¥¸€ ‰ÍÕ‰Í¥‘å}Ñ½Ñ…°ˆ°€‰Ñ½Ñ…±}Á…äˆ¤è4(€€€€€€€€€€€€€€€€€€€€€€€É½İm™¥•±‘t€´ôÉ½İl‰µ•…±}…±±½İ…¹”‰t4(€€€€€€€€€€€€€€€€€€€É½İl‰µ•…±}…±±½İ…¹”‰t€ô€À4(€€€€€€€€€€€€€€€µ•…±}‘…åÌ¹…‘¡­•ä¤4(€€€€€€€™½È½É¥¥¹…°¥¸‰…Í•}É½İÌ¹Ù…±Õ•Ì ¤è4(€€€€€€€€€€€¥˜¹½Ğ½É¥¥¹…±l‰‰…Í•}Í…±…Éä‰tè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€‰…Í”€ôí­•äè€À¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°€¡¥¹Ğ°™±½…Ğ¤¤•±Í”Ù…±Õ”™½È­•ä°Ù…±Õ”¥¸½É¥¥¹…°¹¥Ñ•µÌ ¥ô4(€€€€€€€€€€€‰…Í”¹ÕÁ‘…Ñ”¡İ½É­•É}¥õ½É¥¥¹…±l‰İ½É­•É}¥‰t°İ½É­•É}¹…µ”õ½É¥¥¹…±l‰İ½É­•É}¹…µ”‰t°4(€€€€€€€€€€€€€€€‰…Í•}Í…±…Éäõ½É¥¥¹…±l‰‰…Í•}Í…±…Éä‰t°Ñ½Ñ…±}Á…äõ½É¥¥¹…±l‰‰…Í•}Í…±…Éä‰t°4(€€€€€€€€€€€€€€€…ÑÑ•¹‘…¹•}‘…Ñ”ôˆˆ°Í•ÉÙ¥•}½É‘•É}¥õ9½¹”°½É‘•É}¹Õµ‰•Èôˆˆ°±¥•¹Ñ}¹…µ”ôˆˆ°É•Á½ÉÑ}¥‘ÌõÍ•Ğ ¤¤4(€€€€€€€€€€€Á…åÉ½±±}É½İÌ¹…ÁÁ•¹¡‰…Í”¤4(€€€Ñ½Ñ…±Ì€ôí­•äèÍÕ´¡É½İm­•åt™½ÈÉ½Ü¥¸Á…åÉ½±±}É½İÌ¤™½È­•ä¥¸€ 4(€€€€€€€€‰‰…Í•}Í…±…Éäˆ°€‰ÍÑ…¹‘…É‘}Á…äˆ°€‰ÑÉ…¹ÍÁ½ÉÑ}Á…äˆ°€‰½Ù•ÉÑ¥µ•}Á…äˆ°€‰¡½±¥‘…å}Á…äˆ°4(€€€€€€€€‰Í•±™}‘É¥Ù•}…±±½İ…¹”ˆ°€‰™½±±½İ¥¹}…±±½İ…¹”ˆ°€‰É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”ˆ°€‰…É}…±±½İ…¹”ˆ°4(€€€€€€€€‰µ•…±}…±±½İ…¹”ˆ°€‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”ˆ°€‰ÍÕ‰Í¥‘å}Ñ½Ñ…°ˆ°€‰Ñ½Ñ…±}Á…äˆ¥ô4(€€€É•ÑÕÉ¸ì‰É½İÌˆèÁ…åÉ½±±}É½İÌ°€‰Ñ½Ñ…±ÌˆèÑ½Ñ…±Ì°€‰Á•É¥½‘}ÍÑ…ÉĞˆèÁ•É¥½‘}ÍÑ…ÉĞ°4(€€€€€€€€€€€€‰Á•É¥½‘}•¹ˆèÁ•É¥½‘}•¹°€‰Á…å}‘…Ñ”ˆèÁ…å}‘…Ñ”°€‰ÍÕ‰Í¥‘å}Í•ÑÑ¥¹ÌˆèÍÕ‰Í¥‘å}Í•ÑÑ¥¹Íô4(4(4)‘•˜Á…åÉ½±±}É½İÍ}™½É}Á•É¥½¡Á•É¥½‘}ÍÑ…ÉĞ°İ½É­•É}¥ôˆˆ¤è4(€€€Á•É¥½‘}•¹°Á…å}‘…Ñ”€ôÁ…åÉ½±±}Á•É¥½‘}‘…Ñ•Ì¡Á•É¥½‘}ÍÑ…ÉĞ¤4(€€€É•ÑÕÉ¸Á…åÉ½±±}É½İÍ}™½É}É…¹”¡Á•É¥½‘}ÍÑ…ÉĞ°Á•É¥½‘}•¹°Á…å}‘…Ñ”°İ½É­•É}¥¤4(4(4)‘•˜Á…åÉ½±±}É½İ}•áÁ½ÉĞ¡É½Ü¤è4(€€€Á…å±½…€ôì4(€€€€€€€€‹–Fc–Ş”ˆèÉ½İl‰İ½É­•É}¹…µ”‰t°4(€€€€€€€€‹–Fc–Ş—¶'êœˆèÉ½İl‰É…‘•}¹…µ”‰t°4(€€€€€€€€‹–~ëšr³–Ş—¢ÖˆèÉ½Õ¹¡É½İl‰‰…Í•}Í…±…Éä‰t°€È¤°4(€€€€€€€€‹–ë–.“–’§šVÀˆèÉ½İl‰…ÑÑ•¹‘…¹•}‘…åÌ‰t°4(€€€€€€€€‹¦3¢,ˆèÉ½Õ¹¡É½İl‰‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€‹¢ö›¦3¢,ˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€‹š‚––Ş—š^ØˆèÉ½Õ¹¡É½İl‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€‹š‚––Ş—¢ÖˆèÉ½Õ¹¡É½İl‰ÍÑ…¹‘…É‘}Á…ä‰t°€È¤°4(€€€€€€€€‹¦j?¢†3š^Û¦VüˆèÉ½Õ¹¡É½İl‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€‹¢ö›¦¦û¦¦Ûš^Û¦VüˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€‹¢«¦¦û¢ö›¢†”ˆèÉ½Õ¹¡É½İl‰Í•±™}‘É¥Ù•}…±±½İ…¹”‰t°€È¤°4(€€€€€€€€‹¦j?¢†3¢†—¢ÒĞˆèÉ½Õ¹¡É½İl‰™½±±½İ¥¹}…±±½İ…¹”‰t°€È¤°4(€€€€€€€€‹¢ö›¦¦û¦¦Û¢†—¢ÒĞˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”‰t°€È¤°4(€€€€€€€€‹–*ƒ>·–Ş—š^ØˆèÉ½Õ¹¡É½İl‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€‹–*ƒ>·–Ş—¢ÖˆèÉ½Õ¹¡É½İl‰½Ù•ÉÑ¥µ•}Á…ä‰t°€È¤°4(€€€€€€€€‹–šr–Ş—š^ØˆèÉ½Õ¹¡É½İl‰¡½±¥‘…å}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€‹–šr–Ş—¢ÖˆèÉ½Õ¹¡É½İl‰¡½±¥‘…å}Á…ä‰t°€È¤°4(€€€€€€€€‹¢†—¢ÒĞˆèÉ½Õ¹¡É½İl‰ÍÕ‰Í¥‘å}Ñ½Ñ…°‰t°€È¤°4(€€€ô4(€€€Á…å±½…‘l‹–B#¢º‡–Ş—¢Ö‰t€ôÉ½Õ¹¡É½İl‰Ñ½Ñ…±}Á…ä‰t°€È¤4(€€€É•ÑÕÉ¸Á…å±½…4(4(4)‘•˜Á…åÉ½±±}Á…åÍ±¥Á}Á…å±½…¡É½Ü¤è4(€€€±¥¹•Ì€ôl4(€€€€€€€ì‰±…‰•°ˆè€‹–~ëšr³–Ş—¢Öˆ°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰‰…Í•}Í…±…Éä‰t°€È¥ô°4(€€€€€€€ì‰±…‰•°ˆè€‹š‚––Ş—¢Öˆ°€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t°€È¤°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰ÍÑ…¹‘…É‘}É…Ñ”‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰ÍÑ…¹‘…É‘}Á…ä‰t°€È¥ô°4(€€€€€€€ì‰±…‰•°ˆè€‹–*ƒ>·–Ş—¢Öˆ°€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t°€È¤°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰½Ù•ÉÑ¥µ•}É…Ñ”‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰½Ù•ÉÑ¥µ•}Á…ä‰t°€È¥ô°4(€€€€€€€ì‰±…‰•°ˆè€‹–šr–Ş—¢Öˆ°€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰¡½±¥‘…å}¡½ÕÉÌ‰t°€È¤°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰¡½±¥‘…å}É…Ñ”‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰¡½±¥‘…å}Á…ä‰t°€È¥ô°4(€€€t4(€€€¥˜É½İl‰Í•±™}‘É¥Ù•}…±±½İ…¹”‰t€ø€Àè4(€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€‰±…‰•°ˆè€‹¢«¦¦û¢ö›¢†”ˆ°4(€€€€€€€€€€€€‰µ¥±•ÌˆèÉ½Õ¹¡É½İl‰‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€€€€€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰Í•±™}‘É¥Ù•}ÑÉ…Ù•±}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€€€€€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰…É}µ¥±•…•}É…Ñ”‰t°€È¤°4(€€€€€€€€€€€€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰Í•±™}‘É¥Ù•}…±±½İ…¹”‰t°€È¤°4(€€€€€€€ô¤4(€€€¥˜É½İl‰™½±±½İ¥¹}…±±½İ…¹”‰t€ø€Àè4(€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ì‰±…‰•°ˆè€‹¦j?¢†3¢†—¢ÒĞˆ°€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰™½±±½İ¥¹}ÑÉ…Ù•±}¡½ÕÉÌ‰t°€È¤°4(€€€€€€€€€€€€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰ÑÉ…¹ÍÁ½ÉÑ}É…Ñ”‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰™½±±½İ¥¹}…±±½İ…¹”‰t°€È¥ô¤4(€€€¥˜É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”‰t€ø€Àè4(€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ì‰±…‰•°ˆè€‹¢ö›¦¦û¦¦Û¢†—¢ÒĞˆ°€‰µ¥±•ÌˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€€€€€‰¡½ÕÉÌˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉÌ‰t°€È¤°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}¡½ÕÉ±å}É…Ñ”‰t°€È¤°4(€€€€€€€€€€€€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}…±±½İ…¹”‰t°€È¥ô¤4(€€€¥˜É½İl‰µ•…±}…±±½İ…¹”‰t€ø€Àè4(€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ì‰±…‰•°ˆè€‹¦’C¢†”ˆ°€‰‘…åÌˆèÉ½İl‰…ÑÑ•¹‘…¹•}‘…åÌ‰t°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰µ•…±}‘…¥±å}…µ½Õ¹Ğ‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰µ•…±}…±±½İ…¹”‰t°€È¥ô¤4(€€€¥˜É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”‰t€ø€Àè4(€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ì‰±…‰•°ˆè€‹š*—–F+šJÃ–g¢Òäˆ°€‰½Õ¹ĞˆèÉ½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t°€‰É…Ñ”ˆèÉ½Õ¹¡É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”‰t€¼É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}½Õ¹Ğ‰t°€È¤°€‰…µ½Õ¹ĞˆèÉ½Õ¹¡É½İl‰É•Á½ÉÑ}İÉ¥Ñ¥¹}™•”‰t°€È¥ô¤4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰•µÁ±½å•”ˆèÉ½İl‰İ½É­•É}¹…µ”‰t°4(€€€€€€€€‰É…‘”ˆèÉ½İl‰É…‘•}¹…µ”‰t°4(€€€€€€€€‰…ÑÑ•¹‘…¹•}‘…åÌˆèÉ½İl‰…ÑÑ•¹‘…¹•}‘…åÌ‰t°4(€€€€€€€€‰‘É¥Ù¥¹}µ¥±•ÌˆèÉ½Õ¹¡É½İl‰‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•ÌˆèÉ½Õ¹¡É½İl‰É•¹Ñ…±}‘É¥Ù¥¹}µ¥±•Ì‰t°€Ä¤°4(€€€€€€€€‰±¥¹•Ìˆè±¥¹•Ì°4(€€€€€€€€‰Ñ½Ñ…±}Á…äˆèÉ½Õ¹¡É½İl‰Ñ½Ñ…±}Á…ä‰t°€È¤°4(€€€ô4(4(4)‘•˜Á…åÉ½±±}¡¥ÍÑ½É¥…±}Á…¥‘}‘…Ñ” ¤è4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡•Ñ}Í•ÑÑ¥¹œ ‰Á…åÉ½±±}¡¥ÍÑ½É¥…±}Á…¥‘}‘…Ñ”ˆ°€ˆÈÀÈØ´ÀÜ´ÀÔˆ¤¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€É•ÑÕÉ¸‘…Ñ” ÈÀÈØ°€Ü°€Ô¤4(4(4)‘•˜¡¥ÍÑ½É¥…±}Á…åÉ½±±}Á•É¥½¡İ½É­•É}¥ôˆˆ¤è4(€€€å±•}ÍÑ…ÉĞ€ôÁ…åÉ½±±}å±•}ÍÑ…ÉÑ}‘…Ñ” ¤4(€€€Á•É¥½‘}•¹€ôµ¥¸¡å±•}ÍÑ…ÉĞ€´Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤°Á…åÉ½±±}¡¥ÍÑ½É¥…±}Á…¥‘}‘…Ñ” ¤¤4(€€€±…ÕÍ•Ì€ôl‰‘…Ñ”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤€ğô€ü‰t4(€€€Á…É…µÌ€ômÁ•É¥½‘}•¹¹¥Í½™½Éµ…Ğ ¥t4(€€€¥˜İ½É­•É}¥…¹ÍÑÈ¡İ½É­•É}¥¤¹¥Í‘¥¥Ğ ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰ÕÍ•ÉÌ¹¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡¥¹Ğ¡İ½É­•É}¥¤¤4(€€€É½Ü€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğµ¥¸¡‘…Ñ”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”¤¤…ÌÁ•É¥½‘}ÍÑ…ÉĞ4(€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ½¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÕÍ•É}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½ĞÉ½Ü½È¹½ĞÉ½İl‰Á•É¥½‘}ÍÑ…ÉĞ‰tè4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€Á•É¥½‘}ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É½İl‰Á•É¥½‘}ÍÑ…ÉĞ‰t¤4(€€€¥˜Á•É¥½‘}ÍÑ…ÉĞ€øÁ•É¥½‘}•¹è4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰‰…Ñ¡}ÑåÁ”ˆè€‰¡¥ÍÑ½É¥…°ˆ°4(€€€€€€€€‰±…‰•°ˆè€‹–:–>Ë–Ş—¢Ö¢†—–>Dˆ°4(€€€€€€€€‰Á•É¥½‘}ÍÑ…ÉĞˆèÁ•É¥½‘}ÍÑ…ÉĞ°4(€€€€€€€€‰Á•É¥½‘}•¹ˆèÁ•É¥½‘}•¹°4(€€€€€€€€‰Á…å}‘…Ñ”ˆèÁ…åÉ½±±}¡¥ÍÑ½É¥…±}Á…¥‘}‘…Ñ” ¤°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á…¥ˆ°4(€€€ô4(4(4)‘•˜Á…åÉ½±±}‰…Ñ¡}Á…å±½…¡Á•É¥½‘}ÍÑ…ÉĞ°‰…Ñ¡}ÑåÁ”ô‰É•Õ±…Èˆ¤è4(€€€¥˜‰…Ñ¡}ÑåÁ”€ôô€‰¡¥ÍÑ½É¥…°ˆè4(€€€€€€€Á•É¥½€ô¡¥ÍÑ½É¥…±}Á…åÉ½±±}Á•É¥½¡•™™•Ñ¥Ù•}Á…åÉ½±±}İ½É­•É}¥ ¤¤4(€€€€€€€¥˜¹½ĞÁ•É¥½½ÈÁ•É¥½‘l‰Á•É¥½‘}ÍÑ…ÉĞ‰t€„ôÁ•É¥½‘}ÍÑ…ÉĞè4(€€€€€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€€€€€‰…Ñ €ôÁ…åÉ½±±}É½İÍ}™½É}É…¹” 4(€€€€€€€€€€€Á•É¥½‘l‰Á•É¥½‘}ÍÑ…ÉĞ‰t°4(€€€€€€€€€€€Á•É¥½‘l‰Á•É¥½‘}•¹‰t°4(€€€€€€€€€€€Á•É¥½‘l‰Á…å}‘…Ñ”‰t°4(€€€€€€€€€€€•™™•Ñ¥Ù•}Á…åÉ½±±}İ½É­•É}¥ ¤°4(€€€€€€€€€€€‘…Ñ•}µ½‘”ô‰É•Á½ÉĞˆ°4(€€€€€€€€¤4(€€€€€€€±…‰•°€ôÁ•É¥½‘l‰±…‰•°‰t4(€€€€€€€ÍÑ…ÑÕÌ€ô€‰Á…¥ˆ4(€€€•±Í”è4(€€€€€€€‰…Ñ €ôÁ…åÉ½±±}É½İÍ}™½É}Á•É¥½¡Á•É¥½‘}ÍÑ…ÉĞ°•™™•Ñ¥Ù•}Á…åÉ½±±}İ½É­•É}¥ ¤¤4(€€€€€€€±…‰•°€ô€‹–Ş—¢Ö–>GšRøˆ4(€€€€€€€ÍÑ…ÑÕÌ€ô€‰Á…¥ˆ¥˜‰…Ñ¡l‰Á…å}‘…Ñ”‰t€ğô‘…Ñ”¹Ñ½‘…ä ¤•±Í”€‰Í¡•‘Õ±•ˆ4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰‰…Ñ¡}ÑåÁ”ˆè‰…Ñ¡}ÑåÁ”°4(€€€€€€€€‰±…‰•°ˆè±…‰•°°4(€€€€€€€€‰Á•É¥½‘}ÍÑ…ÉĞˆè‰…Ñ¡l‰Á•É¥½‘}ÍÑ…ÉĞ‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰Á•É¥½‘}•¹ˆè‰…Ñ¡l‰Á•É¥½‘}•¹‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰Á…å}‘…Ñ”ˆè‰…Ñ¡l‰Á…å}‘…Ñ”‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰ÍÑ…ÑÕÌˆèÍÑ…ÑÕÌ°4(€€€€€€€€‰É½İÌˆèmÁ…åÉ½±±}É½İ}•áÁ½ÉĞ¡É½Ü¤™½ÈÉ½Ü¥¸‰…Ñ¡l‰É½İÌ‰ut°4(€€€€€€€€‰Á…åÍ±¥ÁÌˆèmÁ…åÉ½±±}Á…åÍ±¥Á}Á…å±½…¡É½Ü¤™½ÈÉ½Ü¥¸‰…Ñ¡l‰É½İÌ‰ut°4(€€€€€€€€‰Ñ½Ñ…±Ìˆèí­•äèÉ½Õ¹¡Ù…±Õ”°€È¤™½È­•ä°Ù…±Õ”¥¸‰…Ñ¡l‰Ñ½Ñ…±Ì‰t¹¥Ñ•µÌ ¥ô°4(€€€ô4(4(4)‘•˜Á…åÉ½±±}å±•}ÍÑ…ÉÑ}‘…Ñ” ¤è4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡•Ñ}Í•ÑÑ¥¹œ ‰Á…åÉ½±±}å±•}ÍÑ…ÉĞˆ°€ˆÈÀÈØ´ÀÜ´ÀØˆ¤¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€É•ÑÕÉ¸‘…Ñ” ÈÀÈØ°€Ü°€Ø¤4(4(4)‘•˜Á…åÉ½±±}Á•É¥½‘Í}™½É}µ½¹Ñ ¡å•…È°µ½¹Ñ ¤è4(€€€µ½¹Ñ¡}ÍÑ…ÉĞ€ô‘…Ñ”¡å•…È°µ½¹Ñ °€Ä¤4(€€€µ½¹Ñ¡}•¹€ô€¡‘…Ñ”¡å•…È€¬€Ä°€Ä°€Ä¤¥˜µ½¹Ñ €ôô€ÄÈ•±Í”‘…Ñ”¡å•…È°µ½¹Ñ €¬€Ä°€Ä¤¤€´Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤4(€€€å±•}ÍÑ…ÉĞ€ôÁ…åÉ½±±}å±•}ÍÑ…ÉÑ}‘…Ñ” ¤4(€€€ÍÑ…ÉĞ€ôå±•}ÍÑ…ÉĞ4(€€€¥˜µ½¹Ñ¡}ÍÑ…ÉĞ€øå±•}ÍÑ…ÉĞ€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÈÜ¤è4(€€€€€€€Á•É¥½‘Í}‰•™½É”€ôµ…à  ¡µ½¹Ñ¡}ÍÑ…ÉĞ€´å±•}ÍÑ…ÉĞ¤¹‘…åÌ€´€ÈÜ¤€¼¼€ÄĞ€´€Ä°€À¤4(€€€€€€€ÍÑ…ÉĞ€ôå±•}ÍÑ…ÉĞ€¬Ñ¥µ•‘•±Ñ„¡‘…åÌõÁ•É¥½‘Í}‰•™½É”€¨€ÄĞ¤4(€€€Á•É¥½‘Ì€ômt4(€€€İ¡¥±”ÍÑ…ÉĞ€ğôµ½¹Ñ¡}•¹è4(€€€€€€€Á•É¥½‘}•¹°Á…å}‘…Ñ”€ôÁ…åÉ½±±}Á•É¥½‘}‘…Ñ•Ì¡ÍÑ…ÉĞ¤4(€€€€€€€¥˜µ½¹Ñ¡}ÍÑ…ÉĞ€ğôÁ…å}‘…Ñ”€ğôµ½¹Ñ¡}•¹è4(€€€€€€€€€€€Á•É¥½‘Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰‰…Ñ¡}ÑåÁ”ˆè€‰É•Õ±…Èˆ°4(€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè€‹–Ş—¢Ö–>GšRøˆ°4(€€€€€€€€€€€€€€€€€€€€‰Á•É¥½‘}ÍÑ…ÉĞˆèÍÑ…ÉĞ°4(€€€€€€€€€€€€€€€€€€€€‰Á•É¥½‘}•¹ˆèÁ•É¥½‘}•¹°4(€€€€€€€€€€€€€€€€€€€€‰Á…å}‘…Ñ”ˆèÁ…å}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á…¥ˆ¥˜Á…å}‘…Ñ”€ğô‘…Ñ”¹Ñ½‘…ä ¤•±Í”€‰Í¡•‘Õ±•ˆ°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€€¤4(€€€€€€€ÍÑ…ÉĞ€¬ôÑ¥µ•‘•±Ñ„¡‘…åÌôÄĞ¤4(€€€¡¥ÍÑ½É¥…±}Á•É¥½€ô¡¥ÍÑ½É¥…±}Á…åÉ½±±}Á•É¥½¡•™™•Ñ¥Ù•}Á…åÉ½±±}İ½É­•É}¥ ¤¤4(€€€¥˜¡¥ÍÑ½É¥…±}Á•É¥½…¹µ½¹Ñ¡}ÍÑ…ÉĞ€ğô¡¥ÍÑ½É¥…±}Á•É¥½‘l‰Á…å}‘…Ñ”‰t€ğôµ½¹Ñ¡}•¹è4(€€€€€€€Á•É¥½‘Ì¹…ÁÁ•¹¡¡¥ÍÑ½É¥…±}Á•É¥½¤4(€€€Á•É¥½‘Ì¹Í½ÉĞ¡­•äõ±…µ‰‘„Á•É¥½è€¡Á•É¥½‘l‰Á…å}‘…Ñ”‰t°Á•É¥½‘l‰Á•É¥½‘}ÍÑ…ÉĞ‰t°Á•É¥½‘l‰‰…Ñ¡}ÑåÁ”‰t¤¤4(€€€É•ÑÕÉ¸Á•É¥½‘Ì4(4(4)‘•˜Á…åÉ½±±}…±•¹‘…É}İ••­Ì¡å•…È°µ½¹Ñ °Á•É¥½‘Ì¤è4(€€€µ½¹Ñ¡}ÍÑ…ÉĞ€ô‘…Ñ”¡å•…È°µ½¹Ñ °€Ä¤4(€€€µ½¹Ñ¡}•¹€ô€¡‘…Ñ”¡å•…È€¬€Ä°€Ä°€Ä¤¥˜µ½¹Ñ €ôô€ÄÈ•±Í”‘…Ñ”¡å•…È°µ½¹Ñ €¬€Ä°€Ä¤¤€´Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤4(€€€Á…å}‘…åÌ€ôíô4(€€€™½ÈÁ•É¥½¥¸Á•É¥½‘Ìè4(€€€€€€€Á…å}‘…åÌ¹Í•Ñ‘•™…Õ±Ğ¡Á•É¥½‘l‰Á…å}‘…Ñ”‰t°mt¤¹…ÁÁ•¹¡Á•É¥½¤4(€€€ÕÉÉ•¹Ğ€ôµ½¹Ñ¡}ÍÑ…ÉĞ€´Ñ¥µ•‘•±Ñ„¡‘…åÌõµ½¹Ñ¡}ÍÑ…ÉĞ¹İ••­‘…ä ¤¤4(€€€İ••­Ì€ômt4(€€€İ¡¥±”ÕÉÉ•¹Ğ€ğôµ½¹Ñ¡}•¹½ÈÕÉÉ•¹Ğ¹İ••­‘…ä ¤€„ô€Àè4(€€€€€€€İ••¬€ômt4(€€€€€€€™½È|¥¸É…¹” Ü¤è4(€€€€€€€€€€€‘…å}Á•É¥½‘Ì€ôÁ…å}‘…åÌ¹•Ğ¡ÕÉÉ•¹Ğ°mt¤4(€€€€€€€€€€€İ••¬¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰‘…Ñ”ˆèÕÉÉ•¹Ğ°4(€€€€€€€€€€€€€€€€€€€€‰¥¹}µ½¹Ñ ˆèÕÉÉ•¹Ğ¹µ½¹Ñ €ôôµ½¹Ñ °4(€€€€€€€€€€€€€€€€€€€€‰Á•É¥½‘Ìˆè‘…å}Á•É¥½‘Ì°4(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á…¥ˆ¥˜…¹ä¡Á•É¥½‘l‰ÍÑ…ÑÕÌ‰t€ôô€‰Á…¥ˆ™½ÈÁ•É¥½¥¸‘…å}Á•É¥½‘Ì¤•±Í”€ ‰Í¡•‘Õ±•ˆ¥˜‘…å}Á•É¥½‘Ì•±Í”€ˆˆ¤°4(€€€€€€€€€€€€€€€€€€€€‰¥Í}Ñ½‘…äˆèÕÉÉ•¹Ğ€ôô‘…Ñ”¹Ñ½‘…ä ¤°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€€¤4(€€€€€€€€€€€ÕÉÉ•¹Ğ€¬ôÑ¥µ•‘•±Ñ„¡‘…åÌôÄ¤4(€€€€€€€İ••­Ì¹…ÁÁ•¹¡İ••¬¤4(€€€É•ÑÕÉ¸İ••­Ì4(4(4)‘•˜Á…ÉÍ•‘}¥Í½}‘…Ñ”¡Ù…±Õ”¤è4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡ÍÑÈ¡Ù…±Õ”¤¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸9½¹”4(4(4)‘•˜Í•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ¡Ñ…‰±•}…±¥…Ìô‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤è4(€€€±…ÕÍ•Ì€ômt4(€€€Á…É…µÌ€ômt4(€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€¥˜¹½Ğœ¹ÕÍ•Él‰±¥•¹Ñ}¥‰tè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆÄ€ô€Àˆ¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰íÑ…‰±•}…±¥…Íô¹±¥•¹Ñ}¥€ô€üˆ¤4(€€€€€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t¤4(€€€•±¥˜¥Í}•áÑ•É¹…±}•µÁ±½å•” ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€˜‰íÑ…‰±•}…±¥…Íô¹¥¥¸€¡Í•±•ĞÍ•ÉÙ¥•}½É‘•É}¥™É½´ÕÍ•É}Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”ÕÍ•É}¥€ô€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰¥‰t¤4(€€€É•ÑÕÉ¸±…ÕÍ•Ì°Á…É…µÌ4(4(4)‘•˜Í•ÉÙ¥•}½É‘•É}…±•¹‘…É}É½İÌ¡‘…Ñ•}™É½´°‘…Ñ•}Ñ¼¤è4(€€€±…ÕÍ•Ì°Á…É…µÌ€ôÍ•ÉÙ¥•}½É‘•É}…•ÍÍ}™¥±Ñ•ÉÌ ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤4(€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹…±•¹‘…É}‘…Ñ”‰•Ñİ••¸€ü…¹€üˆ¤4(€€€Á…É…µÌ¹•áÑ•¹¡m‘…Ñ•}™É½´¹¥Í½™½Éµ…Ğ ¤°‘…Ñ•}Ñ¼¹¥Í½™½Éµ…Ğ ¥t¤4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€İ¥Ñ É•Á½ÉÑ}‘…Ñ•Ì…Ì€ 4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€‘…Ñ”¡½…±•Í”¡…ÑÕ…±}İ½É­}‘…Ñ”°É•Á½ÉÑ}‘…Ñ”¤¤…Ì…±•¹‘…É}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€Ä…Ì¡…Í}É•Á½ÉĞ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€€€€€İ¡•É”½…±•Í”¡…ÑÕ…±}İ½É­}‘…Ñ”°É•Á½ÉÑ}‘…Ñ”¤¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•É}¥°‘…Ñ”¡½…±•Í”¡…ÑÕ…±}İ½É­}‘…Ñ”°É•Á½ÉÑ}‘…Ñ”¤¤4(€€€€€€€€¤°4(€€€€€€€É•Á½ÉÑ}½Õ¹ÑÌ…Ì€ 4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•É}¥°½Õ¹Ğ ¨¤…ÌÉ•Á½ÉÑ}½Õ¹Ğ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•É}¥4(€€€€€€€€¤°4(€€€€€€€…±•¹‘…É}½É‘•ÉÌ…Ì€ 4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¸¨°4(€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ•Ì¹…±•¹‘…É}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€Ä…Ì¡…Í}É•Á½ÉĞ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€©½¥¸É•Á½ÉÑ}‘…Ñ•Ì½¸É•Á½ÉÑ}‘…Ñ•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€€€€€Õ¹¥½¸…±°4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¸¨°4(€€€€€€€€€€€€€€€€€€‘…Ñ”¡Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÉÑ}‘…Ñ”¤…Ì…±•¹‘…É}‘…Ñ”°4(€€€€€€€€€€€€€€€€€€€À…Ì¡…Í}É•Á½ÉĞ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€±•™Ğ©½¥¸É•Á½ÉÑ}½Õ¹ÑÌ½¸É•Á½ÉÑ}½Õ¹ÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€€€€€İ¡•É”½…±•Í”¡É•Á½ÉÑ}½Õ¹ÑÌ¹É•Á½ÉÑ}½Õ¹Ğ°€À¤€ô€À4(€€€€€€€€€€€€€…¹Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÉÑ}‘…Ñ”¥Ì¹½Ğ¹Õ±°4(€€€€€€€€¤4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¸¨°4(€€€€€€€€€€€€€€±¥•¹ÑÌ¹¹…µ”…ÌÕÍÑ½µ•É}¹…µ”°4(€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ì¹¹…µ”…Ìİ½É­}½É‘•É}ÑåÁ•}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€Í•±•Ğ½Õ¹Ğ ¨¤™É½´¥¹Ù½¥•Ì4(€€€€€€€€€€€€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥…¹¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€€€€€€€€€¤…Ì¥¹Ù½¥•}½Õ¹Ğ°4(€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€Í•±•Ğ½Õ¹Ğ ¨¤™É½´¥¹Ù½¥•Ì4(€€€€€€€€€€€€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€€€€€€€€€€€€€€…¹¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ…¹¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€€€€€¤…ÌÁ…¥‘}¥¹Ù½¥•}½Õ¹Ğ4(€€€€€€€™É½´…±•¹‘…É}½É‘•ÉÌ…ÌÍ•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€±•™Ğ©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¥4(€€€€€€€±•™Ğ©½¥¸İ½É­}½É‘•É}ÑåÁ•Ì½¸İ½É­}½É‘•É}ÑåÁ•Ì¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹İ½É­}½É‘•É}ÑåÁ•}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹…±•¹‘…É}‘…Ñ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)‘•˜…ÍÍ¥¹}Í•ÉÙ¥•}½É‘•É}…±•¹‘…É}±…¹•Ì¡İ••¬¤è4(€€€€ˆˆ‰-••À•… İ½É¬½É‘•È½¸½¹”½µÁ…Ğ¡½É¥é½¹Ñ…°±…¹”İ¥Ñ¡¥¸„…±•¹‘…Èİ••¬¸ˆˆˆ4(€€€¥¹Ñ•ÉÙ…±Ì€ôíô4(€€€™½È‘…å}¥¹‘•à°‘…ä¥¸•¹Õµ•É…Ñ”¡İ••¬¤è4(€€€€€€€™½È•Ù•¹Ğ¥¸‘…ål‰•Ù•¹ÑÌ‰tè4(€€€€€€€€€€€¥¹Ñ•ÉÙ…°€ô¥¹Ñ•ÉÙ…±Ì¹Í•Ñ‘•™…Õ±Ğ 4(€€€€€€€€€€€€€€€•Ù•¹Ñl‰½É‘•É}¥‰t°4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰½É‘•É}¥ˆè•Ù•¹Ñl‰½É‘•É}¥‰t°4(€€€€€€€€€€€€€€€€€€€€‰½É‘•É}¹Õµ‰•Èˆè•Ù•¹Ğ¹•Ğ ‰½É‘•É}¹Õµ‰•Èˆ¤½È€ˆˆ°4(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÉĞˆè‘…å}¥¹‘•à°4(€€€€€€€€€€€€€€€€€€€€‰•¹ˆè‘…å}¥¹‘•à°4(€€€€€€€€€€€€€€€€€€€€‰•Ù•¹ÑÌˆèmt°4(€€€€€€€€€€€€€€€ô°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€¥¹Ñ•ÉÙ…±l‰ÍÑ…ÉĞ‰t€ôµ¥¸¡¥¹Ñ•ÉÙ…±l‰ÍÑ…ÉĞ‰t°‘…å}¥¹‘•à¤4(€€€€€€€€€€€¥¹Ñ•ÉÙ…±l‰•¹‰t€ôµ…à¡¥¹Ñ•ÉÙ…±l‰•¹‰t°‘…å}¥¹‘•à¤4(€€€€€€€€€€€¥¹Ñ•ÉÙ…±l‰•Ù•¹ÑÌ‰t¹…ÁÁ•¹¡•Ù•¹Ğ¤4(4(€€€±…¹•}•¹‘Ì€ômt4(€€€™½È¥¹Ñ•ÉÙ…°¥¸Í½ÉÑ• 4(€€€€€€€¥¹Ñ•ÉÙ…±Ì¹Ù…±Õ•Ì ¤°4(€€€€€€€­•äõ±…µ‰‘„¥Ñ•´è€¡¥Ñ•µl‰ÍÑ…ÉĞ‰t°¥Ñ•µl‰•¹‰t°¥Ñ•µl‰½É‘•É}¹Õµ‰•È‰t°¥Ñ•µl‰½É‘•É}¥‰t¤°4(€€€€¤è4(€€€€€€€±…¹”€ô¹•áĞ 4(€€€€€€€€€€€€¡¥¹‘•à™½È¥¹‘•à°±…¹•}•¹¥¸•¹Õµ•É…Ñ”¡±…¹•}•¹‘Ì¤¥˜±…¹•}•¹€ğ¥¹Ñ•ÉÙ…±l‰ÍÑ…ÉĞ‰t¤°4(€€€€€€€€€€€±•¸¡±…¹•}•¹‘Ì¤°4(€€€€€€€€¤4(€€€€€€€¥˜±…¹”€ôô±•¸¡±…¹•}•¹‘Ì¤è4(€€€€€€€€€€€±…¹•}•¹‘Ì¹…ÁÁ•¹¡¥¹Ñ•ÉÙ…±l‰•¹‰t¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€±…¹•}•¹‘Ím±…¹•t€ô¥¹Ñ•ÉÙ…±l‰•¹‰t4(€€€€€€€™½È•Ù•¹Ğ¥¸¥¹Ñ•ÉÙ…±l‰•Ù•¹ÑÌ‰tè4(€€€€€€€€€€€•Ù•¹Ñl‰±…¹”‰t€ô±…¹”4(4(€€€±…¹•}½Õ¹Ğ€ô±•¸¡±…¹•}•¹‘Ì¤4(€€€™½È‘…ä¥¸İ••¬è4(€€€€€€€‘…ål‰±…¹•}½Õ¹Ğ‰t€ô±…¹•}½Õ¹Ğ4(€€€€€€€‘…ål‰•Ù•¹ÑÌ‰t¹Í½ÉĞ¡­•äõ±…µ‰‘„•Ù•¹Ğè€¡•Ù•¹Ğ¹•Ğ ‰±…¹”ˆ°€À¤°•Ù•¹Ğ¹•Ğ ‰½É‘•É}¹Õµ‰•Èˆ¤½È€ˆˆ¤¤4(€€€É•ÑÕÉ¸İ••¬4(4(4)‘•˜Í•ÉÙ¥•}½É‘•É}…±•¹‘…É}İ••­Ì¡å•…È°µ½¹Ñ ¤è4(€€€µ½¹Ñ¡}ÍÑ…ÉĞ€ô‘…Ñ”¡å•…È°µ½¹Ñ °€Ä¤4(€€€µ½¹Ñ¡}•¹€ô€¡‘…Ñ”¡å•…È€¬€Ä°€Ä°€Ä¤¥˜µ½¹Ñ €ôô€ÄÈ•±Í”‘…Ñ”¡å•…È°µ½¹Ñ €¬€Ä°€Ä¤¤€´Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤4(€€€É¥‘}ÍÑ…ÉĞ€ôµ½¹Ñ¡}ÍÑ…ÉĞ€´Ñ¥µ•‘•±Ñ„¡‘…åÌõµ½¹Ñ¡}ÍÑ…ÉĞ¹İ••­‘…ä ¤¤4(€€€É¥‘}•¹€ôµ½¹Ñ¡}•¹€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôØ€´µ½¹Ñ¡}•¹¹İ••­‘…ä ¤¤4(€€€•Ù•¹ÑÍ}‰å}‘…ä€ôíô4(€€€‘…Ñ•Í}‰å}½É‘•È€ôíô4(€€€™½ÈÉ½Ü¥¸Í•ÉÙ¥•}½É‘•É}…±•¹‘…É}É½İÌ¡É¥‘}ÍÑ…ÉĞ°É¥‘}•¹¤è4(€€€€€€€•Ù•¹Ñ}‘…Ñ”€ôÁ…ÉÍ•‘}¥Í½}‘…Ñ”¡É½İl‰…±•¹‘…É}‘…Ñ”‰t¤4(€€€€€€€¥˜¹½Ğ•Ù•¹Ñ}‘…Ñ”è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€‘…Ñ•Í}‰å}½É‘•È¹Í•Ñ‘•™…Õ±Ğ¡É½İl‰¥‰t°Í•Ğ ¤¤¹…‘¡•Ù•¹Ñ}‘…Ñ”¤4(€€€€€€€¥¹Ù½¥•}½Õ¹Ğ€ô¥¹Ğ¡É½İl‰¥¹Ù½¥•}½Õ¹Ğ‰t½È€À¤4(€€€€€€€Á…¥‘}¥¹Ù½¥•}½Õ¹Ğ€ô¥¹Ğ¡É½İl‰Á…¥‘}¥¹Ù½¥•}½Õ¹Ğ‰t½È€À¤4(€€€€€€€‰¥±±¥¹}ÍÑ…Ñ”€ô€ˆˆ4(€€€€€€€¥˜É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰±½Í•ˆ…¹¥¹Ù½¥•}½Õ¹Ğ…¹Á…¥‘}¥¹Ù½¥•}½Õ¹Ğ€ôô¥¹Ù½¥•}½Õ¹Ğè4(€€€€€€€€€€€‰¥±±¥¹}ÍÑ…Ñ”€ô€‰±½Í•µÁ…¥ˆ4(€€€€€€€•±¥˜É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰±½Í•ˆ…¹¥¹Ù½¥•}½Õ¹Ğè4(€€€€€€€€€€€‰¥±±¥¹}ÍÑ…Ñ”€ô€‰±½Í•µ¥¹Ù½¥•ˆ4(€€€€€€€•±¥˜É½İl‰ÍÑ…ÑÕÌ‰t€„ô€‰±½Í•ˆ…¹¥¹Ù½¥•}½Õ¹Ğè4(€€€€€€€€€€€‰¥±±¥¹}ÍÑ…Ñ”€ô€‰½Á•¸µ¥¹Ù½¥•ˆ4(€€€€€€€•Ù•¹ÑÍ}‰å}‘…ä¹Í•Ñ‘•™…Õ±Ğ¡•Ù•¹Ñ}‘…Ñ”°mt¤¹…ÁÁ•¹ 4(€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€‰½É‘•É}¥ˆèÉ½İl‰¥‰t°4(€€€€€€€€€€€€€€€€‰½É‘•É}¹Õµ‰•ÈˆèÉ½İl‰½É‘•É}¹Õµ‰•È‰t°4(€€€€€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¹Õµ‰•ÈˆèÉ½İl‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰t°4(€€€€€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¹Õµ‰•É}İ…É¹¥¹œˆè±¥•¹Ñ}½É‘•É}¹Õµ‰•É}İ…É¹¥¹œ¡É½İl‰ÕÍÑ½µ•É}¹…µ”‰t°É½İl‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰t¤°4(€€€€€€€€€€€€€€€€‰Í¥Ñ•}¹…µ”ˆèÉ½İl‰±¥•¹Ñ}¹…µ”‰t°4(€€€€€€€€€€€€€€€€‰½İ¹•ÈˆèÉ½İl‰‰Õå•É}½İ¹•È‰t°4(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆèÉ½İl‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÍ}±…‰•°ˆèMQQUM}1	1L¹•Ğ¡É½İl‰ÍÑ…ÑÕÌ‰t°É½İl‰ÍÑ…ÑÕÌ‰t¤°4(€€€€€€€€€€€€€€€€‰‰¥±±¥¹}ÍÑ…Ñ”ˆè‰¥±±¥¹}ÍÑ…Ñ”°4(€€€€€€€€€€€€€€€€‰İ½É­}½É‘•É}ÑåÁ”ˆèÉ½İl‰İ½É­}½É‘•É}ÑåÁ•}¹…µ”‰t°4(€€€€€€€€€€€€€€€€‰ÍÑ…ÉÑ}‘…Ñ”ˆèÉ½İl‰ÍÑ…ÉÑ}‘…Ñ”‰t°4(€€€€€€€€€€€€€€€€‰…±•¹‘…É}‘…Ñ”ˆèÉ½İl‰…±•¹‘…É}‘…Ñ”‰t°4(€€€€€€€€€€€€€€€€‰¡…Í}É•Á½ÉĞˆè‰½½°¡É½İl‰¡…Í}É•Á½ÉĞ‰t¤°4(€€€€€€€€€€€€€€€€‰‘•Ñ…¥±}ÕÉ°ˆèÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õÉ½İl‰¥‰t¤°4(€€€€€€€€€€€ô4(€€€€€€€€¤4(€€€™½È•Ù•¹Ñ}‘…Ñ”°•Ù•¹ÑÌ¥¸•Ù•¹ÑÍ}‰å}‘…ä¹¥Ñ•µÌ ¤è4(€€€€€€€™½È•Ù•¹Ğ¥¸•Ù•¹ÑÌè4(€€€€€€€€€€€½É‘•É}‘…Ñ•Ì€ô‘…Ñ•Í}‰å}½É‘•È¹•Ğ¡•Ù•¹Ñl‰½É‘•É}¥‰t°Í•Ğ ¤¤4(€€€€€€€€€€€½¹Ñ¥¹Õ•Í}™É½µ}ÁÉ•Ù¥½ÕÌ€ô€¡•Ù•¹Ñ}‘…Ñ”€´Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤¤¥¸½É‘•É}‘…Ñ•Ì…¹•Ù•¹Ñ}‘…Ñ”¹İ••­‘…ä ¤€„ô€À4(€€€€€€€€€€€½¹Ñ¥¹Õ•Í}Ñ½}¹•áĞ€ô€¡•Ù•¹Ñ}‘…Ñ”€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤¤¥¸½É‘•É}‘…Ñ•Ì…¹•Ù•¹Ñ}‘…Ñ”¹İ••­‘…ä ¤€„ô€Ø4(€€€€€€€€€€€¥˜½¹Ñ¥¹Õ•Í}™É½µ}ÁÉ•Ù¥½ÕÌ…¹½¹Ñ¥¹Õ•Í}Ñ½}¹•áĞè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ…Ñ¥½¸€ô€‰µ¥‘‘±”ˆ4(€€€€€€€€€€€•±¥˜½¹Ñ¥¹Õ•Í}™É½µ}ÁÉ•Ù¥½ÕÌè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ…Ñ¥½¸€ô€‰•¹ˆ4(€€€€€€€€€€€•±¥˜½¹Ñ¥¹Õ•Í}Ñ½}¹•áĞè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ…Ñ¥½¸€ô€‰ÍÑ…ÉĞˆ4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ…Ñ¥½¸€ô€‰Í¥¹±”ˆ4(€€€€€€€€€€€•Ù•¹Ñl‰½¹Ñ¥¹Õ…Ñ¥½¸‰t€ô½¹Ñ¥¹Õ…Ñ¥½¸4(€€€ÕÉÉ•¹Ğ€ôÉ¥‘}ÍÑ…ÉĞ4(€€€İ••­Ì€ômt4(€€€İ¡¥±”ÕÉÉ•¹Ğ€ğôÉ¥‘}•¹è4(€€€€€€€İ••¬€ômt4(€€€€€€€™½È|¥¸É…¹” Ü¤è4(€€€€€€€€€€€İ••¬¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰‘…Ñ”ˆèÕÉÉ•¹Ğ°4(€€€€€€€€€€€€€€€€€€€€‰¥¹}µ½¹Ñ ˆèÕÉÉ•¹Ğ¹µ½¹Ñ €ôôµ½¹Ñ °4(€€€€€€€€€€€€€€€€€€€€‰•Ù•¹ÑÌˆè•Ù•¹ÑÍ}‰å}‘…ä¹•Ğ¡ÕÉÉ•¹Ğ°mt¤°4(€€€€€€€€€€€€€€€€€€€€‰¥Í}Ñ½‘…äˆèÕÉÉ•¹Ğ€ôô‘…Ñ”¹Ñ½‘…ä ¤°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€€¤4(€€€€€€€€€€€ÕÉÉ•¹Ğ€¬ôÑ¥µ•‘•±Ñ„¡‘…åÌôÄ¤4(€€€€€€€İ••­Ì¹…ÁÁ•¹¡…ÍÍ¥¹}Í•ÉÙ¥•}½É‘•É}…±•¹‘…É}±…¹•Ì¡İ••¬¤¤4(€€€É•ÑÕÉ¸İ••­Ì4(4(4)‘•˜á±Íá}½±Õµ¹}¹…µ”¡¥¹‘•à¤è4(€€€¹…µ”€ô€ˆˆ4(€€€¥¹‘•à€¬ô€Ä4(€€€İ¡¥±”¥¹‘•àè4(€€€€€€€¥¹‘•à°É•µ…¥¹‘•È€ô‘¥Ùµ½¡¥¹‘•à€´€Ä°€ÈØ¤4(€€€€€€€¹…µ”€ô¡È ØÔ€¬É•µ…¥¹‘•È¤€¬¹…µ”4(€€€É•ÑÕÉ¸¹…µ”4(4(4)‘•˜‰Õ¥±‘}Í¥µÁ±•}á±Íà¡¡•…‘•ÉÌ°É½İÌ°Í¡••Ñ}¹…µ”ô‰M¡••ĞÄˆ¤è4(€€€‘•˜•±±}áµ°¡É½İ}¥¹‘•à°½±Õµ¹}¥¹‘•à°Ù…±Õ”¤è4(€€€€€€€É•˜€ô˜‰íá±Íá}½±Õµ¹}¹…µ”¡½±Õµ¹}¥¹‘•à¥õíÉ½İ}¥¹‘•áôˆ4(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°€¡¥¹Ğ°™±½…Ğ°•¥µ…°¤¤…¹¹½Ğ¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°‰½½°¤è4(€€€€€€€€€€€É•ÑÕÉ¸˜œñŒÈô‰íÉ•™ôˆøñØùí™±½…Ğ¡Ù…±Õ”¤è¸É™ôğ½Øøğ½Œøœ4(€€€€€€€Ñ•áĞ€ô¡Ñµ°¹•Í…Á” ˆˆ¥˜Ù…±Õ”¥Ì9½¹”•±Í”ÍÑÈ¡Ù…±Õ”¤¤4(€€€€€€€É•ÑÕÉ¸˜œñŒÈô‰íÉ•™ôˆĞô‰¥¹±¥¹•MÑÈˆøñ¥ÌøñĞùíÑ•áÑôğ½Ğøğ½¥Ìøğ½Œøœ4(4(€€€İ½É­Í¡••Ñ}É½İÌ€ômt4(€€€…±±}É½İÌ€ôm¡•…‘•ÉÍt€¬É½İÌ4(€€€™½ÈÉ½İ}¥¹‘•à°É½Ü¥¸•¹Õµ•É…Ñ”¡…±±}É½İÌ°ÍÑ…ÉĞôÄ¤è4(€€€€€€€•±±Ì€ô€ˆˆ¹©½¥¸¡•±±}áµ°¡É½İ}¥¹‘•à°½±Õµ¹}¥¹‘•à°Ù…±Õ”¤™½È½±Õµ¹}¥¹‘•à°Ù…±Õ”¥¸•¹Õµ•É…Ñ”¡É½Ü¤¤4(€€€€€€€İ½É­Í¡••Ñ}É½İÌ¹…ÁÁ•¹¡˜œñÉ½ÜÈô‰íÉ½İ}¥¹‘•áôˆùí•±±Íôğ½É½Üøœ¤4(€€€‘¥µ•¹Í¥½¸€ô˜‰Äéíá±Íá}½±Õµ¹}¹…µ”¡µ…à¡±•¸¡¡•…‘•ÉÌ¤€´€Ä°€À¤¥õíµ…à¡±•¸¡…±±}É½İÌ¤°€Ä¥ôˆ4(€€€Í¡••Ñ}áµ°€ô€ 4(€€€€€€€€œğıáµ°Ù•ÉÍ¥½¸ôˆÄ¸Àˆ•¹½‘¥¹œô‰UQ´àˆÍÑ…¹‘…±½¹”ô‰å•Ìˆüøœ4(€€€€€€€€œñİ½É­Í¡••Ğáµ±¹Ìô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½ÍÁÉ•…‘Í¡••Ñµ°¼ÈÀÀØ½µ…¥¸ˆøœ4(€€€€€€€˜œñ‘¥µ•¹Í¥½¸É•˜ô‰í‘¥µ•¹Í¥½¹ôˆ¼øñÍ¡••Ñ…Ñ„ùìˆˆ¹©½¥¸¡İ½É­Í¡••Ñ}É½İÌ¥ôğ½Í¡••Ñ…Ñ„øğ½İ½É­Í¡••Ğøœ4(€€€€¤4(€€€İ½É­‰½½­}áµ°€ô€ 4(€€€€€€€€œğıáµ°Ù•ÉÍ¥½¸ôˆÄ¸Àˆ•¹½‘¥¹œô‰UQ´àˆÍÑ…¹‘…±½¹”ô‰å•Ìˆüøœ4(€€€€€€€€œñİ½É­‰½½¬áµ±¹Ìô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½ÍÁÉ•…‘Í¡••Ñµ°¼ÈÀÀØ½µ…¥¸ˆ€œ4(€€€€€€€€áµ±¹ÌéÈô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½½™™¥•½Õµ•¹Ğ¼ÈÀÀØ½É•±…Ñ¥½¹Í¡¥ÁÌˆøœ4(€€€€€€€˜œñÍ¡••ÑÌøñÍ¡••Ğ¹…µ”ô‰í¡Ñµ°¹•Í…Á”¡Í¡••Ñ}¹…µ”¥ôˆÍ¡••Ñ%ôˆÄˆÈé¥ô‰É%Äˆ¼øğ½Í¡••ÑÌøğ½İ½É­‰½½¬øœ4(€€€€¤4(€€€½¹Ñ•¹Ñ}ÑåÁ•Ì€ô€ 4(€€€€€€€€œğıáµ°Ù•ÉÍ¥½¸ôˆÄ¸Àˆ•¹½‘¥¹œô‰UQ´àˆÍÑ…¹‘…±½¹”ô‰å•Ìˆüøœ4(€€€€€€€€œñQåÁ•Ìáµ±¹Ìô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½Á…­…”¼ÈÀÀØ½½¹Ñ•¹ĞµÑåÁ•Ìˆøœ4(€€€€€€€€œñ•™…Õ±ĞáÑ•¹Í¥½¸ô‰É•±Ìˆ½¹Ñ•¹ÑQåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµÁ…­…”¹É•±…Ñ¥½¹Í¡¥ÁÌ­áµ°ˆ¼øœ4(€€€€€€€€œñ•™…Õ±ĞáÑ•¹Í¥½¸ô‰áµ°ˆ½¹Ñ•¹ÑQåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½áµ°ˆ¼øœ4(€€€€€€€€œñ=Ù•ÉÉ¥‘”A…ÉÑ9…µ”ôˆ½á°½İ½É­‰½½¬¹áµ°ˆ½¹Ñ•¹ÑQåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹ÍÁÉ•…‘Í¡••Ñµ°¹Í¡••Ğ¹µ…¥¸­áµ°ˆ¼øœ4(€€€€€€€€œñ=Ù•ÉÉ¥‘”A…ÉÑ9…µ”ôˆ½á°½İ½É­Í¡••ÑÌ½Í¡••ĞÄ¹áµ°ˆ½¹Ñ•¹ÑQåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹ÍÁÉ•…‘Í¡••Ñµ°¹İ½É­Í¡••Ğ­áµ°ˆ¼øœ4(€€€€€€€€œğ½QåÁ•Ìøœ4(€€€€¤4(€€€É½½Ñ}É•±Ì€ô€ 4(€€€€€€€€œğıáµ°Ù•ÉÍ¥½¸ôˆÄ¸Àˆ•¹½‘¥¹œô‰UQ´àˆÍÑ…¹‘…±½¹”ô‰å•Ìˆüøœ4(€€€€€€€€œñI•±…Ñ¥½¹Í¡¥ÁÌáµ±¹Ìô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½Á…­…”¼ÈÀÀØ½É•±…Ñ¥½¹Í¡¥ÁÌˆøœ4(€€€€€€€€œñI•±…Ñ¥½¹Í¡¥À%ô‰É%ÄˆQåÁ”ô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½½™™¥•½Õµ•¹Ğ¼ÈÀÀØ½É•±…Ñ¥½¹Í¡¥ÁÌ½½™™¥•½Õµ•¹ĞˆQ…É•Ğô‰á°½İ½É­‰½½¬¹áµ°ˆ¼øœ4(€€€€€€€€œğ½I•±…Ñ¥½¹Í¡¥ÁÌøœ4(€€€€¤4(€€€İ½É­‰½½­}É•±Ì€ô€ 4(€€€€€€€€œğıáµ°Ù•ÉÍ¥½¸ôˆÄ¸Àˆ•¹½‘¥¹œô‰UQ´àˆÍÑ…¹‘…±½¹”ô‰å•Ìˆüøœ4(€€€€€€€€œñI•±…Ñ¥½¹Í¡¥ÁÌáµ±¹Ìô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½Á…­…”¼ÈÀÀØ½É•±…Ñ¥½¹Í¡¥ÁÌˆøœ4(€€€€€€€€œñI•±…Ñ¥½¹Í¡¥À%ô‰É%ÄˆQåÁ”ô‰¡ÑÑÀè¼½Í¡•µ…Ì¹½Á•¹áµ±™½Éµ…ÑÌ¹½Éœ½½™™¥•½Õµ•¹Ğ¼ÈÀÀØ½É•±…Ñ¥½¹Í¡¥ÁÌ½İ½É­Í¡••ĞˆQ…É•Ğô‰İ½É­Í¡••ÑÌ½Í¡••ĞÄ¹áµ°ˆ¼øœ4(€€€€€€€€œğ½I•±…Ñ¥½¹Í¡¥ÁÌøœ4(€€€€¤4(€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€İ¥Ñ é¥Á™¥±”¹i¥Á¥±”¡‰Õ™™•È°€‰Üˆ°é¥Á™¥±”¹i%A}1Q¤…Ìİ½É­‰½½¬è4(€€€€€€€İ½É­‰½½¬¹İÉ¥Ñ•ÍÑÈ ‰m½¹Ñ•¹Ñ}QåÁ•Ít¹áµ°ˆ°½¹Ñ•¹Ñ}ÑåÁ•Ì¤4(€€€€€€€İ½É­‰½½¬¹İÉ¥Ñ•ÍÑÈ ‰}É•±Ì¼¹É•±Ìˆ°É½½Ñ}É•±Ì¤4(€€€€€€€İ½É­‰½½¬¹İÉ¥Ñ•ÍÑÈ ‰á°½İ½É­‰½½¬¹áµ°ˆ°İ½É­‰½½­}áµ°¤4(€€€€€€€İ½É­‰½½¬¹İÉ¥Ñ•ÍÑÈ ‰á°½}É•±Ì½İ½É­‰½½¬¹áµ°¹É•±Ìˆ°İ½É­‰½½­}É•±Ì¤4(€€€€€€€İ½É­‰½½¬¹İÉ¥Ñ•ÍÑÈ ‰á°½İ½É­Í¡••ÑÌ½Í¡••ĞÄ¹áµ°ˆ°Í¡••Ñ}áµ°¤4(€€€‰Õ™™•È¹Í••¬ À¤4(€€€É•ÑÕÉ¸‰Õ™™•È4(4(4)…ÁÀ¹Á½ÍĞ ˆ½É•Á½ÉÑÌ½•áÁ½ÉĞµÙ¥Í¥‰±”¹á±Íàˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ½ÉÑ}Ù¥Í¥‰±•}É•Á½ÉĞ ¤è4(€€€Á…å±½…€ôÉ•ÅÕ•ÍĞ¹•Ñ}©Í½¸¡Í¥±•¹ĞõQÉÕ”¤½Èíô4(€€€Ñ¥Ñ±”€ôÍÑÈ¡Á…å±½…¹•Ğ ‰Ñ¥Ñ±”ˆ¤½È€‹š*—¢† ˆ¤¹ÍÑÉ¥À ¥lèàÁt½È€‹š*—¢† ˆ4(€€€¡•…‘•ÉÌ€ôÁ…å±½…¹•Ğ ‰¡•…‘•ÉÌˆ¤½Èmt4(€€€É½İÌ€ôÁ…å±½…¹•Ğ ‰É½İÌˆ¤½Èmt4(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¡•…‘•ÉÌ°±¥ÍĞ¤½È¹½Ğ¡•…‘•ÉÌ½È±•¸¡¡•…‘•ÉÌ¤€ø€ÄÀÀè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É½İÌ°±¥ÍĞ¤½È±•¸¡É½İÌ¤€ø€ÄÀÀÀÀè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€±•…¹}¡•…‘•ÉÌ€ômÍÑÈ¡Ù…±Õ”½È€ˆˆ¥lèÔÀÁt™½ÈÙ…±Õ”¥¸¡•…‘•ÉÍt4(€€€±•…¹}É½İÌ€ômt4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É½Ü°±¥ÍĞ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€±•…¹}É½İÌ¹…ÁÁ•¹¡mÍÑÈ¡Ù…±Õ”½È€ˆˆ¥lèÔÀÀÁt™½ÈÙ…±Õ”¥¸É½İlè±•¸¡±•…¹}¡•…‘•ÉÌ¥ut¤4(€€€Í…™•}Í¡••Ñ}¹…µ”€ôÉ”¹ÍÕˆ¡È‰mqp¼¨üéqmqutˆ°€ˆ€ˆ°Ñ¥Ñ±”¤¹ÍÑÉ¥À ¥lèÌÅt½È€‹š*—¢† ˆ4(€€€Í…™•}™¥±•¹…µ”€ôÉ”¹ÍÕˆ¡È‰mxÀ´åµi„µéqÔÑ”ÀÀµqÔå™™™|µt¬ˆ°€ˆ´ˆ°Ñ¥Ñ±”¤¹ÍÑÉ¥À ˆ´ˆ¤½È€‰É•Á½ÉĞˆ4(€€€İ½É­‰½½¬€ô‰Õ¥±‘}Í¥µÁ±•}á±Íà¡±•…¹}¡•…‘•ÉÌ°±•…¹}É½İÌ°Í¡••Ñ}¹…µ”õÍ…™•}Í¡••Ñ}¹…µ”¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€İ½É­‰½½¬°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ˜‰íÍ…™•}™¥±•¹…µ•ôµí‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¥ô¹á±Íàˆ°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹ÍÁÉ•…‘Í¡••Ñµ°¹Í¡••Ğˆ°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½Á…åÉ½±°ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Á…åÉ½±±}É•Á½ÉĞ ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}±…‰½É}Á…åÉ½±±}É•Á½ÉÑÌ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€Á•É¥½‘}ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÍlÁ•É¥½‘}ÍÑ…ÉĞt¤¥˜É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ Á•É¥½‘}ÍÑ…ÉĞœ¤•±Í”ÕÉÉ•¹Ñ}Á…åÉ½±±}Á•É¥½‘}ÍÑ…ÉĞ ¤4(€€€€€€€Á•É¥½‘}•¹€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÍlÁ•É¥½‘}•¹t¤¥˜É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ Á•É¥½‘}•¹œ¤•±Í”Á…åÉ½±±}Á•É¥½‘}‘…Ñ•Ì¡Á•É¥½‘}ÍÑ…ÉĞ¥lÁt4(€€€€€€€¥˜Á•É¥½‘}•¹€ğÁ•É¥½‘}ÍÑ…ÉĞè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ°‘•ÍÉ¥ÁÑ¥½¸ôŸîOšvš^—šr’â7¢÷š^§’ê;–ò–/š^—šrœ¤4(€€€€€€€Á…å}‘…Ñ”€ôÁ•É¥½‘}•¹€¬Ñ¥µ•‘•±Ñ„¡‘…åÌôÄĞ¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°=Ù•É™±½İÉÉ½È¤è4(€€€€€€€…‰½ÉĞ ĞÀÀ°‘•ÍÉ¥ÁÑ¥½¸ôŸ¢¾ß¦'š.§šr'šV#j–ò–/š^—šr–J3îOšvš^—šrœ¤4(€€€…¹}™¥±Ñ•É}İ½É­•ÉÌ€ô¹½Éµ…±¥é•‘}É½±” ¤¥¸ì…‘µ¥¸œ°€µ…¹…•Èœ°€™¥¹…¹”ô4(€€€İ½É­•É}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ İ½É­•É}¥œ°€œœ¤¹ÍÑÉ¥À ¤¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”ÍÑÈ¡œ¹ÕÍ•Él¥t¤4(€€€¥˜İ½É­•É}¥…¹¹½Ğİ½É­•É}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÀ°‘•ÍÉ¥ÁÑ¥½¸ôŸ¢¾ß¦'š.§šr'šV#j’êë–Fcœ¤4(€€€İ½É­•ÉÌ€ô‘ˆ ¤¹•á•ÕÑ” ˆˆ‰Í•±•Ğ¥°¹…µ”™É½´ÕÍ•ÉÌ4(€€€€€€€İ¡•É”É½±”¥¸€ …‘µ¥¸œ°€µ…¹…•Èœ°€™¥¹…¹”œ°€•µÁ±½å•”œ°€•áÑ•É¹…±}•µÁ±½å•”œ°€¥¹Ñ•É¹…°œ°€ÕÍ•Èœ¤4(€€€€€€€½É‘•È‰ä¹…µ”ˆˆˆ¤¹™•Ñ¡…±° ¤¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”mt4(€€€Á…åÉ½±±}‰…Ñ €ôÁ…åÉ½±±}É½İÍ}™½É}É…¹”¡Á•É¥½‘}ÍÑ…ÉĞ°Á•É¥½‘}•¹°Á…å}‘…Ñ”°İ½É­•É}¥¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Á…åÉ½±±}É•Á½ÉĞ¹¡Ñµ°ˆ°4(€€€€€€€İ½É­•ÉÌõİ½É­•ÉÌ°İ½É­•É}¥õİ½É­•É}¥°…¹}™¥±Ñ•É}İ½É­•ÉÌõ…¹}™¥±Ñ•É}İ½É­•ÉÌ°4(€€€€€€€É½İÌõÁ…åÉ½±±}‰…Ñ¡l‰É½İÌ‰t°4(€€€€€€€Ñ½Ñ…±ÌõÁ…åÉ½±±}‰…Ñ¡l‰Ñ½Ñ…±Ì‰t°4(€€€€€€€Á•É¥½‘}ÍÑ…ÉĞõÁ…åÉ½±±}‰…Ñ¡l‰Á•É¥½‘}ÍÑ…ÉĞ‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€Á•É¥½‘}•¹õÁ…åÉ½±±}‰…Ñ¡l‰Á•É¥½‘}•¹‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€Á…å}‘…Ñ”õÁ…åÉ½±±}‰…Ñ¡l‰Á…å}‘…Ñ”‰t¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€ÍÕ‰Í¥‘å}Í•ÑÑ¥¹ÌõÁ…åÉ½±±}‰…Ñ¡l‰ÍÕ‰Í¥‘å}Í•ÑÑ¥¹Ì‰t°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½Á…åÉ½±°µ‘•Ñ…¥±Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Á…åÉ½±±}‘•Ñ…¥±}É•Á½ÉĞ ¤è4(€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Á…åÉ½±±}É•Á½ÉĞˆ°€‰Ù¥•Üˆ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ¤½ÈÕÉÉ•¹Ñ}Á…åÉ½±±}Á•É¥½‘}ÍÑ…ÉĞ ¤¹¥Í½™½Éµ…Ğ ¤¤4(€€€€€€€•¹€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ¤½ÈÁ…åÉ½±±}Á•É¥½‘}‘…Ñ•Ì¡ÍÑ…ÉĞ¥lÁt¹¥Í½™½Éµ…Ğ ¤¤4(€€€€€€€¥˜•¹€ğÍÑ…ÉĞè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ¤4(€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°=Ù•É™±½İÉÉ½È¤è4(€€€€€€€…‰½ÉĞ ĞÀÀ°‘•ÍÉ¥ÁÑ¥½¸ô‹¢¾ß¦'š.§šr'šV#j–ò–/š^—šr–J3îOšvš^—šrˆ¤4(€€€…¹}™¥±Ñ•É}İ½É­•ÉÌ€ô¹½Éµ…±¥é•‘}É½±” ¤¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•Èˆ°€‰™¥¹…¹”‰ô4(€€€İ½É­•É}¥‘Ì€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰İ½É­•É}¥ˆ¤¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”mÍÑÈ¡œ¹ÕÍ•Él‰¥‰t¥t4(€€€½É‘•É}¥‘Ì°Í¥Ñ•Ì€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰½É‘•É}¥ˆ¤°É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰Í¥Ñ”ˆ¤4(€€€¥˜…¹ä¡¹½ĞÙ…±Õ”¹¥Í‘¥¥Ğ ¤™½ÈÙ…±Õ”¥¸İ½É­•É}¥‘Ì€¬½É‘•É}¥‘Ì¤è4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€‰…Ñ €ôÁ…åÉ½±±}É½İÍ}™½É}É…¹”¡ÍÑ…ÉĞ°•¹°•¹°€ˆˆ¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”ÍÑÈ¡œ¹ÕÍ•Él‰¥‰t¤°‘•Ñ…¥°õQÉÕ”¤4(€€€É½İÌ€ômÉ½Ü™½ÈÉ½Ü¥¸‰…Ñ¡l‰É½İÌ‰t¥˜€¡¹½Ğİ½É­•É}¥‘Ì½ÈÍÑÈ¡É½İl‰İ½É­•É}¥‰t¤¥¸İ½É­•É}¥‘Ì¥t4(€€€½É‘•É}½ÁÑ¥½¹Ì€ôíÉ½İl‰Í•ÉÙ¥•}½É‘•É}¥‰tèÉ½Ü™½ÈÉ½Ü¥¸É½İÌ¥˜É½İl‰Í•ÉÙ¥•}½É‘•É}¥‰uô4(€€€Í¥Ñ•}½ÁÑ¥½¹Ì€ômì‰±¥•¹Ñ}¹…µ”ˆèÙ…±Õ•ô™½ÈÙ…±Õ”¥¸Í½ÉÑ•¡íÉ½İl‰±¥•¹Ñ}¹…µ”‰t™½ÈÉ½Ü¥¸É½İÌ¥˜É½İl‰±¥•¹Ñ}¹…µ”‰uô¥t4(€€€É½İÌ€ômÉ½Ü™½ÈÉ½Ü¥¸É½İÌ¥˜€¡¹½Ğ½É‘•É}¥‘Ì½ÈÍÑÈ¡É½İl‰Í•ÉÙ¥•}½É‘•É}¥‰t¤¥¸½É‘•É}¥‘Ì¤4(€€€€€€€€€€€…¹€¡¹½ĞÍ¥Ñ•Ì½ÈÉ½İl‰±¥•¹Ñ}¹…µ”‰t¥¸Í¥Ñ•Ì¥t4(€€€İ½É­•ÉÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ¥°¹…µ”™É½´ÕÍ•ÉÌ½É‘•È‰ä¹…µ”ˆ¤¹™•Ñ¡…±° ¤¥˜…¹}™¥±Ñ•É}İ½É­•ÉÌ•±Í”mt4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” ‰Á…åÉ½±±}‘•Ñ…¥±}É•Á½ÉĞ¹¡Ñµ°ˆ°É½İÌõÉ½İÌ°4(€€€€€€€‘…Ñ•}™É½´õÍÑ…ÉĞ¹¥Í½™½Éµ…Ğ ¤°‘…Ñ•}Ñ¼õ•¹¹¥Í½™½Éµ…Ğ ¤°İ½É­•ÉÌõİ½É­•ÉÌ°İ½É­•É}¥‘Ìõİ½É­•É}¥‘Ì°4(€€€€€€€½É‘•É}½ÁÑ¥½¹Ìõmì‰¥ˆè­•ä°€‰½É‘•É}¹Õµ‰•ÈˆèÙ…±Õ•l‰½É‘•É}¹Õµ‰•È‰uô™½È­•ä°Ù…±Õ”¥¸½É‘•É}½ÁÑ¥½¹Ì¹¥Ñ•µÌ ¥t°4(€€€€€€€½É‘•É}¥‘Ìõ½É‘•É}¥‘Ì°Í¥Ñ•}½ÁÑ¥½¹ÌõÍ¥Ñ•}½ÁÑ¥½¹Ì°Í¥Ñ•ÌõÍ¥Ñ•Ì°…¹}™¥±Ñ•É}İ½É­•ÉÌõ…¹}™¥±Ñ•É}İ½É­•ÉÌ°4(€€€€€€€Ñ½Ñ…°õÍÕ´¡É½İl‰Ñ½Ñ…±}Á…ä‰t™½ÈÉ½Ü¥¸É½İÌ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Á…åÉ½±°½…±•¹‘…Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Á…åÉ½±±}…±•¹‘…È ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}±…‰½É}Á…åÉ½±±}É•Á½ÉÑÌ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ñ½‘…ä€ô‘…Ñ”¹Ñ½‘…ä ¤4(€€€ÑÉäè4(€€€€€€€å•…È€ô¥¹Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰å•…Èˆ°Ñ½‘…ä¹å•…È¤¤4(€€€€€€€µ½¹Ñ €ô¥¹Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰µ½¹Ñ ˆ°Ñ½‘…ä¹µ½¹Ñ ¤¤4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ €ô‘…Ñ”¡å•…È°µ½¹Ñ °€Ä¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ €ô‘…Ñ”¡Ñ½‘…ä¹å•…È°Ñ½‘…ä¹µ½¹Ñ °€Ä¤4(€€€Á•É¥½‘Ì€ôÁ…åÉ½±±}Á•É¥½‘Í}™½É}µ½¹Ñ ¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ ¤4(€€€ÁÉ•Ù¥½ÕÍ}µ½¹Ñ €ô‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È€´€Ä°€ÄÈ°€Ä¤¥˜Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €ôô€Ä•±Í”‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €´€Ä°€Ä¤4(€€€¹•áÑ}µ½¹Ñ €ô‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È€¬€Ä°€Ä°€Ä¤¥˜Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €ôô€ÄÈ•±Í”‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €¬€Ä°€Ä¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Á…åÉ½±±}…±•¹‘…È¹¡Ñµ°ˆ°4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ õÙ¥Í¥‰±•}µ½¹Ñ °4(€€€€€€€ÁÉ•Ù¥½ÕÍ}µ½¹Ñ õÁÉ•Ù¥½ÕÍ}µ½¹Ñ °4(€€€€€€€¹•áÑ}µ½¹Ñ õ¹•áÑ}µ½¹Ñ °4(€€€€€€€İ••­ÌõÁ…åÉ½±±}…±•¹‘…É}İ••­Ì¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ °Á•É¥½‘Ì¤°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½Á…åÉ½±°½…±•¹‘…È½‰…Ñ ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Á…åÉ½±±}…±•¹‘…É}‰…Ñ  ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}±…‰½É}Á…åÉ½±±}É•Á½ÉÑÌ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€Á•É¥½‘}ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á•É¥½‘}ÍÑ…ÉĞˆ°€ˆˆ¤¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡Á…åÉ½±±}‰…Ñ¡}Á…å±½…¡Á•É¥½‘}ÍÑ…ÉĞ°É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‰…Ñ¡}ÑåÁ”ˆ°€‰É•Õ±…Èˆ¤¤¤4(4(4)…ÁÀ¹•Ğ ˆ½Á…åÉ½±°½…±•¹‘…È½•áÁ½ÉĞ¹á±Íàˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Á…åÉ½±±}…±•¹‘…É}•áÁ½ÉĞ ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}±…‰½É}Á…åÉ½±±}É•Á½ÉÑÌ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€Á•É¥½‘}ÍÑ…ÉĞ€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á•É¥½‘}ÍÑ…ÉĞˆ°€ˆˆ¤¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€Á…å±½…€ôÁ…åÉ½±±}‰…Ñ¡}Á…å±½…¡Á•É¥½‘}ÍÑ…ÉĞ°É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‰…Ñ¡}ÑåÁ”ˆ°€‰É•Õ±…Èˆ¤¤4(€€€¡•…‘•ÉÌ€ô±¥ÍĞ¡Á…å±½…‘l‰É½İÌ‰ulÁt¹­•åÌ ¤¤¥˜Á…å±½…‘l‰É½İÌ‰t•±Í”l4(€€€€€€€€‹–Fc–Ş”ˆ°€‹–Fc–Ş—¶'êœˆ°€‹–~ëšr³–Ş—¢Öˆ°€‹–ë–.“–’§šVÀˆ°€‹¦3¢,ˆ°€‹š‚––Ş—š^Øˆ°€‹š‚––Ş—¢Öˆ°4(€€€€€€€€‹¦j?¢†3š^Û¦Vüˆ°€‹¢ö›¦¦û¦¦Ûš^Û¦Vüˆ°€‹¦j?¢†3¢†—¢ÒĞˆ°€‹¢ö›¦¦û¦¦Û¢†—¢ÒĞˆ°€‹¢«¦¦û¢ö›¢†”ˆ°4(€€€€€€€€‹–*ƒ>·–Ş—š^Øˆ°€‹–*ƒ>·–Ş—¢Öˆ°€‹–šr–Ş—š^Øˆ°€‹–šr–Ş—¢Öˆ°€‹¢†—¢ÒĞˆ°€‹–B#¢º‡–Ş—¢Öˆ°4(€€€t4(€€€É½İÌ€ômmÉ½Ü¹•Ğ¡¡•…‘•È°€ˆˆ¤™½È¡•…‘•È¥¸¡•…‘•ÉÍt™½ÈÉ½Ü¥¸Á…å±½…‘l‰É½İÌ‰ut4(€€€É½İÌ¹…ÁÁ•¹¡mt¤4(€€€É½İÌ¹…ÁÁ•¹¡l‹–F£šr|ˆ°˜‰íÁ…å±½…‘lÁ•É¥½‘}ÍÑ…ÉĞuôƒ¢ÌíÁ…å±½…‘lÁ•É¥½‘}•¹uô‰t¤4(€€€É½İÌ¹…ÁÁ•¹¡l‹–>G¢Z«š^—šr|ˆ°Á…å±½…‘l‰Á…å}‘…Ñ”‰ut¤4(€€€É½İÌ¹…ÁÁ•¹¡l‹–B#¢º‡–Ş—¢Öˆ°Á…å±½…‘l‰Ñ½Ñ…±Ì‰ul‰Ñ½Ñ…±}Á…ä‰ut¤4(€€€É½İÌ¹…ÁÁ•¹¡mt¤4(€€€É½İÌ¹…ÁÁ•¹¡l‹–Ş—¢Öšv„‰t¤4(€€€™½ÈÁ…åÍ±¥À¥¸Á…å±½…‘l‰Á…åÍ±¥ÁÌ‰tè4(€€€€€€€É½İÌ¹…ÁÁ•¹¡mt¤4(€€€€€€€É½İÌ¹…ÁÁ•¹¡l‹–Fc–Ş”ˆ°Á…åÍ±¥Ál‰•µÁ±½å•”‰t°€‹–Fc–Ş—¶'êœˆ°Á…åÍ±¥Ál‰É…‘”‰t°€‹–B#¢º‡–Ş—¢Öˆ°Á…åÍ±¥Ál‰Ñ½Ñ…±}Á…ä‰ut¤4(€€€€€€€É½İÌ¹…ÁÁ•¹¡l‹¦†çn¸ˆ°€‹–Ş—š^Øˆ°€‹–6W’îÜˆ°€‹–’§šVÀˆ°€‹šVÃ¦<ˆ°€‹¦3¢,ˆ°€‹¦G¦Št‰t¤4(€€€€€€€™½È±¥¹”¥¸Á…åÍ±¥Ál‰±¥¹•Ì‰tè4(€€€€€€€€€€€É½İÌ¹…ÁÁ•¹¡l4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰±…‰•°ˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰¡½ÕÉÌˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰É…Ñ”ˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰‘…åÌˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰½Õ¹Ğˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰µ¥±•Ìˆ°€ˆˆ¤°4(€€€€€€€€€€€€€€€±¥¹”¹•Ğ ‰…µ½Õ¹Ğˆ°€ˆˆ¤°4(€€€€€€€€€€€t¤4(€€€İ½É­‰½½¬€ô‰Õ¥±‘}Í¥µÁ±•}á±Íà¡¡•…‘•ÉÌ°É½İÌ°Í¡••Ñ}¹…µ”ô‹–Ş—¢Öš&çš²„ˆ¤4(€€€™¥±•¹…µ”€ô˜‰Á…åÉ½±°µíÁ…å±½…‘l‰…Ñ¡}ÑåÁ”uôµíÁ…å±½…‘lÁ…å}‘…Ñ”uô¹á±Íàˆ4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€İ½É­‰½½¬°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ™¥±•¹…µ”°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹ÍÁÉ•…‘Í¡••Ñµ°¹Í¡••Ğˆ°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}ÅÕ•Éä ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€½É‘•É}¥‘Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡Ù…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰½É‘•É}¥ˆ¤¥˜Ù…±Õ”¤¤4(€€€Í¥Ñ•Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡Ù…±Õ”™½ÈÙ…±Õ”¥¸É•ÅÕ•ÍĞ¹…ÉÌ¹•Ñ±¥ÍĞ ‰Í¥Ñ”ˆ¤¥˜Ù…±Õ”¤¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t¤4(€€€½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ”¡˜‰Í•±•Ğ‘¥ÍÑ¥¹ĞÍ•ÉÙ¥•}½É‘•ÉÌ¹¥°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”™É½´Í•ÉÙ¥•}½É‘•ÉÌ©½¥¸ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ½¸ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥İ¡•É”ìœ…¹€œ¹©½¥¸¡±…ÕÍ•Ì¥ô½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È‘•ÍŒˆ°Á…É…µÌ¤¹™•Ñ¡…±° ¤4(€€€™½È½±Õµ¸°Ù…±Õ•Ì¥¸l ‰Í•ÉÙ¥•}½É‘•ÉÌ¹¥ˆ°½É‘•É}¥‘Ì¤°€ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”ˆ°Í¥Ñ•Ì¥tè4(€€€€€€€¥˜Ù…±Õ•Ìè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰í½±Õµ¹ô¥¸€¡ìœ°œ¹©½¥¸ œüœ™½È|¥¸Ù…±Õ•Ì¥ô¤ˆ¤4(€€€€€€€€€€€Á…É…µÌ¹•áÑ•¹¡Ù…±Õ•Ì¤4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹™¥±•}¹…µ”±¥­”€ü4(€€€€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü4(€€€€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€€€€½È½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤±¥­”€ü4(€€€€€€€€€€€€€€€½È¥¹Ù½¥•Ì¹¥¹Ù½¥•}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€¤4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”‰t€¨€Ø¤4(€€€¥˜ÍÑ…ÑÕÌ¥¸UMQ=5I}I%5	UIM59Q}MQQUM}1	1Lè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÍÑ…ÑÕÌ¤4(€€€•±Í”è4(€€€€€€€ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰‘…Ñ”¡ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹É•…Ñ•‘}…Ğ¤€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰‘…Ñ”¡ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹É•…Ñ•‘}…Ğ¤€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¸¨°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹É•¥½¹}½‘”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹É•¥½¹}¹…µ”°½Õ¹ÑÉå}é ¹É•¥½¹}¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹É•¥½¹}½‘”¤…ÌÉ•¥½¹}¹…µ”°4(€€€€€€€€€€€€€€É•…Ñ½ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”°4(€€€€€€€€€€€€€€É•Ù¥•İ•ÉÌ¹¹…µ”…ÌÉ•Ù¥•İ•É}¹…µ”°4(€€€€€€€€€€€€€€¥¹Ù½¥•Ì¹¥¹Ù½¥•}¹Õµ‰•È4(€€€€€€€™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌÉ•…Ñ½ÉÌ½¸É•…Ñ½ÉÌ¹¥€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹É•…Ñ•‘}‰ä4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌÉ•Ù¥•İ•ÉÌ½¸É•Ù¥•İ•ÉÌ¹¥€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹É•Ù¥•İ•‘}‰ä4(€€€€€€€±•™Ğ©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹¥¹Ù½¥•}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰äÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹É•…Ñ•‘}…Ğ‘•ÍŒ°ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€mÕÉÉ•¹Ñ}±…¹Õ…” ¤°€©Á…É…µÍt°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ñ½Ñ…±Ì€ôì4(€€€€€€€€‰±…‰½É}Ñ½Ñ…°ˆèÍÕ´¡™±½…Ğ¡É½İl‰±…‰½É}Ñ½Ñ…°‰t½È€À¤™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰ÑÉ…Ù•±}Ñ½Ñ…°ˆèÍÕ´¡™±½…Ğ¡É½İl‰ÑÉ…Ù•±}Ñ½Ñ…°‰t½È€À¤™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰µ¥±•…•}Ñ½Ñ…°ˆèÍÕ´¡™±½…Ğ¡É½İl‰µ¥±•…•}Ñ½Ñ…°‰t½È€À¤™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€€€€€€‰Ñ½Ñ…±}…µ½Õ¹ĞˆèÍÕ´¡™±½…Ğ¡É½İl‰Ñ½Ñ…±}…µ½Õ¹Ğ‰t½È€À¤™½ÈÉ½Ü¥¸É½İÌ¤°4(€€€ô4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}ÅÕ•Éä¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€Ñ½Ñ…±ÌõÑ½Ñ…±Ì°4(€€€€€€€ÄõÄ°4(€€€€€€€ÍÑ…ÑÕÌõÍÑ…ÑÕÌ°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€±…‰•±ÌõUMQ=5I}I%5	UIM59Q}MQQUM}1	1L°4(€€€€€€€½É‘•É}¥‘Ìõ½É‘•É}¥‘Ì°Í¥Ñ•ÌõÍ¥Ñ•Ì°½É‘•É}½ÁÑ¥½¹Ìõ½ÁÑ¥½¹Ì°4(€€€€€€€Í¥Ñ•}½ÁÑ¥½¹Ìõmì‰±¥•¹Ñ}¹…µ”ˆè¹…µ•ô™½È¹…µ”¥¸Í½ÉÑ•¡íÉ½İl‰±¥•¹Ñ}¹…µ”‰t™½ÈÉ½Ü¥¸½ÁÑ¥½¹Ì¥˜É½İl‰±¥•¹Ñ}¹…µ”‰uô¥t°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½•áÁ•¹Í•Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ•¹Í•}ÅÕ•Éä ¤è4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€Á•ÉÍ½¹}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á•ÉÍ½¹}¥ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…å½ÕÑ}ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€½É‘•É}¹Õµ‰•È€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰½É‘•É}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÁÉ½©•Ñ}¹…µ”€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÁÉ½©•Ñ}¹…µ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô•áÁ•¹Í•}…•ÍÍ}™¥±Ñ•È ¤4(€€€±…ÕÍ•Ì¹…ÁÁ•¹¡…•ÍÍ}±…ÕÍ”¤4(€€€Á…É…µÌ¹•áÑ•¹¡…•ÍÍ}Á…É…µÌ¤4(€€€¥˜Á•ÉÍ½¹}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰½…±•Í”¡•áÁ•¹Í•Ì¹‰•¹•™¥¥…Éå}¥°•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä¤€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡¥¹Ğ¡Á•ÉÍ½¹}¥¤¤4(€€€•±Í”è4(€€€€€€€Á•ÉÍ½¹}¥€ô€ˆˆ4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆˆˆ¡•áÁ•¹Í•Ì¹•áÁ•¹Í•}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€€€€€½È½…±•Í”¡ÁÉ½©•ÑÌ¹¹…µ”°•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ğ¤±¥­”€ü4(€€€€€€€€€€€€€€€€½È½…±•Í”¡•áÁ•¹Í•}¥Ñ•µÌ¹‘•ÍÉ¥ÁÑ¥½¸°€œœ¤±¥­”€ü4(€€€€€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü¤ˆˆˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”‰t€¨€Ô¤4(€€€¥˜½É‘•É}¹Õµ‰•Èè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡˜ˆ•í½É‘•É}¹Õµ‰•Éô”ˆ¤4(€€€¥˜ÁÉ½©•Ñ}¹…µ”è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰½…±•Í”¡ÁÉ½©•ÑÌ¹¹…µ”°•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ğ¤€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÁÉ½©•Ñ}¹…µ”¤4(€€€¥˜ÍÑ…ÑÕÌè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÍÑ…ÑÕÌ¤4(€€€¥˜Á…å½ÕÑ}ÍÑ…ÑÕÌ¥¸aA9M}Ae=UQ}1	1Lè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡Á…å½ÕÑ}ÍÑ…ÑÕÌ¤4(€€€•±Í”è4(€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹•áÁ•¹Í•}‘…Ñ”€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹•áÁ•¹Í•}‘…Ñ”€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ•áÁ•¹Í•Ì¸¨°•áÁ•¹Í•}¥Ñ•µÌ¹¥…Ì¥Ñ•µ}¥°4(€€€€€€€€€€€€€€½…±•Í”¡ÁÉ½©•ÑÌ¹¹…µ”°•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ğ¤…Ì¥Ñ•µ}ÁÉ½©•Ğ°4(€€€€€€€€€€€€€€•áÁ•¹Í•}¥Ñ•µÌ¹‘•ÍÉ¥ÁÑ¥½¸…Ì¥Ñ•µ}‘•ÍÉ¥ÁÑ¥½¸°•áÁ•¹Í•}¥Ñ•µÌ¹…µ½Õ¹Ğ…Ì¥Ñ•µ}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°ÕÍ•ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”°‰•¹•™¥¥…É¥•Ì¹¹…µ”…Ì‰•¹•™¥¥…Éå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹É•¥½¹}¹…µ”°½Õ¹ÑÉå}é ¹É•¥½¹}¹…µ”°Í•ÉÙ¥•}½É‘•ÉÌ¹É•¥½¹}½‘”¤…ÌÉ•¥½¹}¹…µ”4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€©½¥¸•áÁ•¹Í•}¥Ñ•µÌ½¸•áÁ•¹Í•}¥Ñ•µÌ¹•áÁ•¹Í•}¥€ô•áÁ•¹Í•Ì¹¥4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…Ì‰•¹•™¥¥…É¥•Ì½¸‰•¹•™¥¥…É¥•Ì¹¥€ô½…±•Í”¡•áÁ•¹Í•Ì¹‰•¹•™¥¥…Éå}¥°•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä¤4(€€€€€€€±•™Ğ©½¥¸ÁÉ½©•ÑÌ½¸ÁÉ½©•ÑÌ¹¥€ô•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä•áÁ•¹Í•Ì¹•áÁ•¹Í•}‘…Ñ”‘•ÍŒ°•áÁ•¹Í•Ì¹¥‘•ÍŒ°•áÁ•¹Í•}¥Ñ•µÌ¹Í½ÉÑ}½É‘•È°•áÁ•¹Í•}¥Ñ•µÌ¹¥4(€€€€€€€€ˆˆˆ°4(€€€€€€€mÕÉÉ•¹Ñ}±…¹Õ…” ¤°€©Á…É…µÍt°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ñ½Ñ…°€ôÍÕ´ 4(€€€€€€€™±½…Ğ¡É½İl‰¥Ñ•µ}…µ½Õ¹Ğ‰t½È€À¤4(€€€€€€€™½ÈÉ½Ü¥¸É½İÌ4(€€€€€€€¥˜É½İl‰ÍÑ…ÑÕÌ‰t€ôô€‰…ÁÁÉ½Ù•ˆ…¹É½İl‰Á…å½ÕÑ}ÍÑ…ÑÕÌ‰t€„ô€‰Á…¥ˆ4(€€€€¤4(€€€Á•½Á±”€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ‘¥ÍÑ¥¹ĞÕÍ•ÉÌ¹¥°ÕÍ•ÉÌ¹¹…µ”4(€€€€€€€™É½´ÕÍ•ÉÌ4(€€€€€€€©½¥¸•áÁ•¹Í•Ì½¸½…±•Í”¡•áÁ•¹Í•Ì¹‰•¹•™¥¥…Éå}¥°•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä¤€ôÕÍ•ÉÌ¹¥4(€€€€€€€İ¡•É”í…•ÍÍ}±…ÕÍ•ô4(€€€€€€€½É‘•È‰äÕÍ•ÉÌ¹¹…µ”4(€€€€€€€€ˆˆˆ°4(€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€ÁÉ½©•Ñ}½ÁÑ¥½¹Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆ‰Í•±•Ğ‘¥ÍÑ¥¹Ğ½…±•Í”¡ÁÉ½©•ÑÌ¹¹…µ”°•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ğ¤…Ì¹…µ”4(€€€€€€€€€€€™É½´•áÁ•¹Í•Ì©½¥¸•áÁ•¹Í•}¥Ñ•µÌ½¸•áÁ•¹Í•}¥Ñ•µÌ¹•áÁ•¹Í•}¥€ô•áÁ•¹Í•Ì¹¥4(€€€€€€€€€€€±•™Ğ©½¥¸ÁÉ½©•ÑÌ½¸ÁÉ½©•ÑÌ¹¥€ô•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥4(€€€€€€€€€€€İ¡•É”í…•ÍÍ}±…ÕÍ•ô½É‘•È‰ä¹…µ”ˆˆˆ°…•ÍÍ}Á…É…µÌ¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•áÁ•¹Í•}ÅÕ•Éä¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€ÄõÄ°4(€€€€€€€Á•ÉÍ½¹}¥õÁ•ÉÍ½¹}¥°4(€€€€€€€Á•½Á±”õÁ•½Á±”°4(€€€€€€€ÍÑ…ÑÕÌõÍÑ…ÑÕÌ°4(€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌõÁ…å½ÕÑ}ÍÑ…ÑÕÌ°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€Ñ½Ñ…°õÑ½Ñ…°°4(€€€€€€€±…‰•±ÌõaA9M}MQQUM}1	1L°4(€€€€€€€Á…å½ÕÑ}±…‰•±ÌõaA9M}Ae=UQ}1	1L°4(€€€€€€€½É‘•É}¹Õµ‰•Èõ½É‘•É}¹Õµ‰•È°4(€€€€€€€ÁÉ½©•Ñ}¹…µ”õÁÉ½©•Ñ}¹…µ”°4(€€€€€€€ÁÉ½©•Ñ}½ÁÑ¥½¹ÌõÁÉ½©•Ñ}½ÁÑ¥½¹Ì°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½•áÁ•¹Í”µÁÉ½•ÍÍ¥¹œˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œ ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…å½ÕÑ}ÍÑ…ÑÕÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô•áÁ•¹Í•}…•ÍÍ}™¥±Ñ•È ¤4(€€€±…ÕÍ•Ì¹…ÁÁ•¹¡…•ÍÍ}±…ÕÍ”¤4(€€€Á…É…µÌ¹•áÑ•¹¡…•ÍÍ}Á…É…µÌ¤4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€¡•áÁ•¹Í•Ì¹•áÁ•¹Í•}¹Õµ‰•È±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü4(€€€€€€€€€€€€½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü½ÈÕÍ•ÉÌ¹¹…µ”±¥­”€ü½È‰•¹•™¥¥…É¥•Ì¹¹…µ”±¥­”€ü¤4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”‰t€¨€Ô¤4(€€€¥˜ÍÑ…ÑÕÌ¥¸aA9M}MQQUM}1	1Lè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡ÍÑ…ÑÕÌ¤4(€€€•±Í”è4(€€€€€€€ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€¥˜Á…å½ÕÑ}ÍÑ…ÑÕÌ¥¸aA9M}Ae=UQ}1	1Lè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•áÁ•¹Í•Ì¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡Á…å½ÕÑ}ÍÑ…ÑÕÌ¤4(€€€•±Í”è4(€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€ˆˆ4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ•áÁ•¹Í•Ì¸¨°Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È°Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”°4(€€€€€€€€€€€€€€ÕÍ•ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”°‰•¹•™¥¥…É¥•Ì¹¹…µ”…Ì‰•¹•™¥¥…Éå}¹…µ”°É•¥µ‰ÕÉÍ•ÉÌ¹¹…µ”…ÌÉ•¥µ‰ÕÉÍ•‘}‰å}¹…µ”4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€©½¥¸Í•ÉÙ¥•}½É‘•ÉÌ½¸Í•ÉÙ¥•}½É‘•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹Í•ÉÙ¥•}½É‘•É}¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…Ì‰•¹•™¥¥…É¥•Ì½¸‰•¹•™¥¥…É¥•Ì¹¥€ô½…±•Í”¡•áÁ•¹Í•Ì¹‰•¹•™¥¥…Éå}¥°•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä¤4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…ÌÉ•¥µ‰ÕÉÍ•ÉÌ½¸É•¥µ‰ÕÉÍ•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹É•¥µ‰ÕÉÍ•‘}‰ä4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä4(€€€€€€€€€€€…Í”4(€€€€€€€€€€€€€€€İ¡•¸•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ…¹•áÁ•¹Í•Ì¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœÑ¡•¸€À4(€€€€€€€€€€€€€€€İ¡•¸•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€ÍÕ‰µ¥ÑÑ•œÑ¡•¸€Ä4(€€€€€€€€€€€€€€€İ¡•¸•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€É•ÑÕÉ¹•œÑ¡•¸€È4(€€€€€€€€€€€€€€€İ¡•¸•áÁ•¹Í•Ì¹ÍÑ…ÑÕÌ€ô€‘É…™ĞœÑ¡•¸€Ì4(€€€€€€€€€€€€€€€•±Í”€Ğ4(€€€€€€€€€€€•¹°4(€€€€€€€€€€€•áÁ•¹Í•Ì¹•áÁ•¹Í•}‘…Ñ”‘•ÍŒ°4(€€€€€€€€€€€•áÁ•¹Í•Ì¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Ñ½Ñ…±Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ4(€€€€€€€€€€€½…±•Í”¡ÍÕ´¡…Í”İ¡•¸Á…å½ÕÑ}ÍÑ…ÑÕÌ€„ô€Á…¥œÑ¡•¸…µ½Õ¹Ğ•±Í”€À•¹¤°€À¤…ÌÁ•¹‘¥¹}Ñ½Ñ…°°4(€€€€€€€€€€€½…±•Í”¡ÍÕ´¡…Í”İ¡•¸Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á…¥œÑ¡•¸…µ½Õ¹Ğ•±Í”€À•¹¤°€À¤…ÌÁ…¥‘}Ñ½Ñ…°4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€İ¡•É”ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ…¹í…•ÍÍ}±…ÕÍ•ô4(€€€€€€€€ˆˆˆ°…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œ¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€ÄõÄ°4(€€€€€€€ÍÑ…ÑÕÌõÍÑ…ÑÕÌ°4(€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌõÁ…å½ÕÑ}ÍÑ…ÑÕÌ°4(€€€€€€€•áÁ•¹Í•}±…‰•±ÌõaA9M}MQQUM}1	1L°4(€€€€€€€Á…å½ÕÑ}±…‰•±ÌõaA9M}Ae=UQ}1	1L°4(€€€€€€€Á•¹‘¥¹}Ñ½Ñ…°õÑ½Ñ…±Íl‰Á•¹‘¥¹}Ñ½Ñ…°‰t°4(€€€€€€€Á…¥‘}Ñ½Ñ…°õÑ½Ñ…±Íl‰Á…¥‘}Ñ½Ñ…°‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í”µÁÉ½•ÍÍ¥¹œ½…Ñ¥½¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ½•ÍÍ}•áÁ•¹Í•}…Ñ¥½¸ ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€•áÁ•¹Í•}¥€ô¥¹Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰•áÁ•¹Í•}¥ˆ°€ˆˆ¤¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€™±…Í  ‹¢¾ß¦'š.§’â–òƒš*—¦R–6Wš6»ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œˆ¤¤4(€€€•áÁ•¹Í”€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´•áÁ•¹Í•Ìİ¡•É”¥€ô€üˆ°€¡•áÁ•¹Í•}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ•áÁ•¹Í”è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€…Ñ¥½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ¥½¸ˆ°€ˆˆ¤4(€€€¥˜…Ñ¥½¸€ôô€‰É•¥µ‰ÕÉÍ”ˆè4(€€€€€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰•áÁ•¹Í•Ìˆ°€‰…ÁÁÉ½Ù”ˆ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€™±…Í  ‹–>«šr'–ŞË–º‡š‚ã¦k¢şjš*—¦R–>¿’î—–>GšRûˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œˆ¤¤4(€€€€€€€¥˜•áÁ•¹Í•l‰Á…å½ÕÑ}ÍÑ…ÑÕÌ‰t€ôô€‰Á…¥ˆè4(€€€€€€€€€€€™±…Í  ‹¢şg–òƒš*—¦R–6W–ŞËî?–º3š"Cš*—¦Rˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œˆ¤¤4(€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•Ì4(€€€€€€€€€€€Í•ĞÁ…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á…¥œ°É•¥µ‰ÕÉÍ•‘}‰ä€ô€ü°É•¥µ‰ÕÉÍ•‘}…Ğ€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€€€€€İ¡•É”¥€ô€ü…¹ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ…¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€„ô€Á…¥œ4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°¹½Ü ¤°•áÁ•¹Í•}¥¤°4(€€€€€€€€¤4(€€€€€€€¥˜ÕÉÍ½È¹É½İ½Õ¹Ğ€„ô€Äè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í  ‹¢şg–òƒš*—¦R–6W–ŞËî?–º3š"Cš*—¦Rš"[šÖ¢/*Ûš–ŞË–>c–2[ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œˆ¤¤4(€€€€€€€µ•ÍÍ…”€ô˜‹š*—¦R í•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èuôƒ–ŞË–º3š"C–>GšRû¾ò3¦G¦Štíµ½¹•ä¡•áÁ•¹Í•l…µ½Õ¹Ğt°•áÁ•¹Í•lÕÉÉ•¹ät¥÷ˆ4(€€€€€€€¹½Ñ¥™å}•áÁ•¹Í•}Á…ÉÑ¥¥Á…¹ÑÌ 4(€€€€€€€€€€€•áÁ•¹Í”°4(€€€€€€€€€€€€‹š*—¦R–ŞË–>GšRøˆ°4(€€€€€€€€€€€µ•ÍÍ…”°4(€€€€€€€€€€€ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰É•¥µ‰ÕÉÍ”ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°µ•ÍÍ…”¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹š*—¦R–ŞËš‚¢ºÃ’âë–ŞËš*—¦Rˆ°€‰ÍÕ•ÍÌˆ¤4(€€€•±¥˜…Ñ¥½¸€ôô€‰É•Í•Ñ}Á…å½ÕĞˆè4(€€€€€€€¥˜¹½Éµ…±¥é•‘}É½±” ¤€„ô€‰…‘µ¥¸ˆè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•Ì4(€€€€€€€€€€€Í•ĞÁ…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ°É•¥µ‰ÕÉÍ•‘}‰ä€ô¹Õ±°°É•¥µ‰ÕÉÍ•‘}…Ğ€ô¹Õ±°°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡¹½Ü ¤°•áÁ•¹Í•}¥¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰É•Í•Ğˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°€‹–>GšRû*Ûš¦7ö»’âë–úš*—¦R ˆ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹–>GšRû*Ûš–ŞË¦7ö»’âë–úš*—¦Rˆ°€‰ÍÕ•ÍÌˆ¤4(€€€•±¥˜…Ñ¥½¸€ôô€‰É•Í•Ñ}İ½É­™±½Üˆè4(€€€€€€€¥˜¹½Éµ…±¥é•‘}É½±” ¤€„ô€‰…‘µ¥¸ˆè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•Ì4(€€€€€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€ÍÕ‰µ¥ÑÑ•œ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°4(€€€€€€€€€€€€€€€É•Ù¥•İ•‘}‰ä€ô¹Õ±°°É•Ù¥•İ•‘}…Ğ€ô¹Õ±°°4(€€€€€€€€€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ°É•¥µ‰ÕÉÍ•‘}‰ä€ô¹Õ±°°É•¥µ‰ÕÉÍ•‘}…Ğ€ô¹Õ±°°4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡¹½Ü ¤°•áÁ•¹Í•}¥¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰É•Í•Ğˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°€‹šÖ¢/*Ûš¦7ö»’âë–úî?B–º‡š‚ã¾ò3–>GšRû*Ûš¦7ö»’âë–úš*—¦R ˆ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹šÖ¢/*Ûš–J3–>GšRû*Ûš–ŞË¦7ö»ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€•±Í”è4(€€€€€€€™±…Í  ‹¢¾ß¦'š.§¢šš&Ÿ¢†3jšN7’ösˆ°€‰•ÉÉ½Èˆ¤4(€€€¥˜…Ñ¥½¸¥¸ì‰É•Í•Ñ}Á…å½ÕĞˆ°€‰É•Í•Ñ}İ½É­™±½Ü‰ôè4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}ÁÉ½•ÍÍ¥¹œˆ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½É•Á½ÉÑÌ½…Õ‘¥Ğµ±½Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…Õ‘¥Ñ}±½}É•Á½ÉĞ ¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}…Õ‘¥Ñ}±½Ì ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€•¹Ñ¥Ñå}±…‰•±Ì€ôì4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•Èˆè€‹–Ş—–6Tˆ°4(€€€€€€€€‰¥¹Ù½¥”ˆè€‹–>G– ˆ°4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉĞˆè€‹–Ş—’ösš^—š*”ˆ°4(€€€€€€€€‰•áÁ•¹Í”ˆè€‹š*—¦R ˆ°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆè€‹–Ş—–6WîOº\ˆ°4(€€€€€€€€‰ÕÍ•Èˆè€‹R£š"Üˆ°4(€€€€€€€€‰‘…Ñ…‰…Í”ˆè€‹šVÃš6»–êLˆ°4(€€€ô4(€€€…Ñ¥½¹}±…‰•±Ì€ôì4(€€€€€€€€‰É•…Ñ”ˆè€‹–"o–îèˆ°4(€€€€€€€€‰ÕÁ‘…Ñ”ˆè€‹’ş»šRäˆ°4(€€€€€€€€‰‘•±•Ñ”ˆè€‹–"ƒ¦fˆ°4(€€€€€€€€‰ÍÕ‰µ¥Ğˆè€‹š>C’ê“–º‡š‚àˆ°4(€€€€€€€€‰…ÁÁÉ½Ù”ˆè€‹–º‡š‚ã¦k¢şˆ°4(€€€€€€€€‰É•ÑÕÉ¸ˆè€‹¦–nxˆ°4(€€€€€€€€‰Ù½¥ˆè€‹’ös–ê|ˆ°4(€€€€€€€€‰µ…É­}Á…¥ˆè€‹š‚ã¦R ˆ°4(€€€€€€€€‰Õ¹µ…É­}Á…¥ˆè€‹–>[šÚ#š‚ã¦R ˆ°4(€€€€€€€€‰ÍÑ…ÑÕÍ}¡…¹”ˆè€‹¢ÂšVÓ*Ûšˆ°4(€€€€€€€€‰É•¥µ‰ÕÉÍ”ˆè€‹š*—¦R–>GšRøˆ°4(€€€€€€€€‰É•Í•Ğˆè€‹¦7ö»*Ûšˆ°4(€€€€€€€€‰Í•¹ˆè€‹–>G¦¦
»’îØˆ°4(€€€€€€€€‰±½¥¸ˆè€‹fï–öTˆ°4(€€€€€€€€‰ÍÅ°ˆè€‹š&Ÿ¢†0ME0ˆ°4(€€€ô4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€•¹Ñ¥Ñå}ÑåÁ”€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰•¹Ñ¥Ñå}ÑåÁ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€…Ñ¥½¸€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰…Ñ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‘…Ñ•}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}™É½´ˆ°€ˆˆ¤4(€€€‘…Ñ•}Ñ¼€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…Ñ•}Ñ¼ˆ°€ˆˆ¤4(€€€±…ÕÍ•Ì€ôlˆÄ€ô€Ä‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆ¡ÕÍ•É}¹…µ”±¥­”€ü½È•¹Ñ¥Ñå}±…‰•°±¥­”€ü½ÈÍÕµµ…Éä±¥­”€ü¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”‰t¤4(€€€¥˜•¹Ñ¥Ñå}ÑåÁ”¥¸•¹Ñ¥Ñå}±…‰•±Ìè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰•¹Ñ¥Ñå}ÑåÁ”€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡•¹Ñ¥Ñå}ÑåÁ”¤4(€€€•±Í”è4(€€€€€€€•¹Ñ¥Ñå}ÑåÁ”€ô€ˆˆ4(€€€¥˜…Ñ¥½¸¥¸…Ñ¥½¹}±…‰•±Ìè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰…Ñ¥½¸€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡…Ñ¥½¸¤4(€€€•±Í”è4(€€€€€€€…Ñ¥½¸€ô€ˆˆ4(€€€¥˜‘…Ñ•}™É½´è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰‘…Ñ”¡É•…Ñ•‘}…Ğ¤€øô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}™É½´¤4(€€€¥˜‘…Ñ•}Ñ¼è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰‘…Ñ”¡É•…Ñ•‘}…Ğ¤€ğô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡‘…Ñ•}Ñ¼¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ€¨™É½´…Õ‘¥Ñ}±½Ì4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€½É‘•È‰ä‘…Ñ•Ñ¥µ”¡É•…Ñ•‘}…Ğ¤‘•ÍŒ°¥‘•ÍŒ4(€€€€€€€±¥µ¥Ğ€ÄÀÀÀ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰…Õ‘¥Ñ}±½Ì¹¡Ñµ°ˆ°4(€€€€€€€É½İÌõÉ½İÌ°4(€€€€€€€ÄõÄ°4(€€€€€€€•¹Ñ¥Ñå}ÑåÁ”õ•¹Ñ¥Ñå}ÑåÁ”°4(€€€€€€€…Ñ¥½¸õ…Ñ¥½¸°4(€€€€€€€‘…Ñ•}™É½´õ‘…Ñ•}™É½´°4(€€€€€€€‘…Ñ•}Ñ¼õ‘…Ñ•}Ñ¼°4(€€€€€€€•¹Ñ¥Ñå}±…‰•±Ìõ•¹Ñ¥Ñå}±…‰•±Ì°4(€€€€€€€…Ñ¥½¹}±…‰•±Ìõ…Ñ¥½¹}±…‰•±Ì°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}½É‘•ÉÌ ¤è4(€€€Ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€‰Õå•É}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‰Õå•É}¥ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€±…ÕÍ•Ì€ôl‰Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÑÕÌ€„ô€±½Í•œ‰t4(€€€Á…É…µÌ€ômt4(€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€¥˜¹½Ğœ¹ÕÍ•Él‰±¥•¹Ñ}¥‰tè4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ˆÄ€ô€Àˆ¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¥€ô€üˆ¤4(€€€€€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t¤4(€€€•±¥˜¥Í}•áÑ•É¹…±}•µÁ±½å•” ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€‰Í•ÉÙ¥•}½É‘•ÉÌ¹¥¥¸€¡Í•±•ĞÍ•ÉÙ¥•}½É‘•É}¥™É½´ÕÍ•É}Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”ÕÍ•É}¥€ô€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰¥‰t¤4(€€€¥˜Äè4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€ˆ¡Í•ÉÙ¥•}½É‘•ÉÌ¹½É‘•É}¹Õµ‰•È±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¹…µ”±¥­”€ü½È½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤±¥­”€ü½ÈÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}½É‘•É}¹Õµ‰•È±¥­”€ü¤ˆ4(€€€€€€€€¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡m˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”ˆ°˜ˆ•íÅô”‰t¤4(€€€¥˜‰Õå•É}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹ ‰Í•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥€ô€üˆ¤4(€€€€€€€Á…É…µÌ¹…ÁÁ•¹¡¥¹Ğ¡‰Õå•É}¥¤¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¸¨°4(€€€€€€€€€€€€€€±¥•¹ÑÌ¹¹…µ”…ÌÕÍÑ½µ•É}¹…µ”°4(€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ì¹¹…µ”…Ìİ½É­}½É‘•É}ÑåÁ•}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì‰Õå•É}½İ¹•È°4(€€€€€€€€€€€€€€½…±•Í”¡½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È¤…Ì‰Õå•É}•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È°4(€€€€€€€€€€€€€€½¹ÑÉ…ÑÌ¹½¹ÑÉ…Ñ}¹Õµ‰•È°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹ĞÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥¤…ÌÉ•Á½ÉÑ}½Õ¹Ğ°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹Ğ¥¹Ù½¥•Ì¹¥¤…Ì¥¹Ù½¥•}½Õ¹Ğ°4(€€€€€€€€€€€€€€½Õ¹Ğ¡‘¥ÍÑ¥¹Ğ…Í”İ¡•¸¥¹Ù½¥•Ì¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°Ñ¡•¸¥¹Ù½¥•Ì¹¥•¹¤…ÌÁ…¥‘}¥¹Ù½¥•}½Õ¹Ğ4(€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€±•™Ğ©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹±¥•¹Ñ}¥4(€€€€€€€±•™Ğ©½¥¸İ½É­}½É‘•É}ÑåÁ•Ì½¸İ½É­}½É‘•É}ÑåÁ•Ì¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹İ½É­}½É‘•É}ÑåÁ•}¥4(€€€€€€€±•™Ğ©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€±•™Ğ©½¥¸µ…¹Õ™…ÑÕÉ•ÉÌ…Ì½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ½¸½É‘•É}µ…¹Õ™…ÑÕÉ•ÉÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹µ…¹Õ™…ÑÕÉ•É}¥4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½¹ÑÉ…ÑÌ½¸½¹ÑÉ…ÑÌ¹¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹½¹ÑÉ…Ñ}¥4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌ½¸Í•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€±•™Ğ©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥…¹¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€½É‘•È‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹É•…Ñ•‘}…Ğ‘•ÍŒ°Í•ÉÙ¥•}½É‘•ÉÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” ‰Í•ÉÙ¥•}½É‘•ÉÌ¹¡Ñµ°ˆ°½É‘•ÉÌõÉ½İÌ°ÄõÄ°‰Õå•É}¥õ‰Õå•É}¥¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ½…±•¹‘…Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}½É‘•É}…±•¹‘…È ¤è4(€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Í•ÉÙ¥•}½É‘•É}…±•¹‘…Èˆ°€‰Ù¥•Üˆ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Ñ½‘…ä€ô‘…Ñ”¹Ñ½‘…ä ¤4(€€€ÑÉäè4(€€€€€€€å•…È€ô¥¹Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰å•…Èˆ°Ñ½‘…ä¹å•…È¤¤4(€€€€€€€µ½¹Ñ €ô¥¹Ğ¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰µ½¹Ñ ˆ°Ñ½‘…ä¹µ½¹Ñ ¤¤4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ €ô‘…Ñ”¡å•…È°µ½¹Ñ °€Ä¤4(€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ €ô‘…Ñ”¡Ñ½‘…ä¹å•…È°Ñ½‘…ä¹µ½¹Ñ °€Ä¤4(€€€ÁÉ•Ù¥½ÕÍ}µ½¹Ñ €ô‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È€´€Ä°€ÄÈ°€Ä¤¥˜Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €ôô€Ä•±Í”‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €´€Ä°€Ä¤4(€€€¹•áÑ}µ½¹Ñ €ô‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È€¬€Ä°€Ä°€Ä¤¥˜Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €ôô€ÄÈ•±Í”‘…Ñ”¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ €¬€Ä°€Ä¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}…±•¹‘…È¹¡Ñµ°ˆ°4(€€€€€€€Ù¥Í¥‰±•}µ½¹Ñ õÙ¥Í¥‰±•}µ½¹Ñ °4(€€€€€€€ÁÉ•Ù¥½ÕÍ}µ½¹Ñ õÁÉ•Ù¥½ÕÍ}µ½¹Ñ °4(€€€€€€€¹•áÑ}µ½¹Ñ õ¹•áÑ}µ½¹Ñ °4(€€€€€€€İ••­ÌõÍ•ÉÙ¥•}½É‘•É}…±•¹‘…É}İ••­Ì¡Ù¥Í¥‰±•}µ½¹Ñ ¹å•…È°Ù¥Í¥‰±•}µ½¹Ñ ¹µ½¹Ñ ¤°4(€€€€¤4(4(4)‘•˜‰Õå•É}µ…Á}Á…å±½…¡‰Õå•È¤è4(€€€İ…É¹¥¹}‘…åÌ€ô¥¹ÍÁ•Ñ¥½¹}İ…É¹¥¹}‘…åÌ ¤4(€€€å±•}‘…åÌ€ô¥¹ÍÁ•Ñ¥½¹}å±•}‘…åÌ ¤4(€€€±…ÍÑ}…ÑÕ…±}‘…Ñ”€ôÁ…ÉÍ•‘}¥Í½}‘…Ñ”¡‰Õå•Él‰±…ÍÑ}…ÑÕ…±}‘…Ñ”‰t¤4(€€€‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°€ô€¡‘…Ñ”¹Ñ½‘…ä ¤€´±…ÍÑ}…ÑÕ…±}‘…Ñ”¤¹‘…åÌ¥˜±…ÍÑ}…ÑÕ…±}‘…Ñ”•±Í”9½¹”4(€€€¥˜¹½Ğ‰Õå•Él‰İ½É­}½É‘•É}Ñ½Ñ…°‰tè4(€€€€€€€¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰¹½¹”ˆ4(€€€•±¥˜‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°¥Ì9½¹”è4(€€€€€€€¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰½Ù•É‘Õ”ˆ4(€€€•±¥˜‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°€øôå±•}‘…åÌè4(€€€€€€€¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰½Ù•É‘Õ”ˆ4(€€€•±¥˜‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°€øôİ…É¹¥¹}‘…åÌè4(€€€€€€€¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰İ…É¹¥¹œˆ4(€€€•±Í”è4(€€€€€€€¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰™É•Í ˆ4(€€€Á…å±½…€ôì4(€€€€€€€€‰¥ˆè‰Õå•Él‰¥‰t°4(€€€€€€€€‰‰Õå•É}¹Õµ‰•Èˆè‰Õå•Él‰‰Õå•É}¹Õµ‰•È‰t°4(€€€€€€€€‰¹…µ”ˆè‰Õå•Él‰¹…µ”‰t°4(€€€€€€€€‰½İ¹•Èˆè‰Õå•Él‰½İ¹•É}¹…µ”‰t°4(€€€€€€€€‰½¹Ñ…Ñ}¹…µ”ˆè‰Õå•Él‰½¹Ñ…Ñ}¹…µ”‰t°4(€€€€€€€€‰½¹Ñ…Ñ}‘•Ñ…¥±Ìˆè‰Õå•Él‰½¹Ñ…Ñ}‘•Ñ…¥±Ì‰t°4(€€€€€€€€‰•µ…¥°ˆè‰Õå•Él‰•µ…¥°‰t°4(€€€€€€€€‰½Õ¹ÑÉäˆè‰Õå•Él‰½Õ¹ÑÉå}¹…µ”‰t½È‰Õå•Él‰½Õ¹ÑÉå}½‘”‰t½È‰Õå•Él‰½Õ¹ÑÉä‰t°4(€€€€€€€€‰½Õ¹ÑÉå}½‘”ˆè‰Õå•Él‰½Õ¹ÑÉå}½‘”‰t°4(€€€€€€€€‰‘•Ñ…¥±•‘}…‘‘É•ÍÌˆè‰Õå•Él‰‘•Ñ…¥±•‘}…‘‘É•ÍÌ‰t°4(€€€€€€€€‰•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•Èˆè‰Õå•Él‰•ÅÕ¥Áµ•¹Ñ}µ…¹Õ™…ÑÕÉ•È‰t°4(€€€€€€€€‰±…Ñ¥ÑÕ‘”ˆè‰Õå•Él‰±…Ñ¥ÑÕ‘”‰t°4(€€€€€€€€‰±½¹¥ÑÕ‘”ˆè‰Õå•Él‰±½¹¥ÑÕ‘”‰t°4(€€€€€€€€‰•½½‘•}ÍÑ…ÑÕÌˆè‰Õå•Él‰•½½‘•}ÍÑ…ÑÕÌ‰t½È€‰Á•¹‘¥¹œˆ°4(€€€€€€€€‰İ½É­}½É‘•É}Ñ½Ñ…°ˆè‰Õå•Él‰İ½É­}½É‘•É}Ñ½Ñ…°‰t°4(€€€€€€€€‰İ½É­}½É‘•É}½µÁ±•Ñ•ˆè‰Õå•Él‰İ½É­}½É‘•É}½µÁ±•Ñ•‰t°4(€€€€€€€€‰±…ÍÑ}…ÑÕ…±}‘…Ñ”ˆè‰Õå•Él‰±…ÍÑ}…ÑÕ…±}‘…Ñ”‰t°4(€€€€€€€€‰‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°ˆè‘…åÍ}Í¥¹•}±…ÍÑ}…ÑÕ…°°4(€€€€€€€€‰¥¹ÍÁ•Ñ¥½¹}İ…É¹¥¹}‘…åÌˆèİ…É¹¥¹}‘…åÌ°4(€€€€€€€€‰¥¹ÍÁ•Ñ¥½¹}å±•}‘…åÌˆèå±•}‘…åÌ°4(€€€€€€€€‰¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌˆè¥¹ÍÁ•Ñ¥½¹}ÍÑ…ÑÕÌ°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€ 4(€€€€€€€€€€€€‰½µÁ±•Ñ•ˆ4(€€€€€€€€€€€¥˜‰Õå•Él‰İ½É­}½É‘•É}Ñ½Ñ…°‰t…¹‰Õå•Él‰İ½É­}½É‘•É}Ñ½Ñ…°‰t€ôô‰Õå•Él‰İ½É­}½É‘•É}½µÁ±•Ñ•‰t4(€€€€€€€€€€€•±Í”€‰…Ñ¥Ù”ˆ4(€€€€€€€€¤°4(€€€€€€€€‰‘•Ñ…¥±}ÕÉ°ˆèÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ°‰Õå•É}¥õ‰Õå•Él‰¥‰t¤°4(€€€ô4(€€€¥˜…¹}µ…¹…•}‰Õå•ÉÌ ¤è4(€€€€€€€Á…å±½…‘l‰•‘¥Ñ}ÕÉ°‰t€ôÕÉ±}™½È ‰•‘¥Ñ}‰Õå•Èˆ°‰Õå•É}¥õ‰Õå•Él‰¥‰t¤4(€€€¥˜…¹}Ù¥•İ}¥¹Ù½¥•Ì ¤è4(€€€€€€€Á…å±½…‘l‰Á…¥‘}¥¹Ù½¥•}…µ½Õ¹Ğ‰t€ô‰Õå•Él‰Á…¥‘}¥¹Ù½¥•}…µ½Õ¹Ğ‰t4(€€€€€€€Á…å±½…‘l‰½µÁ±•Ñ•‘}¥¹Ù½¥•}…µ½Õ¹Ğ‰t€ô‰Õå•Él‰½µÁ±•Ñ•‘}¥¹Ù½¥•}…µ½Õ¹Ğ‰t4(€€€É•ÑÕÉ¸Á…å±½…4(4(4)‘•˜‰Õå•É}µ…Á}É½İÌ¡İ¡•É•}±…ÕÍ”ôˆÄ€ô€Äˆ°Á…É…µÌô ¤¤è4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€İ¥Ñ ½É‘•É}ÍÑ…ÑÌ…Ì€ 4(€€€€€€€€€€€Í•±•Ğ‰Õå•É}¥°4(€€€€€€€€€€€€€€€€€€½Õ¹Ğ ¨¤…Ìİ½É­}½É‘•É}Ñ½Ñ…°°4(€€€€€€€€€€€€€€€€€€ÍÕ´¡…Í”İ¡•¸ÍÑ…ÑÕÌ€ô€±½Í•œÑ¡•¸€Ä•±Í”€À•¹¤…Ìİ½É­}½É‘•É}½µÁ±•Ñ•4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€İ¡•É”‰Õå•É}¥¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€É½ÕÀ‰ä‰Õå•É}¥4(€€€€€€€€¤°4(€€€€€€€±…ÍÑ}É•Á½ÉÑ}‘…Ñ•Ì…Ì€ 4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥°4(€€€€€€€€€€€€€€€€€€µ…à¡‘…Ñ”¡½…±•Í”¡Í•ÉÙ¥•}É•Á½ÉÑÌ¹…ÑÕ…±}İ½É­}‘…Ñ”°Í•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”°Í•ÉÙ¥•}½É‘•ÉÌ¹ÍÑ…ÉÑ}‘…Ñ”¤¤¤…Ì±…ÍÑ}…ÑÕ…±}‘…Ñ”4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌ½¸Í•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€€€€€İ¡•É”Í•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€€¤°4(€€€€€€€¥¹Ù½¥•}…µ½Õ¹ÑÌ…Ì€ 4(€€€€€€€€€€€Í•±•Ğ¥¹Ù½¥•Ì¹¥°¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥°¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ°¥¹Ù½¥•Ì¹Á…¥‘}…Ğ°4(€€€€€€€€€€€€€€€€€€¥¹Ù½¥•Ì¹Á…åµ•¹Ñ}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€€€€€½…±•Í”¡ÍÕ´¡¥¹Ù½¥•}¥Ñ•µÌ¹…µ½Õ¹Ğ€¨€ Ä€¬¥¹Ù½¥•}¥Ñ•µÌ¹Ñ…á}É…Ñ”€¼€ÄÀÀ¸À¤¤°€À¤…Ì¥¹Ù½¥•}Ñ½Ñ…°4(€€€€€€€€€€€™É½´¥¹Ù½¥•Ì4(€€€€€€€€€€€±•™Ğ©½¥¸¥¹Ù½¥•}¥Ñ•µÌ½¸¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥€ô¥¹Ù½¥•Ì¹¥4(€€€€€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€€€€€É½ÕÀ‰ä¥¹Ù½¥•Ì¹¥4(€€€€€€€€¤°4(€€€€€€€¥¹Ù½¥•}ÍÑ…ÑÌ…Ì€ 4(€€€€€€€€€€€Í•±•ĞÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥°4(€€€€€€€€€€€€€€€€€€ÍÕ´ 4(€€€€€€€€€€€€€€€€€€€€€€…Í”İ¡•¸¥¹Ù½¥•}…µ½Õ¹ÑÌ¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€€€€€€€€€€€€Ñ¡•¸½…±•Í”¡¥¹Ù½¥•}…µ½Õ¹ÑÌ¹Á…åµ•¹Ñ}…µ½Õ¹Ğ°¥¹Ù½¥•}…µ½Õ¹ÑÌ¹¥¹Ù½¥•}Ñ½Ñ…°¤4(€€€€€€€€€€€€€€€€€€€€€€•±Í”€À•¹4(€€€€€€€€€€€€€€€€€€€¤…ÌÁ…¥‘}¥¹Ù½¥•}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€€€€€ÍÕ´ 4(€€€€€€€€€€€€€€€€€€€€€€…Í”İ¡•¸¥¹Ù½¥•}…µ½Õ¹ÑÌ¹ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ4(€€€€€€€€€€€€€€€€€€€€€€Ñ¡•¸¥¹Ù½¥•}…µ½Õ¹ÑÌ¹¥¹Ù½¥•}Ñ½Ñ…°4(€€€€€€€€€€€€€€€€€€€€€€•±Í”€À•¹4(€€€€€€€€€€€€€€€€€€€¤…Ì½µÁ±•Ñ•‘}¥¹Ù½¥•}…µ½Õ¹Ğ4(€€€€€€€€€€€™É½´Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€©½¥¸¥¹Ù½¥•}…µ½Õ¹ÑÌ½¸¥¹Ù½¥•}…µ½Õ¹ÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ôÍ•ÉÙ¥•}½É‘•ÉÌ¹¥4(€€€€€€€€€€€İ¡•É”Í•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥¥Ì¹½Ğ¹Õ±°4(€€€€€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}½É‘•ÉÌ¹‰Õå•É}¥4(€€€€€€€€¤4(€€€€€€€Í•±•Ğ‰Õå•ÉÌ¸¨°4(€€€€€€€€€€€€€€½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì½İ¹•É}¹…µ”°4(€€€€€€€€€€€€€€½İ¹•ÉÌ¹½İ¹•É}¹Õµ‰•È…Ì½İ¹•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°½Õ¹ÑÉå}•¸¹¹…µ”°‰Õå•ÉÌ¹½Õ¹ÑÉä°‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½É‘•É}ÍÑ…ÑÌ¹İ½É­}½É‘•É}Ñ½Ñ…°°€À¤…Ìİ½É­}½É‘•É}Ñ½Ñ…°°4(€€€€€€€€€€€€€€½…±•Í”¡½É‘•É}ÍÑ…ÑÌ¹İ½É­}½É‘•É}½µÁ±•Ñ•°€À¤…Ìİ½É­}½É‘•É}½µÁ±•Ñ•°4(€€€€€€€€€€€€€€±…ÍÑ}É•Á½ÉÑ}‘…Ñ•Ì¹±…ÍÑ}…ÑÕ…±}‘…Ñ”…Ì±…ÍÑ}…ÑÕ…±}‘…Ñ”°4(€€€€€€€€€€€€€€½…±•Í”¡¥¹Ù½¥•}ÍÑ…ÑÌ¹Á…¥‘}¥¹Ù½¥•}…µ½Õ¹Ğ°€À¤…ÌÁ…¥‘}¥¹Ù½¥•}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€½…±•Í”¡¥¹Ù½¥•}ÍÑ…ÑÌ¹½µÁ±•Ñ•‘}¥¹Ù½¥•}…µ½Õ¹Ğ°€À¤…Ì½µÁ±•Ñ•‘}¥¹Ù½¥•}…µ½Õ¹Ğ4(€€€€€€€™É½´‰Õå•ÉÌ4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}•¸4(€€€€€€€€€½¸½Õ¹ÑÉå}•¸¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}•¸¹±…¹Õ…•}½‘”€ô€•¸œ4(€€€€€€€±•™Ğ©½¥¸½É‘•É}ÍÑ…ÑÌ½¸½É‘•É}ÍÑ…ÑÌ¹‰Õå•É}¥€ô‰Õå•ÉÌ¹¥4(€€€€€€€±•™Ğ©½¥¸±…ÍÑ}É•Á½ÉÑ}‘…Ñ•Ì½¸±…ÍÑ}É•Á½ÉÑ}‘…Ñ•Ì¹‰Õå•É}¥€ô‰Õå•ÉÌ¹¥4(€€€€€€€±•™Ğ©½¥¸¥¹Ù½¥•}ÍÑ…ÑÌ½¸¥¹Ù½¥•}ÍÑ…ÑÌ¹‰Õå•É}¥€ô‰Õå•ÉÌ¹¥4(€€€€€€€İ¡•É”íİ¡•É•}±…ÕÍ•ô4(€€€€€€€½É‘•È‰ä½İ¹•É}¹…µ”°‰Õå•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹¥4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡ÕÉÉ•¹Ñ}±…¹Õ…” ¤°€©Á…É…µÌ¤°4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ½µ…Àˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}½É‘•É}µ…À ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤…¹¹½Ğ¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€É½İÌ€ô€ 4(€€€€€€€‰Õå•É}µ…Á}É½İÌ ‰‰Õå•ÉÌ¹±¥•¹Ñ}¥€ô€üˆ°€¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t°¤¤4(€€€€€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤4(€€€€€€€•±Í”‰Õå•É}µ…Á}É½İÌ ¤4(€€€€¤4(€€€‰Õå•ÉÍ}Á…å±½…€ôm‰Õå•É}µ…Á}Á…å±½…¡É½Ü¤™½ÈÉ½Ü¥¸É½İÍt4(€€€½½±•}µ…ÁÍ}‰É½İÍ•É}…Á¥}­•ä€ô•Ñ}½½±•}µ…ÁÍ}‰É½İÍ•É}…Á¥}­•ä ¤4(€€€½µÁ…¹ä€ô•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤4(€€€¡•…‘ÅÕ…ÉÑ•ÉÌ€ô¡•…‘ÅÕ…ÉÑ•ÉÍ}½½É‘¥¹…Ñ•Ì ¤4(€€€¥˜¡•…‘ÅÕ…ÉÑ•ÉÌè4(€€€€€€€¡•…‘ÅÕ…ÉÑ•ÉÌ€ôì4(€€€€€€€€€€€€¨©¡•…‘ÅÕ…ÉÑ•ÉÌ°4(€€€€€€€€€€€€‰¹…µ”ˆè½µÁ…¹ål‰¹…µ”‰t°4(€€€€€€€€€€€€‰…‘‘É•ÍÌˆè½µÁ…¹ål‰…‘‘É•ÍÌ‰t°4(€€€€€€€ô4(€€€É½ÕÑ•}½É¥¥¹}…‘‘É•ÍÌ€ô€¡œ¹ÕÍ•Él‰…‘‘É•ÍÌ‰t½È€ˆˆ¤¹ÍÑÉ¥À ¤½È½µÁ…¹ål‰…‘‘É•ÍÌ‰t4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}µ…À¹¡Ñµ°ˆ°4(€€€€€€€µ…Á}‰Õå•ÉÌõ‰Õå•ÉÍ}Á…å±½…°4(€€€€€€€Í¡½İ}¥¹Ù½¥•}…µ½Õ¹ÑÌõ…¹}Ù¥•İ}¥¹Ù½¥•Ì ¤°4(€€€€€€€¡•…‘ÅÕ…ÉÑ•ÉÌõ¡•…‘ÅÕ…ÉÑ•ÉÌ°4(€€€€€€€½µÁ…¹å}…‘‘É•ÍÌõ½µÁ…¹ål‰…‘‘É•ÍÌ‰t°4(€€€€€€€É½ÕÑ•}½É¥¥¹}…‘‘É•ÍÌõÉ½ÕÑ•}½É¥¥¹}…‘‘É•ÍÌ°4(€€€€€€€•½½‘¥¹}•¹…‰±•õ==%9}9	1°4(€€€€€€€½½±•}µ…ÁÍ}•¹…‰±•õ‰½½°¡½½±•}µ…ÁÍ}‰É½İÍ•É}…Á¥}­•ä¤°4(€€€€€€€½½±•}µ…ÁÍ}‰É½İÍ•É}…Á¥}­•äõ½½±•}µ…ÁÍ}‰É½İÍ•É}…Á¥}­•ä°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ½µ…À½•½½‘”µ¹•áĞˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•½½‘•}¹•áÑ}Í•ÉÙ¥•}½É‘•È ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤…¹¹½Ğ¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¹½Ğ==%9}9	1è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰…Ù…¥±…‰±”ˆè…±Í”°€‰É•µ…¥¹¥¹œˆè€Áô¤°€ÔÀÌ4(€€€±¥•¹Ñ}±…ÕÍ”€ô€‰…¹±¥•¹Ñ}¥€ô€üˆ¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤•±Í”€ˆˆ4(€€€•½½‘•}Á…É…µÌ€ôm==I}YIM%=9t4(€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€•½½‘•}Á…É…µÌ¹…ÁÁ•¹¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t¤4(€€€‰Õå•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ¥™É½´‰Õå•ÉÌ4(€€€€€€€İ¡•É”½…±•Í”¡µ…¹Õ…±}½½É‘¥¹…Ñ•Ì°€À¤€ô€À…¹€ 4(€€€€€€€€€€•½½‘•}ÍÑ…ÑÕÌ¥Ì¹Õ±°½È•½½‘•}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ4(€€€€€€€€€€½È•½½‘•}…‘‘É•ÍÌ¥Ì¹Õ±°4(€€€€€€€€€€½È±½İ•È¡ÑÉ¥´¡•½½‘•}…‘‘É•ÍÌ¤¤€„ô±½İ•È¡ÑÉ¥´¡‘•Ñ…¥±•‘}…‘‘É•ÍÌ¤¤4(€€€€€€€€€€½È½…±•Í”¡•½½‘•}Ù•ÉÍ¥½¸°€œœ¤€„ô€ü4(€€€€€€€€¤4(€€€€€€€í±¥•¹Ñ}±…ÕÍ•ô4(€€€€€€€½É‘•È‰äÉ•…Ñ•‘}…Ğ‘•ÍŒ°¥‘•ÍŒ4(€€€€€€€±¥µ¥Ğ€Ä4(€€€€€€€€ˆˆˆ°4(€€€€€€€•½½‘•}Á…É…µÌ°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ‰Õå•Èè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰…Ù…¥±…‰±”ˆèQÉÕ”°€‰É•µ…¥¹¥¹œˆè€À°€‰‰Õå•Èˆè9½¹•ô¤4(€€€ÑÉäè4(€€€€€€€•½½‘•}‰Õå•È¡‰Õå•Él‰¥‰t¤4(€€€•á•ÁĞQ•µÁ½É…Éå•½½‘¥¹ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€…ÁÀ¹±½•È¹İ…É¹¥¹œ ‰Q•µÁ½É…Éä•½½‘¥¹œ™…¥±ÕÉ”™½È‰Õå•È€•Ìè€•Ìˆ°‰Õå•Él‰¥‰t°•ÉÉ½È¤4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰…Ù…¥±…‰±”ˆèQÉÕ”°€‰Ñ•µÁ½É…Éå}•ÉÉ½ÈˆèQÉÕ•ô¤°€ÔÀÌ4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•™É•Í¡•€ô‰Õå•É}µ…Á}É½İÌ ‰‰Õå•ÉÌ¹¥€ô€üˆ°€¡‰Õå•Él‰¥‰t°¤¥lÁt4(€€€É•µ…¥¹¥¹œ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ½Õ¹Ğ ¨¤…Ì½Õ¹Ğ™É½´‰Õå•ÉÌ4(€€€€€€€İ¡•É”½…±•Í”¡µ…¹Õ…±}½½É‘¥¹…Ñ•Ì°€À¤€ô€À…¹€ 4(€€€€€€€€€€•½½‘•}ÍÑ…ÑÕÌ¥Ì¹Õ±°½È•½½‘•}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ4(€€€€€€€€€€½È•½½‘•}…‘‘É•ÍÌ¥Ì¹Õ±°4(€€€€€€€€€€½È±½İ•È¡ÑÉ¥´¡•½½‘•}…‘‘É•ÍÌ¤¤€„ô±½İ•È¡ÑÉ¥´¡‘•Ñ…¥±•‘}…‘‘É•ÍÌ¤¤4(€€€€€€€€€€½È½…±•Í”¡•½½‘•}Ù•ÉÍ¥½¸°€œœ¤€„ô€ü4(€€€€€€€€¤4(€€€€€€€í±¥•¹Ñ}±…ÕÍ•ô4(€€€€€€€€ˆˆˆ°4(€€€€€€€•½½‘•}Á…É…µÌ°4(€€€€¤¹™•Ñ¡½¹” ¥l‰½Õ¹Ğ‰t4(€€€É•ÑÕÉ¸©Í½¹¥™ä 4(€€€€€€€ì4(€€€€€€€€€€€€‰…Ù…¥±…‰±”ˆèQÉÕ”°4(€€€€€€€€€€€€‰É•µ…¥¹¥¹œˆèÉ•µ…¥¹¥¹œ°4(€€€€€€€€€€€€‰‰Õå•Èˆè‰Õå•É}µ…Á}Á…å±½…¡É•™É•Í¡•¤°4(€€€€€€€ô4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ½µ…À½É•ÑÉäµ™…¥±•ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•ÑÉå}™…¥±•‘}Í•ÉÙ¥•}½É‘•É}•½½‘•Ì ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤…¹¹½Ğ¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€±¥•¹Ñ}±…ÕÍ”€ô€‰…¹±¥•¹Ñ}¥€ô€üˆ¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤•±Í”€ˆˆ4(€€€Á…É…µÌ€ômœ¹ÕÍ•Él‰±¥•¹Ñ}¥‰ut¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤•±Í”mt4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”‰Õå•ÉÌ4(€€€€€€€Í•Ğ•½½‘•}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ°•½½‘•}…ÑÑ•µÁÑ•‘}…Ğ€ô¹Õ±°°•½½‘•}Ù•ÉÍ¥½¸€ô¹Õ±°4(€€€€€€€İ¡•É”•½½‘•}ÍÑ…ÑÕÌ€ô€™…¥±•œ…¹½…±•Í”¡µ…¹Õ…±}½½É‘¥¹…Ñ•Ì°€À¤€ô€À4(€€€€€€€í±¥•¹Ñ}±…ÕÍ•ô4(€€€€€€€€ˆˆˆ4(€€€€€€€€°4(€€€€€€€Á…É…µÌ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ•ô¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ½¹•Üˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¹•İ}Í•ÉÙ¥•}½É‘•È ¤è4(€€€¥˜¹½Ğ…¹}É•…Ñ•}Í•ÉÙ¥•}½É‘•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€‰Õå•ÉÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ‰Õå•ÉÌ¸¨°½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì½İ¹•É}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°½Õ¹ÑÉå}•¸¹¹…µ”°‰Õå•ÉÌ¹½Õ¹ÑÉä°‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”4(€€€€€€€™É½´‰Õå•ÉÌ4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}•¸4(€€€€€€€€€½¸½Õ¹ÑÉå}•¸¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}•¸¹±…¹Õ…•}½‘”€ô€•¸œ4(€€€€€€€½É‘•È‰ä½İ¹•É}¹…µ”°‰Õå•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹‰Õå•É}¹Õµ‰•È4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡ÕÉÉ•¹Ñ}±…¹Õ…” ¤°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌ½É‘•È‰ä±¥•¹Ñ}¹Õµ‰•È°¹…µ”ˆ¤¹™•Ñ¡…±° ¤4(€€€İ½É­}½É‘•É}ÑåÁ•Í}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´İ½É­}½É‘•É}ÑåÁ•Ìİ¡•É”¥Í}…Ñ¥Ù”€ô€Ä½É‘•È‰ä½‘”°¹…µ”ˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€½¹ÑÉ…ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ½¹ÑÉ…ÑÌ¸¨°±¥•¹ÑÌ¹¹…µ”…Ì±¥•¹Ñ}¹…µ”4(€€€€€€€™É½´½¹ÑÉ…ÑÌ©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ô½¹ÑÉ…ÑÌ¹±¥•¹Ñ}¥4(€€€€€€€İ¡•É”½¹ÑÉ…ÑÌ¹ÍÑ…ÑÕÌ¥¸€ Í¥¹•œ°€…Ñ¥Ù”œ¤4(€€€€€€€½É‘•È‰ä±¥•¹ÑÌ¹¹…µ”°½¹ÑÉ…ÑÌ¹½¹ÑÉ…Ñ}¹Õµ‰•È4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€µ…¹Õ™…ÑÕÉ•ÉÍ}É½İÌ€ôµ…¹Õ™…ÑÕÉ•É}½ÁÑ¥½¹Ì ¤4(€€€¥˜¹½Ğ‰Õå•ÉÍ}É½İÌè4(€€€€€€€™±…Í  ‹¢¾ß–#RÇ’òk¢º‡š"[î?BîÓš*“®g
ç¢ÖšZgˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰‰Õå•ÉÌˆ¤¥˜…¹}µ…¹…•}‰Õå•ÉÌ ¤•±Í”ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(€€€¥˜¹½Ğ±¥•¹ÑÍ}É½İÌè4(€€€€€€€™±…Í  ‹¢¾ß–#RÇº‡B–Fcš"[î?BîÓš*“–º‹š"ß¢ÖšZgˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰±¥•¹ÑÌˆ¤¥˜¥Í}µ…¹…•È ¤•±Í”ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(€€€¥˜¹½Ğİ½É­}½É‘•É}ÑåÁ•Í}É½İÌè4(€€€€€€€™±…Í  ‹¢¾ß–#RÇº‡B–Fcš"[î?BîÓš*“–Ş—–6WÆï–z/ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰İ½É­}½É‘•É}ÑåÁ•Ìˆ¤¥˜¥Í}µ…¹…•È ¤•±Í”ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€½Õ¹ÑÉä€ô½Õ¹ÑÉå}™É½µ}™½É´ ¤4(€€€€€€€‰Õå•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´‰Õå•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‰Õå•É}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€µ…¹Õ™…ÑÕÉ•È€ôµ…¹Õ™…ÑÕÉ•É}™É½µ}™½É´ ¤4(€€€€€€€İ½É­}½É‘•É}ÑåÁ”€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´İ½É­}½É‘•É}ÑåÁ•Ìİ¡•É”¥€ô€ü…¹¥Í}…Ñ¥Ù”€ô€Äˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰İ½É­}½É‘•É}ÑåÁ•}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€±¥•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´±¥•¹ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±¥•¹Ñ}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€½¹ÑÉ…Ñ}¥€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰½¹ÑÉ…Ñ}¥ˆ¤½È9½¹”4(€€€€€€€½¹ÑÉ…Ğ€ô9½¹”4(€€€€€€€¥˜½¹ÑÉ…Ñ}¥è4(€€€€€€€€€€€½¹ÑÉ…Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´½¹ÑÉ…ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€¡½¹ÑÉ…Ñ}¥°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í¥Ñ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€±¥•¹Ñ}½É‘•É}¹Õµ‰•È€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±¥•¹Ñ}½É‘•É}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€¥˜€ 4(€€€€€€€€€€€¹½Ğ‰Õå•È4(€€€€€€€€€€€½È¹½Ğ±¥•¹Ğ4(€€€€€€€€€€€½È¹½Ğİ½É­}½É‘•É}ÑåÁ”4(€€€€€€€€€€€½È¹½ĞÍ¥Ñ•}…‘‘É•ÍÌ4(€€€€€€€€€€€½È¹½Ğ±¥•¹Ñ}½É‘•É}¹Õµ‰•È4(€€€€€€€€€€€½È€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰µ…¹Õ™…ÑÕÉ•É}¥ˆ¤…¹¹½Ğµ…¹Õ™…ÑÕÉ•È¤4(€€€€€€€€¤è4(€€€€€€€€€€€™±…Í  ‹¢¾ß¦'š.§–º‹š"ß®g
ç–J3–Ş—–6WÆï–z/¾ò3–æÛ–†¯–gšr7–*‡:Ã–rë–rÃ–všr7–*‡¢º‹–6W–>ß‚ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}Í•ÉÙ¥•}½É‘•Èˆ¤¤4(€€€€€€€¥˜½¹ÑÉ…Ñ}¥…¹€¡¹½Ğ½¹ÑÉ…Ğ½È½¹ÑÉ…Ñl‰±¥•¹Ñ}¥‰t€„ô±¥•¹Ñl‰¥‰t¤è4(€€€€€€€€€€€™±…Í  ‹–Ï¢S–B#–B3–ş¦†ï–Æ{’ê;š&¦'–º‹š"ßˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}Í•ÉÙ¥•}½É‘•Èˆ¤¤4(€€€€€€€½É‘•É}¹Õµ‰•È€ô¹•áÑ}Í•ÉÙ¥•}½É‘•É}¹Õµ‰•È ¤4(€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼Í•ÉÙ¥•}½É‘•ÉÌ€ 4(€€€€€€€€€€€€€€€½É‘•É}¹Õµ‰•È°±¥•¹Ñ}¥°½¹ÑÉ…Ñ}¥°‰Õå•É}¥°µ…¹Õ™…ÑÕÉ•É}¥°±¥•¹Ñ}¹…µ”°‰Õå•É}½¹Ñ…Ñ}¹…µ”°‰Õå•É}½¹Ñ…Ñ}‘•Ñ…¥±Ì°4(€€€€€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ°±¥•¹Ñ}½É‘•É}¹Õµ‰•È°ÍÑ…ÉÑ}‘…Ñ”°İ½É­}½É‘•É}ÑåÁ•}¥°4(€€€€€€€€€€€€€€€É•¥½¹}½‘”°½Õ¹ÑÉå}½‘”°É•…Ñ•‘}‰ä°É•…Ñ•‘}…Ğ4(€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€€±¥•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€½¹ÑÉ…Ñl‰¥‰t¥˜½¹ÑÉ…Ğ•±Í”9½¹”°4(€€€€€€€€€€€€€€€‰Õå•Él‰¥‰t°4(€€€€€€€€€€€€€€€µ…¹Õ™…ÑÕÉ•Él‰¥‰t¥˜µ…¹Õ™…ÑÕÉ•È•±Í”‰Õå•Él‰µ…¹Õ™…ÑÕÉ•É}¥‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰¹…µ”‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰½¹Ñ…Ñ}¹…µ”‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰½¹Ñ…Ñ}‘•Ñ…¥±Ì‰t°4(€€€€€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ°4(€€€€€€€€€€€€€€€±¥•¹Ñ}½É‘•É}¹Õµ‰•È°4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÑ…ÉÑ}‘…Ñ”ˆ¤½È9½¹”°4(€€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•l‰¥‰t°4(€€€€€€€€€€€€€€€½Õ¹ÑÉål‰É•¥½¹}½‘”‰t°4(€€€€€€€€€€€€€€€½Õ¹ÑÉål‰½‘”‰t°4(€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰É•…Ñ”ˆ°€‰Í•ÉÙ¥•}½É‘•Èˆ°ÕÉÍ½È¹±…ÍÑÉ½İ¥°½É‘•É}¹Õµ‰•È°˜‹®g
ç¾òií‰Õå•Él¹…µ”uôˆ¤4(€€€€€€€™½±‘•É}É•…Ñ•€ôQÉÕ”4(€€€€€€€ÑÉäè4(€€€€€€€€€€€•¹ÍÕÉ•}Í•ÉÙ¥•}½É‘•É}Á¥ÑÕÉ•}™½±‘•È¡½É‘•É}¹Õµ‰•È¤4(€€€€€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€€€€€™½±‘•É}É•…Ñ•€ô…±Í”4(€€€€€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰…¥±•Ñ¼É•…Ñ”9LÁ¥ÑÕÉ•Ì™½±‘•È™½ÈÍ•ÉÙ¥”½É‘•È€•Ìˆ°½É‘•É}¹Õµ‰•È¤4(€€€€€€€€€€€™±…Í  ‹–Ş—–6W–ŞË–"o–îë¾ò3’ö9LƒŸ&šZ’îÛ–’ç–"o–îë–’Ç¢Ò—¾ò3¢¾ßšš~—–Ç’ê¯Ÿ&n»–öWš2¢ö÷š"[šv¦fCˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€¥˜™½±‘•É}É•…Ñ•è4(€€€€€€€€€€€™±…Í  ‹–Ş—–6W–ŞË–"o–îë¾ò19LƒŸ&šZ’îÛ–’ç–ŞË¢«–*£–"o–îëˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ9½¹”°4(€€€€€€€±¥•¹ÑÌõ±¥•¹ÑÍ}É½İÌ°4(€€€€€€€‰Õå•ÉÌõ‰Õå•ÉÍ}É½İÌ°4(€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ìõİ½É­}½É‘•É}ÑåÁ•Í}É½İÌ°4(€€€€€€€½¹ÑÉ…ÑÌõ½¹ÑÉ…ÑÍ}É½İÌ°4(€€€€€€€µ…¹Õ™…ÑÕÉ•ÉÌõµ…¹Õ™…ÑÕÉ•ÉÍ}É½İÌ°4(€€€€€€€™½Éµ}Ñ¥Ñ±”ô‹šZÃ–îë–Ş—–6Tˆ°4(€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}½¹±äõ…±Í”°4(€€€€€€€½Õ¹ÑÉ¥•Ìõ½Õ¹ÑÉå}É½İÌ ¤°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½•‘¥Ğˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•‘¥Ñ}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜¹½Éµ…±¥é•‘}É½±” ¤¥¸ì‰•µÁ±½å•”ˆ°€‰•áÑ•É¹…±}•µÁ±½å•”ˆ°€‰•áÑ•É¹…±}µ…¹…•È‰ô…¹½É‘•Él‰ÍÑ…ÑÕÌ‰t€ôô€‰±½Í•ˆè4(€€€€€€€™±…Í  ‹–ŞË–Ï¦^·j–Ş—–6W’â7¢÷ò[¢úGˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”Í•ÉÙ¥•}½É‘•ÉÌÍ•ĞÍÑ…ÉÑ}‘…Ñ”€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÑ…ÉÑ}‘…Ñ”ˆ¤½È9½¹”°½É‘•É}¥¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ˆ°4(€€€€€€€€€€€€€€€€‰Í•ÉÙ¥•}½É‘•Èˆ°4(€€€€€€€€€€€€€€€½É‘•É}¥°4(€€€€€€€€€€€€€€€½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°4(€€€€€€€€€€€€€€€€‹’ş»šRç–Ş—–6W–ò–/š^—šr|ˆ°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€™±…Í  ‹–Ş—–6W–ò–/š^—šr–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}™½É´¹¡Ñµ°ˆ°4(€€€€€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€€€€€±¥•¹ÑÌõmt°4(€€€€€€€€€€€‰Õå•ÉÌõmt°4(€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ìõmt°4(€€€€€€€€€€€™½Éµ}Ñ¥Ñ±”ô‹’ş»šRç–Ş—–6W–ò–/š^—šr|ˆ°4(€€€€€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}½¹±äõQÉÕ”°4(€€€€€€€€€€€½Õ¹ÑÉ¥•Ìõmt°4(€€€€€€€€¤4(€€€‰Õå•ÉÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ‰Õå•ÉÌ¸¨°½…±•Í”¡½İ¹•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹½İ¹•È¤…Ì½İ¹•É}¹…µ”°4(€€€€€€€€€€€€€€½…±•Í”¡½Õ¹ÑÉå}±½…°¹¹…µ”°½Õ¹ÑÉå}é ¹¹…µ”°½Õ¹ÑÉå}•¸¹¹…µ”°‰Õå•ÉÌ¹½Õ¹ÑÉä°‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”¤…Ì½Õ¹ÑÉå}¹…µ”4(€€€€€€€™É½´‰Õå•ÉÌ4(€€€€€€€±•™Ğ©½¥¸½İ¹•ÉÌ½¸½İ¹•ÉÌ¹¥€ô‰Õå•ÉÌ¹½İ¹•É}¥4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}±½…°4(€€€€€€€€€½¸½Õ¹ÑÉå}±½…°¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}±½…°¹±…¹Õ…•}½‘”€ô€ü4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}é 4(€€€€€€€€€½¸½Õ¹ÑÉå}é ¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}é ¹±…¹Õ…•}½‘”€ô€é µ8œ4(€€€€€€€±•™Ğ©½¥¸½Õ¹ÑÉå}ÑÉ…¹Í±…Ñ¥½¹Ì½Õ¹ÑÉå}•¸4(€€€€€€€€€½¸½Õ¹ÑÉå}•¸¹½Õ¹ÑÉå}½‘”€ô‰Õå•ÉÌ¹½Õ¹ÑÉå}½‘”…¹½Õ¹ÑÉå}•¸¹±…¹Õ…•}½‘”€ô€•¸œ4(€€€€€€€½É‘•È‰ä½İ¹•É}¹…µ”°‰Õå•ÉÌ¹¹…µ”°‰Õå•ÉÌ¹‰Õå•É}¹Õµ‰•È4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡ÕÉÉ•¹Ñ}±…¹Õ…” ¤°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌ½É‘•È‰ä±¥•¹Ñ}¹Õµ‰•È°¹…µ”ˆ¤¹™•Ñ¡…±° ¤4(€€€İ½É­}½É‘•É}ÑåÁ•Í}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ€¨™É½´İ½É­}½É‘•É}ÑåÁ•Ì4(€€€€€€€İ¡•É”¥Í}…Ñ¥Ù”€ô€Ä½È¥€ô€ü4(€€€€€€€½É‘•È‰ä¥Í}…Ñ¥Ù”‘•ÍŒ°½‘”°¹…µ”4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡½É‘•Él‰İ½É­}½É‘•É}ÑåÁ•}¥‰t°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€½¹ÑÉ…ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ½¹ÑÉ…ÑÌ¸¨°±¥•¹ÑÌ¹¹…µ”…Ì±¥•¹Ñ}¹…µ”4(€€€€€€€™É½´½¹ÑÉ…ÑÌ©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ô½¹ÑÉ…ÑÌ¹±¥•¹Ñ}¥4(€€€€€€€İ¡•É”½¹ÑÉ…ÑÌ¹ÍÑ…ÑÕÌ¥¸€ Í¥¹•œ°€…Ñ¥Ù”œ¤½È½¹ÑÉ…ÑÌ¹¥€ô€ü4(€€€€€€€½É‘•È‰ä±¥•¹ÑÌ¹¹…µ”°½¹ÑÉ…ÑÌ¹½¹ÑÉ…Ñ}¹Õµ‰•È4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡½É‘•Él‰½¹ÑÉ…Ñ}¥‰t½È€À°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€µ…¹Õ™…ÑÕÉ•ÉÍ}É½İÌ€ôµ…¹Õ™…ÑÕÉ•É}½ÁÑ¥½¹Ì ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€½Õ¹ÑÉä€ô½Õ¹ÑÉå}™É½µ}™½É´¡½É‘•Él‰½Õ¹ÑÉå}½‘”‰t¤4(€€€€€€€‰Õå•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´‰Õå•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‰Õå•É}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€µ…¹Õ™…ÑÕÉ•È€ôµ…¹Õ™…ÑÕÉ•É}™É½µ}™½É´ ¤4(€€€€€€€İ½É­}½É‘•É}ÑåÁ”€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´İ½É­}½É‘•É}ÑåÁ•Ìİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰İ½É­}½É‘•É}ÑåÁ•}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€±¥•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´±¥•¹ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±¥•¹Ñ}¥ˆ¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€½¹ÑÉ…Ñ}¥€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰½¹ÑÉ…Ñ}¥ˆ¤½È9½¹”4(€€€€€€€½¹ÑÉ…Ğ€ô9½¹”4(€€€€€€€¥˜½¹ÑÉ…Ñ}¥è4(€€€€€€€€€€€½¹ÑÉ…Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´½¹ÑÉ…ÑÌİ¡•É”¥€ô€üˆ°€¡½¹ÑÉ…Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í¥Ñ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€¥˜€ 4(€€€€€€€€€€€¹½Ğ‰Õå•È4(€€€€€€€€€€€½È¹½Ğ±¥•¹Ğ4(€€€€€€€€€€€½È¹½Ğİ½É­}½É‘•É}ÑåÁ”4(€€€€€€€€€€€½È¹½ĞÍ¥Ñ•}…‘‘É•ÍÌ4(€€€€€€€€€€€½È€¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰µ…¹Õ™…ÑÕÉ•É}¥ˆ¤…¹¹½Ğµ…¹Õ™…ÑÕÉ•È¤4(€€€€€€€€¤è4(€€€€€€€€€€€™±…Í  ‹¢¾ß¦'š.§šr'šV#j–º‹š"ß®g
ç–J3–Ş—–6WÆï–z/¾ò3–æÛ–†¯–gšr7–*‡:Ã–rë–rÃ–vˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}½É‘•Èˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€¥˜½¹ÑÉ…Ñ}¥…¹€¡¹½Ğ½¹ÑÉ…Ğ½È½¹ÑÉ…Ñl‰±¥•¹Ñ}¥‰t€„ô±¥•¹Ñl‰¥‰t¤è4(€€€€€€€€€€€™±…Í  ‹–Ï¢S–B#–B3–ş¦†ï–Æ{’ê;š&¦'–º‹š"ßˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}½É‘•Èˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€ÕÁ‘…Ñ”Í•ÉÙ¥•}½É‘•ÉÌ4(€€€€€€€€€€€Í•Ğ±¥•¹Ñ}¥€ô€ü°½¹ÑÉ…Ñ}¥€ô€ü°‰Õå•É}¥€ô€ü°µ…¹Õ™…ÑÕÉ•É}¥€ô€ü°±¥•¹Ñ}¹…µ”€ô€ü°‰Õå•É}½¹Ñ…Ñ}¹…µ”€ô€ü°‰Õå•É}½¹Ñ…Ñ}‘•Ñ…¥±Ì€ô€ü°4(€€€€€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ€ô€ü°±¥•¹Ñ}½É‘•É}¹Õµ‰•È€ô€ü°ÍÑ…ÉÑ}‘…Ñ”€ô€ü°4(€€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•}¥€ô€ü°ÍÑ…ÑÕÌ€ô€ü°É•¥½¹}½‘”€ô€ü°½Õ¹ÑÉå}½‘”€ô€ü4(€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€±¥•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€½¹ÑÉ…Ñl‰¥‰t¥˜½¹ÑÉ…Ğ•±Í”9½¹”°4(€€€€€€€€€€€€€€€‰Õå•Él‰¥‰t°4(€€€€€€€€€€€€€€€µ…¹Õ™…ÑÕÉ•Él‰¥‰t¥˜µ…¹Õ™…ÑÕÉ•È•±Í”‰Õå•Él‰µ…¹Õ™…ÑÕÉ•É}¥‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰¹…µ”‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰½¹Ñ…Ñ}¹…µ”‰t°4(€€€€€€€€€€€€€€€‰Õå•Él‰½¹Ñ…Ñ}‘•Ñ…¥±Ì‰t°4(€€€€€€€€€€€€€€€Í¥Ñ•}…‘‘É•ÍÌ°4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±¥•¹Ñ}½É‘•É}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÑ…ÉÑ}‘…Ñ”ˆ¤½È9½¹”°4(€€€€€€€€€€€€€€€İ½É­}½É‘•É}ÑåÁ•l‰¥‰t°4(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÑ…ÑÕÌˆ°€‰½Á•¸ˆ¤°4(€€€€€€€€€€€€€€€½Õ¹ÑÉål‰É•¥½¹}½‘”‰t°4(€€€€€€€€€€€€€€€½Õ¹ÑÉål‰½‘”‰t°4(€€€€€€€€€€€€€€€½É‘•É}¥°4(€€€€€€€€€€€€¤°4(€€€€€€€€¤4(€€€€€€€±½}…Ñ¥½¸ ‰ÕÁ‘…Ñ”ˆ°€‰Í•ÉÙ¥•}½É‘•Èˆ°½É‘•É}¥°½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°€‹’ş»šRç–Ş—–6W’ş‡š¼ˆ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹–Ş—–6W–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€±¥•¹ÑÌõ±¥•¹ÑÍ}É½İÌ°4(€€€€€€€‰Õå•ÉÌõ‰Õå•ÉÍ}É½İÌ°4(€€€€€€€İ½É­}½É‘•É}ÑåÁ•Ìõİ½É­}½É‘•É}ÑåÁ•Í}É½İÌ°4(€€€€€€€½¹ÑÉ…ÑÌõ½¹ÑÉ…ÑÍ}É½İÌ°4(€€€€€€€µ…¹Õ™…ÑÕÉ•ÉÌõµ…¹Õ™…ÑÕÉ•ÉÍ}É½İÌ°4(€€€€€€€™½Éµ}Ñ¥Ñ±”ô‹ò[¢úG–Ş—–6Tˆ°4(€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}½¹±äõ…±Í”°4(€€€€€€€½Õ¹ÑÉ¥•Ìõ½Õ¹ÑÉå}É½İÌ ¤°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€É•Á½ÉÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•ĞÍ•ÉÙ¥•}É•Á½ÉÑÌ¸¨°É½ÕÁ}½¹…Ğ¡İ½É­•É}ÕÍ•ÉÌ¹¹…µ”°€œ°€œ¤…Ìİ½É­•É}¹…µ•Ì°4(€€€€€€€€€€€€€€É•…Ñ½É}ÕÍ•ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”4(€€€€€€€™É½´Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€±•™Ğ©½¥¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ½¸Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹É•Á½ÉÑ}¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…Ìİ½É­•É}ÕÍ•ÉÌ½¸İ½É­•É}ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¹ÕÍ•É}¥4(€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…ÌÉ•…Ñ½É}ÕÍ•ÉÌ½¸É•…Ñ½É}ÕÍ•ÉÌ¹¥€ôÍ•ÉÙ¥•}É•Á½ÉÑÌ¹É•…Ñ•‘}‰ä4(€€€€€€€İ¡•É”Í•ÉÙ¥•}É•Á½ÉÑÌ¹Í•ÉÙ¥•}½É‘•É}¥€ô€ü4(€€€€€€€É½ÕÀ‰äÍ•ÉÙ¥•}É•Á½ÉÑÌ¹¥4(€€€€€€€½É‘•È‰äÍ•ÉÙ¥•}É•Á½ÉÑÌ¹É•Á½ÉÑ}‘…Ñ”‘•ÍŒ°Í•ÉÙ¥•}É•Á½ÉÑÌ¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡½É‘•É}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥¹Ù½¥•Í}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ¥¹Ù½¥•Ì¸¨°±¥•¹ÑÌ¹¹…µ”…Ì±¥•¹Ñ}¹…µ”4(€€€€€€€™É½´¥¹Ù½¥•Ì±•™Ğ©½¥¸±¥•¹ÑÌ½¸±¥•¹ÑÌ¹¥€ô¥¹Ù½¥•Ì¹±¥•¹Ñ}¥4(€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ô€ü…¹¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€½É‘•È‰ä¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”‘•ÍŒ°¥¹Ù½¥•Ì¹¥‘•ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡½É‘•É}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€•áÁ•¹Í•Í}É½İÌ€ômt4(€€€•±Í”è4(€€€€€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô•áÁ•¹Í•}…•ÍÍ}™¥±Ñ•È ¤4(€€€€€€€•áÁ•¹Í•}…•ÍÌ€ô˜‰…¹í…•ÍÍ}±…ÕÍ•ôˆ4(€€€€€€€•áÁ•¹Í•}Á…É…µÌ€ôm½É‘•É}¥°€©…•ÍÍ}Á…É…µÍt4(€€€€€€€•áÁ•¹Í•Í}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜ˆˆˆ4(€€€€€€€€€€€Í•±•Ğ•áÁ•¹Í•Ì¸¨°ÕÍ•ÉÌ¹¹…µ”…ÌÉ•…Ñ½É}¹…µ”°‰•¹•™¥¥…É¥•Ì¹¹…µ”…Ì‰•¹•™¥¥…Éå}¹…µ”4(€€€€€€€€€€€™É½´•áÁ•¹Í•Ì±•™Ğ©½¥¸ÕÍ•ÉÌ½¸ÕÍ•ÉÌ¹¥€ô•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä4(€€€€€€€€€€€±•™Ğ©½¥¸ÕÍ•ÉÌ…Ì‰•¹•™¥¥…É¥•Ì½¸‰•¹•™¥¥…É¥•Ì¹¥€ô½…±•Í”¡•áÁ•¹Í•Ì¹‰•¹•™¥¥…Éå}¥°•áÁ•¹Í•Ì¹É•…Ñ•‘}‰ä¤4(€€€€€€€€€€€İ¡•É”•áÁ•¹Í•Ì¹Í•ÉÙ¥•}½É‘•É}¥€ô€üí•áÁ•¹Í•}…•ÍÍô4(€€€€€€€€€€€½É‘•È‰ä•áÁ•¹Í•Ì¹É•…Ñ•‘}…Ğ‘•ÍŒ°•áÁ•¹Í•Ì¹¥‘•ÍŒ4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€•áÁ•¹Í•}Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€•áÁ•¹Í•}Ñ½Ñ…°€ôÍÕ´ ¡•¥µ…°¡ÍÑÈ¡É½İl‰…µ½Õ¹Ğ‰t½È€À¤¤™½ÈÉ½Ü¥¸•áÁ•¹Í•Í}É½İÌ¤°•¥µ…° ˆÀˆ¤¤4(€€€•áÁ•¹Í•}ÕÉÉ•¹ä€ô•áÁ•¹Í•Í}É½İÍlÁul‰ÕÉÉ•¹ä‰t¥˜•áÁ•¹Í•Í}É½İÌ•±Í”€‰UMˆ4(€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•É}¥¤¥˜…¹}Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤•±Í”9½¹”4(€€€‘•±•Ñ•}‰±½­•ÉÌ€ôÍ•ÉÙ¥•}½É‘•É}‘•±•Ñ•}‰±½­•ÉÌ¡½É‘•É}¥¤4(€€€Á¡½Ñ½}ÍÕµµ…Éä€ô€ 4(€€€€€€€Í•ÉÙ¥•}½É‘•É}Á¡½Ñ½}ÍÕµµ…Éä¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€€€€€¥˜…¹}‘•±•Ñ•}Í•ÉÙ¥•}½É‘•È ¤…¹¹½Ğ‘•±•Ñ•}‰±½­•ÉÌ4(€€€€€€€•±Í”ì‰•á¥ÍÑÌˆè…±Í”°€‰¹½¹•µÁÑäˆè…±Í”°€‰™¥±•}½Õ¹Ğˆè€À°€‰‰åÑ•Ìˆè€Áô4(€€€€¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€É•Á½ÉÑÌõÉ•Á½ÉÑÍ}É½İÌ°4(€€€€€€€¥¹Ù½¥•Ìõ¥¹Ù½¥•Í}É½İÌ°4(€€€€€€€•áÁ•¹Í•Ìõ•áÁ•¹Í•Í}É½İÌ°4(€€€€€€€•áÁ•¹Í•}Ñ½Ñ…°õ•áÁ•¹Í•}Ñ½Ñ…°°4(€€€€€€€•áÁ•¹Í•}ÕÉÉ•¹äõ•áÁ•¹Í•}ÕÉÉ•¹ä°4(€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ĞõÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ°4(€€€€€€€…¹}‘•±•Ñ•}½É‘•Èõ…¹}‘•±•Ñ•}Í•ÉÙ¥•}½É‘•È ¤…¹¹½Ğ‘•±•Ñ•}‰±½­•ÉÌ°4(€€€€€€€‘•±•Ñ•}‰±½­•ÉÌõ‘•±•Ñ•}‰±½­•ÉÌ°4(€€€€€€€Á¡½Ñ½}ÍÕµµ…ÉäõÁ¡½Ñ½}ÍÕµµ…Éä°4(€€€€€€€±…‰•±ÌõMQQUM}1	1L°4(€€€€€€€•áÁ•¹Í•}±…‰•±ÌõaA9M}MQQUM}1	1L°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜¹½Ğ…¹}‘•±•Ñ•}Í•ÉÙ¥•}½É‘•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€‰±½­•ÉÌ€ôÍ•ÉÙ¥•}½É‘•É}‘•±•Ñ•}‰±½­•ÉÌ¡½É‘•É}¥¤4(€€€¥˜‰±½­•ÉÌè4(€€€€€€€™±…Í ¡˜‹¢şg’â«–Ş—–6W–ŞËšr%ìœ°€œ¹©½¥¸¡‰±½­•ÉÌ¥÷¾ò3’â7¢÷–"ƒ¦f“ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€Á¡½Ñ½}ÍÕµµ…Éä€ôÍ•ÉÙ¥•}½É‘•É}Á¡½Ñ½}ÍÕµµ…Éä¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€¥˜Á¡½Ñ½}ÍÕµµ…Éål‰¹½¹•µÁÑä‰t…¹É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰½¹™¥Éµ}Á¡½Ñ½}±•…¹ÕÀˆ¤€„ô€‰å•Ìˆè4(€€€€€€€™±…Í  ‹¢şg’â«–Ş—–6WjŸ&n»–öW’â7’âë¦ë¾ò3¢¾ß†»¢º“–B3š^ÛšÂã’æ–"ƒ¦f“–£¦£Ÿ&ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€Á¡½Ñ½}™½±‘•È€ôÍ•ÉÙ¥•}½É‘•É}Á¡½Ñ½}™½±‘•È¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€¥˜Á¡½Ñ½}™½±‘•È¹•á¥ÍÑÌ ¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€Í¡ÕÑ¥°¹ÉµÑÉ•”¡Á¡½Ñ½}™½±‘•È¤4(€€€€€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰U¹…‰±”Ñ¼É•µ½Ù”Á¡½Ñ¼™½±‘•È™½ÈÍ•ÉÙ¥”½É‘•È€•Ìˆ°½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€€€€€€€€€™±…Í  ‹Ÿ&n»–öWšâB–’Ç¢Ò—¾ò3–Ş—–6WšÊ‡šr'–"ƒ¦f“¢¾ßšš~—šr7–*‡–f£šZ’îÛšv¦fC–B;¦7¢¾Wˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´ÕÍ•É}Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”Í•ÉÙ¥•}½É‘•É}¥€ô€üˆ°€¡½É‘•É}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”¥€ô€üˆ°€¡½É‘•É}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰‘•±•Ñ”ˆ°€‰Í•ÉÙ¥•}½É‘•Èˆ°½É‘•É}¥°½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°˜‹®g
ç¾òií½É‘•Él±¥•¹Ñ}¹…µ”uôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—–6W–>+Ÿ&n»–öW–ŞË–"ƒ¦f“¾òo¢şg’â«–Ş—–6W–>ß–>¿’î—¦7šZÃ’öÿR£ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½¥µÁ½ÉĞµ½½±”µÁ¡½Ñ½Ìˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¥µÁ½ÉÑ}½½±•}Á¡½Ñ½Í}™½É}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜¹½Ğ…¹}É•…Ñ•}Í•ÉÙ¥•}É•Á½ÉĞ¡½É‘•È¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í¡…É•}ÕÉ°€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰½½±•}Á¡½Ñ½Í}ÕÉ°ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½Ğ¥Í}½½±•}Á¡½Ñ½Í}Í¡…É•}ÕÉ°¡Í¡…É•}ÕÉ°¤è4(€€€€€€€™±…Í  ‹¢¾ß¢úO–—šr'šV#j½½±”A¡½Ñ½Ìƒ–"’ê¯¦Nûš:—ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€ÑÉäè4(€€€€€€€•¹ÍÕÉ•}Í•ÉÙ¥•}½É‘•É}Á¥ÑÕÉ•}™½±‘•È¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€€€€€Í•ÉÙ¥•}½É‘•É}¥¹½µ¥¹}™½±‘•È¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°€‰½½±”µÁ¡½Ñ½Ìˆ¤4(€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰…¥±•Ñ¼ÁÉ•Á…É”½½±”A¡½Ñ½Ì¥µÁ½ÉĞ™½±‘•È™½ÈÍ•ÉÙ¥”½É‘•È€•Ìˆ°½É‘•Él‰½É‘•É}¹Õµ‰•È‰t¤4(€€€€€€€™±…Í  ‹š^ƒšÎW–g–”9LƒŸ&n»–öW¾ò3¢¾ßšš~—–Ç’ê¯Ÿ&n»–öWš2¢ö÷š"[šv¦fCˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€ÍÑ…ÉÑ}½½±•}Á¡½Ñ½Í}¥µÁ½ÉÑ}©½ˆ¡½É‘•É}¥°œ¹ÕÍ•Él‰¥‰t°Í¡…É•}ÕÉ°¤4(€€€™±…Í  ‰½½±”A¡½Ñ½Ìƒ–"’ê¯¦Nûš:—–¾ó–—’îï–*‡–ŞË–ò–/–º3š"C–B;’òk¦k¢ş¦
»’îÛ–J3ÎïîšÚ#š¿¦k~—’öƒˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹Ğˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´¡½É‘•É}¥¤è4(€€€¥˜¹½Ğ…¹}Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆ…¹¹½Ğ…¹}µ…¹…•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•É}ÍÑ…ÉÑ}‘…Ñ”¡½É‘•È¤4(€€€¥˜ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğè4(€€€€€€€É•ÑÕÉ¸ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•É}¥¤4(€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ğè4(€€€€€€€¥˜¹½Ğ…¹}µ…¹…•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€€€€€™±…Í  ‹¢¾—–Ş—–6W¢şcšÊ‡šr'–Ş—–6WîOº_ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€€ŒØÀ¸Ä¸ÈĞÜè‘¼¹½Ğ…ÕÑ¼µÑÉ…¹Í™•È•Ù•Éä…ÁÁÉ½Ù••µÁ±½å•”•áÁ•¹Í”¸4(€€€€€€€€ŒI•Ù¥•Ü…±°•áÁ•¹Í”±¥¹•Ì€¡¥¹±Õ‘¥¹œÁ•¹‘¥¹œ½¹•Ì¤…¹•áÁ±¥¥Ñ±äÍ•±•Ğ4(€€€€€€€€ŒÑ¡”…ÁÁÉ½Ù•±¥¹•ÌÑ¡…ĞÍ¡½Õ±•¹Ñ•ÈÑ¡”ÕÍÑ½µ•ÈÍ•ÑÑ±•µ•¹Ğ™¥ÉÍĞ¸4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•áÁ•¹Í•}É•Ù¥•Üˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€•±Í”è4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô•¹ÍÕÉ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘™}É•½É¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€€€€€ÕÁ‘…Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Ñ½Ñ…±Ì¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤4(€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€…Ñ¥½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ¥½¸ˆ°€‰Í…Ù”ˆ¤4(€€€€€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰•¹•É…Ñ•}Á‘˜ˆè4(€€€€€€€€€€€€€€€€€€€Á…Ñ €ô‰Õ¥±‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜ 4(€€€€€€€€€€€€€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°4(€€€€€€€€€€€€€€€€€€€€€€€½É‘•È°4(€€€€€€€€€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡Á…Ñ °…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t¤4(€€€€€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰Í•¹‘}•µ…¥°ˆè4(€€€€€€€€€€€€€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷–>G¦¦
»’îÛˆ¤4(€€€€€€€€€€€€€€€€€€€É•¥Á¥•¹Ğ€ô‘•±¥Ù•É}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•µ…¥°¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€€€€€™±…Í ¡˜‹–Ş—–6WîOº_–ŞË–>G¦¢ÌíÉ•¥Á¥•¹Ñ÷ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰•¹•É…Ñ•}¥¹Ù½¥”ˆè4(€€€€€€€€€€€€€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷Rš"C–>G–£ˆ¤4(€€€€€€€€€€€€€€€€€€€¥˜¹½Ğ…¹}É•…Ñ•}¥¹Ù½¥” ¤è4(€€€€€€€€€€€€€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€€€€€€€€€€€€€±¥¹­•‘}¥¹Ù½¥”€ôÍ•ÉÙ¥•}½É‘•É}…Ñ¥Ù•}¥¹Ù½¥”¡½É‘•É}¥¤4(€€€€€€€€€€€€€€€€€€€¥˜±¥¹­•‘}¥¹Ù½¥”è4(€€€€€€€€€€€€€€€€€€€€€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰tè4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¡±¥¹­•‘}¥¹Ù½¥•l‰¥‰t°É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ±¥¹­•‘}¥¹Ù½¥•l‰¥‰t¤¤4(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ 4(€€€€€€€€€€€€€€€€€€€€€€€ÕÉ±}™½È 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¹•İ}¥¹Ù½¥”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õ½É‘•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–>«šr'’şw–¶cšr«š>C’ê“š"[–ŞË¦–n{j–Ş—–6WîOº_–>¿’î—’ş»šRçˆ¤4(€€€€€€€€€€€É½İÌ€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÍ}™É½µ}™½É´¡½É‘•É}¥¤4(€€€€€€€€€€€Í…Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°É½İÌ¤4(€€€€€€€€€€€Ñ½Ñ…±Ì€ôÕÁ‘…Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Ñ½Ñ…±Ì¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°É½İÌ¤4(€€€€€€€€€€€Í…Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}ÕÁ±½…‘Ì¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤4(€€€€€€€€€€€¹•áÑ}ÍÑ…ÑÕÌ€ô€‰ÍÕ‰µ¥ÑÑ•ˆ¥˜…Ñ¥½¸€ôô€‰ÍÕ‰µ¥Ğˆ•±Í”É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ4(€€€€€€€€€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€ü°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°4(€€€€€€€€€€€€€€€€€€€•áÁ•¹Í•}ÑÉ…¹Í™•É}ÕÑ½™™}…Ğ€ô…Í”4(€€€€€€€€€€€€€€€€€€€€€€€İ¡•¸€ü€ô€ÍÕ‰µ¥ÑÑ•œÑ¡•¸½…±•Í”¡•áÁ•¹Í•}ÑÉ…¹Í™•É}ÕÑ½™™}…Ğ°€ü¤4(€€€€€€€€€€€€€€€€€€€€€€€•±Í”•áÁ•¹Í•}ÑÉ…¹Í™•É}ÕÑ½™™}…Ğ4(€€€€€€€€€€€€€€€€€€€•¹4(€€€€€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€¡¹•áÑ}ÍÑ…ÑÕÌ°¹•áÑ}ÍÑ…ÑÕÌ°¹½Ü ¤°É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°¤¤¹™•Ñ¡½¹” ¤4(€€€€€€€€€€€É•µ½Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜¡É•¥µ‰ÕÉÍ•µ•¹Ğ¤4(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰•¹•É…Ñ•}Á‘˜ˆè4(€€€€€€€€€€€€€€€‰Õ¥±‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È°ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤¤4(€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ˆ°4(€€€€€€€€€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€€€€€€€€€˜‹¦G¦Šw¾òiíµ½¹•ä¡Ñ½Ñ…±ÍlÑ½Ñ…±}…µ½Õ¹Ğt¥ôˆ°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰ÍÕ‰µ¥Ğˆè4(€€€€€€€€€€€€€€€¹½Ñ¥™å}É½±” 4(€€€€€€€€€€€€€€€€€€€l‰…‘µ¥¸ˆ°€‰µ…¹…•È‰t°4(€€€€€€€€€€€€€€€€€€€€‹šZÃ–Ş—–6WîOº_–ú–º‡š‚àˆ°4(€€€€€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷š>C’ê“’ê–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èuôƒj–Ş—–6WîOº_¾ò0ˆ4(€€€€€€€€€€€€€€€€€€€€€€€˜‹¦G¦Štíµ½¹•ä¡Ñ½Ñ…±ÍlÑ½Ñ…±}…µ½Õ¹Ğt¥÷ˆ4(€€€€€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€€€€€€€€ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤°4(€€€€€€€€€€€€€€€€€€€•á±Õ‘•}ÕÍ•É}¥‘Ìõíœ¹ÕÍ•Él‰¥‰uô°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€€€€€‰ÍÕ‰µ¥Ğˆ°4(€€€€€€€€€€€€€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€€€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€˜‹¦G¦Šw¾òiíµ½¹•ä¡Ñ½Ñ…±ÍlÑ½Ñ…±}…µ½Õ¹Ğt¥ôˆ°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€™±…Í  ‹–Ş—–6WîOº_–ŞËš>C’ê“î?B–º‡š‚ãˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰•¹•É…Ñ•}Á‘˜ˆè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™¥±•}Á…Ñ ¡É•¥µ‰ÕÉÍ•µ•¹Ğ¤°4(€€€€€€€€€€€€€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€€€€€€€€€€€€€‘½İ¹±½…‘}¹…µ”õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰Í•¹‘}•µ…¥°ˆè4(€€€€€€€€€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷–>G¦¦
»’îÛˆ¤4(€€€€€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€€€€€É•¥Á¥•¹Ğ€ô‘•±¥Ù•É}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•µ…¥°¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€€€€€€€€€€€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°IÕ¹Ñ¥µ•ÉÉ½È¤…Ì•ÉÉ½Èè4(€€€€€€€€€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€™±…Í ¡˜‹–Ş—–6WîOº_–ŞË–>G¦¢ÌíÉ•¥Á¥•¹Ñ÷ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰•¹•É…Ñ•}¥¹Ù½¥”ˆè4(€€€€€€€€€€€€€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷Rš"C–>G–£ˆ¤4(€€€€€€€€€€€€€€€¥˜¹½Ğ…¹}É•…Ñ•}¥¹Ù½¥” ¤è4(€€€€€€€€€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€€€€€€€€€±¥¹­•‘}¥¹Ù½¥”€ôÍ•ÉÙ¥•}½É‘•É}…Ñ¥Ù•}¥¹Ù½¥”¡½É‘•É}¥¤4(€€€€€€€€€€€€€€€¥˜±¥¹­•‘}¥¹Ù½¥”è4(€€€€€€€€€€€€€€€€€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰tè4(€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¡±¥¹­•‘}¥¹Ù½¥•l‰¥‰t°É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ±¥¹­•‘}¥¹Ù½¥•l‰¥‰t¤¤4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ 4(€€€€€€€€€€€€€€€€€€€ÕÉ±}™½È 4(€€€€€€€€€€€€€€€€€€€€€€€€‰¹•İ}¥¹Ù½¥”ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥õ½É‘•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°4(€€€€€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€™±…Í  ‹–Ş—–6WîOº_–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€¥Ñ•µÌ€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤4(€€€¥Ñ•µÌ€ôm‘¥Ğ¡¥Ñ•´¤™½È¥Ñ•´¥¸¥Ñ•µÍt4(€€€™½È¥Ñ•´¥¸¥Ñ•µÌè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€¥Ñ•µl‰•áÁ•¹Í•}Í½ÕÉ•Ì‰t€ô©Í½¸¹±½…‘Ì¡¥Ñ•´¹•Ğ ‰…ÕÑ½}•áÁ•¹Í•}Í½ÕÉ•Ìˆ¤½È€íôœ¤4(€€€€€€€•á•ÁĞ€¡Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è4(€€€€€€€€€€€¥Ñ•µl‰•áÁ•¹Í•}Í½ÕÉ•Ì‰t€ôíô4(€€€€Œƒ’âëš¾?’â«šv—šêC¢†—–¦f’îÛ’ş‡š¿–J3š*—¦R–6W¦Nûš:”4(€€€…±±}Í½ÕÉ•}­•åÌ€ômt4(€€€™½È¥Ñ•´¥¸¥Ñ•µÌè4(€€€€€€€™½È™¥•±‘}Í½ÕÉ•Ì¥¸¥Ñ•µl‰•áÁ•¹Í•}Í½ÕÉ•Ì‰t¹Ù…±Õ•Ì ¤è4(€€€€€€€€€€€™½ÈÍ½ÕÉ”¥¸™¥•±‘}Í½ÕÉ•Ìè4(€€€€€€€€€€€€€€€¥˜Í½ÕÉ”¹•Ğ ‰•áÁ•¹Í•}¥ˆ¤…¹Í½ÕÉ”¹•Ğ ‰±¥¹•}­•äˆ¤è4(€€€€€€€€€€€€€€€€€€€…±±}Í½ÕÉ•}­•åÌ¹…ÁÁ•¹ ¡Í½ÕÉ•l‰•áÁ•¹Í•}¥‰t°Í½ÕÉ•l‰±¥¹•}­•ä‰t¤¤4(€€€Í½ÕÉ•}…ÑÑ…¡µ•¹ÑÌ€ôíô4(€€€¥˜…±±}Í½ÕÉ•}­•åÌè4(€€€€€€€Á±…•¡½±‘•ÉÌ€ô€ˆ°ˆ¹©½¥¸ ˆ ü°ü¤ˆ™½È|¥¸…±±}Í½ÕÉ•}­•åÌ¤4(€€€€€€€Á…É…µÌ€ômÙ…°™½ÈÁ…¥È¥¸…±±}Í½ÕÉ•}­•åÌ™½ÈÙ…°¥¸Á…¥Ét4(€€€€€€€…ÑÑ…¡µ•¹Ñ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜‰Í•±•Ğ¥°•áÁ•¹Í•}¥°•áÁ•¹Í•}¥Ñ•µ}­•ä°½É¥¥¹…±}™¥±•¹…µ”°½¹Ñ•¹Ñ}ÑåÁ”™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ€ˆ4(€€€€€€€€€€€˜‰İ¡•É”€¡•áÁ•¹Í•}¥°•áÁ•¹Í•}¥Ñ•µ}­•ä¤¥¸€¡íÁ±…•¡½±‘•ÉÍô¤ˆ°4(€€€€€€€€€€€Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€€€€€™½È…ÑĞ¥¸…ÑÑ…¡µ•¹Ñ}É½İÌè4(€€€€€€€€€€€­•ä€ô€¡…ÑÑl‰•áÁ•¹Í•}¥‰t°…ÑÑl‰•áÁ•¹Í•}¥Ñ•µ}­•ä‰t¤4(€€€€€€€€€€€Í½ÕÉ•}…ÑÑ…¡µ•¹ÑÌ¹Í•Ñ‘•™…Õ±Ğ¡­•ä°mt¤¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€€€€€‰¥ˆè…ÑÑl‰¥‰t°4(€€€€€€€€€€€€€€€€‰½É¥¥¹…±}™¥±•¹…µ”ˆè…ÑÑl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€‰½¹Ñ•¹Ñ}ÑåÁ”ˆè…ÑÑl‰½¹Ñ•¹Ñ}ÑåÁ”‰t°4(€€€€€€€€€€€ô¤4(€€€™½È¥Ñ•´¥¸¥Ñ•µÌè4(€€€€€€€™½È™¥•±‘}Í½ÕÉ•Ì¥¸¥Ñ•µl‰•áÁ•¹Í•}Í½ÕÉ•Ì‰t¹Ù…±Õ•Ì ¤è4(€€€€€€€€€€€™½ÈÍ½ÕÉ”¥¸™¥•±‘}Í½ÕÉ•Ìè4(€€€€€€€€€€€€€€€Í½ÕÉ•l‰•áÁ•¹Í•}ÕÉ°‰t€ôÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õÍ½ÕÉ•l‰•áÁ•¹Í•}¥‰t¤¥˜Í½ÕÉ”¹•Ğ ‰•áÁ•¹Í•}¥ˆ¤•±Í”9½¹”4(€€€€€€€€€€€€€€€­•ä€ô€¡Í½ÕÉ”¹•Ğ ‰•áÁ•¹Í•}¥ˆ¤°Í½ÕÉ”¹•Ğ ‰±¥¹•}­•äˆ¤¤4(€€€€€€€€€€€€€€€Í½ÕÉ•l‰…ÑÑ…¡µ•¹ÑÌ‰t€ôÍ½ÕÉ•}…ÑÑ…¡µ•¹ÑÌ¹•Ğ¡­•ä°mt¤4(4(€€€±¥¹­•‘}¥¹Ù½¥”€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}±¥¹­•‘}¥¹Ù½¥”¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•É}¥¤4(€€€¥˜±¥¹­•‘}¥¹Ù½¥”…¹É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰t€„ô±¥¹­•‘}¥¹Ù½¥•l‰¥‰tè4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡±¥¹­•‘}¥¹Ù½¥•l‰¥‰t°É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°¤¤¹™•Ñ¡½¹” ¤4(€€€É•Ù¥•İ•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ¹…µ”™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰É•Ù¥•İ•‘}‰ä‰t°¤°4(€€€€¤¹™•Ñ¡½¹” ¤¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰É•Ù¥•İ•‘}‰ä‰t•±Í”9½¹”4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹ĞõÉ•¥µ‰ÕÉÍ•µ•¹Ğ°4(€€€€€€€¥Ñ•µÌõ¥Ñ•µÌ°4(€€€€€€€Ñ½Ñ…±ÌõÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Ñ½Ñ…±Ì 4(€€€€€€€€€€€¥Ñ•µÌ°4(€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰µÉ½}ÍÕÁÁ±¥•Í}Ñ½Ñ…°‰t°4(€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰É•¹Ñ…±}™Õ•±}Ñ½Ñ…°‰t°4(€€€€€€€€¤°4(€€€€€€€É…Ñ•ÌõÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}É…Ñ•Ì ¤°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€±¥¹­•‘}¥¹Ù½¥”õ±¥¹­•‘}¥¹Ù½¥”°4(€€€€€€€É•Ù¥•İ•ÈõÉ•Ù¥•İ•È°4(€€€€€€€•µ…¥±}‘•±¥Ù•Éäõ•µ…¥±}‘•±¥Ù•Éå}ÍÕµµ…Éä ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€•á•ÍÍ¥Ù•}™½±±½İ¥¹}µ¥±•…”õ•á•ÍÍ¥Ù•}™½±±½İ¥¹}µ¥±•…•}É½İÌ¡½É‘•É}¥¤°4(€€€€€€€Á•ÉÍ½¹}‘…åÌõÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á•ÉÍ½¹}‘…åÌ¡½É‘•É}¥¤°4(€€€€€€€½±Õµ¹}Ñ½Ñ…±ÌõÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}½±Õµ¹}Ñ½Ñ…±Ì¡¥Ñ•µÌ¤°4(€€€€€€€…¹}•‘¥Ğõ…¹}µ…¹…•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤…¹É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ô°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô•¹ÍÕÉ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘™}É•½É¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€Á…Ñ €ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™¥±•}Á…Ñ ¡É•¥µ‰ÕÉÍ•µ•¹Ğ¤4(€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹•á¥ÍÑÌ¡Á…Ñ ¤è4(€€€€€€€‰Õ¥±‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È°ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡Á…Ñ °…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t¤4(4(4)…ÁÀ¹•Ğ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½‘½İ¹±½…¹á±Íàˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•á•°¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¥Ñ•µÌ€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¡•…‘•ÉÌ€ôl‹–ê?–>Üˆ°€‹–O–B4ˆ°€‹¦†çn»š^—šr|ˆ°€‹š‚––Ş—š^Øˆ°€‹’ê“¦k–Ş—š^Øˆ°€‹–³–Ç’ê“¦k–Ş—š^Øˆ°€‹–*ƒ>·–Ş—š^Øˆ°€‹–šr–Ş—š^Øˆ°€‹’êë–Ş—¢Òäˆ°€‹’ö?–ºÿ¢Òäˆ°€‹šrë–£¢Òäˆ°€‹¢†3šv;¢Òäˆ°€‹¢ö›¢Òäˆ°€‹šÊç¢Òäˆ°€‹–s¢ö›¢Òäˆ°€‹–ë¢ö›¢Òäˆ°€‹¦3¢/¢Òäˆ°€‹–Û’îXˆ°€‹–B#¢º„‰t4(€€€É½İÌ€ômt4(€€€™½È¥¹‘•à°¥Ñ•´¥¸•¹Õµ•É…Ñ”¡¥Ñ•µÌ°€Ä¤è4(€€€€€€€¥Ñ•´€ô‘¥Ğ¡¥Ñ•´¤4(€€€€€€€•áÁ•¹Í•Ì€ômÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µ}•áÁ•¹Í•}…µ½Õ¹Ğ¡¥Ñ•´°­•ä¤4(€€€€€€€€€€€€€€€€€€€™½È­•ä¥¸€ ‰±½‘¥¹œˆ°€‰…¥É™…É”ˆ°€‰‰……”ˆ°€‰É•¹Ñ…±}…Èˆ°€‰™Õ•°ˆ°€‰Á…É­¥¹œˆ°€‰Ñ…á¤ˆ¥t4(€€€€€€€É½İÌ¹…ÁÁ•¹¡m¥¹‘•à°¥Ñ•µl‰İ½É­•É}¹…µ”‰t°¥Ñ•µl‰ÁÉ½©•Ñ}‘…Ñ”‰t°¥Ñ•µl‰ÍÑ…¹‘…É‘}¡½ÕÉÌ‰t°4(€€€€€€€€€€€€€€€€€€€€¥Ñ•µl‰ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t°¥Ñ•µl‰ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t°¥Ñ•µl‰½Ù•ÉÑ¥µ•}¡½ÕÉÌ‰t°¥Ñ•µl‰¡½±¥‘…å}¡½ÕÉÌ‰t°4(€€€€€€€€€€€€€€€€€€€€¥Ñ•µl‰±…‰½É}Ñ½Ñ…°‰t°€©•áÁ•¹Í•Ì°¥Ñ•µl‰µ¥±•…•}Ñ½Ñ…°‰t°4(€€€€€€€€€€€€€€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µ}•áÁ•¹Í•}…µ½Õ¹Ğ¡¥Ñ•´°€‰½Ñ¡•Èˆ¤°¥Ñ•µl‰Ñ½Ñ…°‰ut¤4(€€€İ½É­‰½½¬€ô‰Õ¥±‘}Í¥µÁ±•}á±Íà¡¡•…‘•ÉÌ°É½İÌ°Í¡••Ñ}¹…µ”ô‹–Ş—–6WîOº\ˆ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡İ½É­‰½½¬°…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”ô‰|ˆ¹©½¥¸¡É”¹ÍÕˆ¡Èlğøèˆ½qqğü©qàÀÀµqàÅ™tœ°€ˆ´ˆ°ÍÑÈ¡Ù…±Õ”¤¤¹ÍÑÉ¥À ˆ€¸ˆ¤™½ÈÙ…±Õ”¥¸€¡½É‘•Él‰½É‘•É}¹Õµ‰•È‰t°½É‘•Él‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰t°€‹–Ş—–6WîOº\¹á±Íàˆ¤¥˜Ù…±Õ”¤°µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹ÍÁÉ•…‘Í¡••Ñµ°¹Í¡••Ğˆ¤4(4(4)…ÁÀ¹•Ğ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô•¹ÍÕÉ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘™}É•½É¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€Á…Ñ €ô‰Õ¥±‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜ 4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°4(€€€€€€€½É‘•È°4(€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€Á…Ñ °4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õÉ•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€½¹‘¥Ñ¥½¹…°õQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½…ÁÁÉ½Ù”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…ÁÁÉ½Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¥˜¹½Ğ…¹}…ÁÁÉ½Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰ÍÕ‰µ¥ÑÑ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–úî?B–º‡š‚ãj–Ş—–6WîOº_–>¿’î—–º‡š‚ã¦k¢şˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ4(€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°É•Ù¥•İ•‘}‰ä€ô€ü°É•Ù¥•İ•‘}…Ğ€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤°4(€€€€¤4(€€€µ•ÍÍ…”€ô€ 4(€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷–ŞË–º‡š‚ã¦k¢ş–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èuôƒj–Ş—–6WîOº_¾ò0ˆ4(€€€€€€€˜‹¦G¦Štíµ½¹•ä¡É•¥µ‰ÕÉÍ•µ•¹ÑlÑ½Ñ…±}…µ½Õ¹Ğt¥÷ˆ4(€€€€¤4(€€€É•…Ñ•}µ•ÍÍ…” 4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰É•…Ñ•‘}‰ä‰t°4(€€€€€€€€‹–Ş—–6WîOº_–ŞË–º‡š‚ã¦k¢şˆ°4(€€€€€€€µ•ÍÍ…”°4(€€€€€€€ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰…ÁÁÉ½Ù”ˆ°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€˜‹¦G¦Šw¾òiíµ½¹•ä¡É•¥µ‰ÕÉÍ•µ•¹ÑlÑ½Ñ…±}…µ½Õ¹Ğt¥ôˆ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—–6WîOº_–ŞË–º‡š‚ã¦k¢şˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½É•ÑÕÉ¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•ÑÕÉ¹}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¥˜¹½Ğ…¹}…ÁÁÉ½Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰ÍÕ‰µ¥ÑÑ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–úî?B–º‡š‚ãj–Ş—–6WîOº_–>¿’î—¦–n{ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€É•…Í½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•ÑÕÉ¹}É•…Í½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½ĞÉ•…Í½¸è4(€€€€€€€™±…Í  ‹¢¾ß–†¯–g¦–n{–:–nƒˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ4(€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€É•ÑÕÉ¹•œ°É•ÑÕÉ¹}É•…Í½¸€ô€ü°É•Ù¥•İ•‘}‰ä€ô€ü°É•Ù¥•İ•‘}…Ğ€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡É•…Í½¸°œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤°4(€€€€¤4(€€€É•…Ñ•}µ•ÍÍ…” 4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰É•…Ñ•‘}‰ä‰t°4(€€€€€€€€‹–Ş—–6WîOº_–ŞË¢Š¯¦–nxˆ°4(€€€€€€€˜‹–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èuôƒj–Ş—–6WîOº_–ŞË¢Š¯¦–n{–:–nƒ¾òiíÉ•…Í½¹ôˆ°4(€€€€€€€ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰É•ÑÕÉ¸ˆ°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€˜‹–:–nƒ¾òiíÉ•…Í½¹ôˆ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—–6WîOº_–ŞË¦–n{ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½É•Í•Ğˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•Í•Ñ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¥˜¹½Ğ…¹}É•Í•Ñ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–ŞË–º‡š‚ã¦k¢şj–Ş—–6WîOº_–>¿’î—¦7ö»ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€±¥¹­•‘}¥¹Ù½¥”€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}±¥¹­•‘}¥¹Ù½¥”¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•Él‰¥‰t¤4(€€€¥˜±¥¹­•‘}¥¹Ù½¥”è4(€€€€€€€™±…Í  ‹¢şg’î÷–Ş—–6WîOº_–ŞËî?Rš"C–>G–£¢¾ß–#–"ƒ¦f“–¾ç–êS–>G–£¾ò3–7¦7ö»–Ş—–6WîOº_–B;¢şo¢†3’ş»šRçˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌ4(€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€‘É…™Ğœ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°É•Ù¥•İ•‘}‰ä€ô¹Õ±°°É•Ù¥•İ•‘}…Ğ€ô¹Õ±°°4(€€€€€€€€€€€¥¹Ù½¥•}¥€ô¹Õ±°4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°¤°4(€€€€¤4(€€€É•µ½Ù•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Á‘˜¡É•¥µ‰ÕÉÍ•µ•¹Ğ¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰É•Í•Ğˆ°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€€‹¦7ö»’âë’şw–¶cšr«š>C’ê“¾ò3–¢ºã¦7šZÃò[¢úDˆ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—–6WîOº_–ŞË¦7ö»’âë’şw–¶cšr«š>C’ê“¾ò3–>¿’î—¦7šZÃò[¢úG’ş»šRç–B;¢¾ß¦7šZÃš>C’ê“î?B–º‡š‚ãˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹ÑÌ¼ñ¥¹ĞéÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤è4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤4(€€€¥˜¹½Ğ…¹}‘•±•Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€™±…Í  ‹–>«šr'’şw–¶cšr«š>C’ê“š"[–ŞË¦–n{j–Ş—–6WîOº_¢6'¢ÿ–>¿’î—–"ƒ¦f“ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}‘¥È€ô½Ì¹Á…Ñ ¹©½¥¸¡UMQ=5I}I%5	UIM59Q}%H°ÍÑÈ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥€ô€üˆ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥Ñ•µÌİ¡•É”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥€ô€üˆ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°¤¤4(€€€Í¡ÕÑ¥°¹ÉµÑÉ•”¡É•¥µ‰ÕÉÍ•µ•¹Ñ}‘¥È°¥¹½É•}•ÉÉ½ÉÌõQÉÕ”¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰‘•±•Ñ”ˆ°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğˆ°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}¥°4(€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰™¥±•}¹…µ”‰t°4(€€€€€€€˜‹–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾ò3*Ûš¾òiíUMQ=5I}I%5	UIM59Q}MQQUM}1	1L¹•Ğ¡É•¥µ‰ÕÉÍ•µ•¹ÑlÍÑ…ÑÕÌt°É•¥µ‰ÕÉÍ•µ•¹ÑlÍÑ…ÑÕÌt¥ôˆ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—–6WîOº_¢6'¢ÿ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)…ÁÀ¹•Ğ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹Ğµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡…ÑÑ…¡µ•¹Ñl‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥‰t¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”õ…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t¤4(4(4)…ÁÀ¹•Ğ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹Ğµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡…ÑÑ…¡µ•¹Ñl‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥‰t¤4(€€€É•ÑÕÉ¸Í…™•}…ÑÑ…¡µ•¹Ñ}É•ÍÁ½¹Í” 4(€€€€€€€ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½ÕÍÑ½µ•ÈµÉ•¥µ‰ÕÉÍ•µ•¹Ğµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡…ÑÑ…¡µ•¹Ñl‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥‰t¤4(€€€¥˜¹½Ğ…¹}µ…¹…•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€™±…Í  ‹–>«šr'’şw–¶cšr«š>C’ê“š"[–ŞË¦–n{j–Ş—–6WîOº_–>¿’î—–"ƒ¦f“¦f’îÛˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t°}…¹¡½Èô‰ÕÍÑ½µ•ÉI•¥µ‰ÕÉÍ•µ•¹ÑÑÑ…¡µ•¹ÑÍM•Ñ¥½¸ˆ¤¤4(€€€ÑÉäè4(€€€€€€€½Ì¹É•µ½Ù”¡ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤¤4(€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€Á…ÍÌ4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰‘•±•Ñ”ˆ°€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ğˆ°…ÑÑ…¡µ•¹Ñ}¥°…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°˜‹–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èuôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹¦f’îÛ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t°}…¹¡½Èô‰ÕÍÑ½µ•ÉI•¥µ‰ÕÉÍ•µ•¹ÑÑÑ…¡µ•¹ÑÍM•Ñ¥½¸ˆ¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½É•Á½ÉÑÌ½¹•Üˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¹•İ}Í•ÉÙ¥•}É•Á½ÉĞ¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜¹½Ğ…¹}É•…Ñ•}Í•ÉÙ¥•}É•Á½ÉĞ¡½É‘•È¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¹½Ğ¥Í}•áÑ•É¹…±}•µÁ±½å•” ¤è4(€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•É}ÍÑ…ÉÑ}‘…Ñ”¡½É‘•È¤4(€€€€€€€¥˜ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğè4(€€€€€€€€€€€É•ÑÕÉ¸ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ4(€€€ÕÍ•ÉÍ}É½İÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•É}½ÁÑ¥½¹Ì¡½É‘•È¤4(€€€É•Á½ÉÑ}İÉ¥Ñ•ÉÌ€ôÉ•Á½ÉÑ}İÉ¥Ñ•É}½ÁÑ¥½¹Ì ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}É•Á½ÉÑ}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸¤è4(€€€€€€€€€€€•á¥ÍÑ¥¹œ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•ĞÉ•Á½ÉÑ}¥™É½´Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•}Ñ½­•¹Ìİ¡•É”Ñ½­•¸€ô€üˆ°4(€€€€€€€€€€€€€€€€¡Í…Ù•}Ñ½­•¸°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€€€€€¥˜•á¥ÍÑ¥¹œ…¹•á¥ÍÑ¥¹l‰É•Á½ÉÑ}¥‰tè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õ•á¥ÍÑ¥¹l‰É•Á½ÉÑ}¥‰t¤¤4(€€€€€€€€€€€™±…Í  ‹¢¾—š^—š*—š¶–r£’şw–¶c¾ò3¢¾ß¢7–gˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}Í•ÉÙ¥•}É•Á½ÉĞˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€İ½É­•É}É½İÌ€ôÉ•Á½ÉÑ}İ½É­•É}É½İÍ}™É½µ}™½É´ ¤4(€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”€ôÁ½ÍÑ•‘}É•Á½ÉÑ}Ñ¥µ” ‰…ÉÉ¥Ù…±}Ñ¥µ”ˆ¤4(€€€€€€€€€€€‘•Á…ÉÑÕÉ•}Ñ¥µ”€ôÁ½ÍÑ•‘}É•Á½ÉÑ}Ñ¥µ” ‰‘•Á…ÉÑÕÉ•}Ñ¥µ”ˆ¤4(€€€€€€€€€€€Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ¡…ÉÉ¥Ù…±}Ñ¥µ”°‘•Á…ÉÑÕÉ•}Ñ¥µ”¤4(€€€€€€€€€€€Ñ½Ñ…±}Ñ¥µ”€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}Ñ½Ñ…±}Ñ¥µ” ¤4(€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}‘É¥Ù¥¹}µ¥±•Ì ¤4(€€€€€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼Í•ÉÙ¥•}É•Á½ÉÑÌ€ 4(€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥°É•Á½ÉÑ}‘…Ñ”°…ÑÕ…±}İ½É­}‘…Ñ”°Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ°ÑÉ…Ù•±}¡½ÕÉÌ°ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ°4(€€€€€€€€€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì°µ¥±•…•}‰¥±±¥¹}µ•Ñ¡½°‘•Á…ÉÑÕÉ•}…‘‘É•ÍÌ°Í¥Ñ•}…‘‘É•ÍÌ°Ñ½Ñ…±}Ñ¥µ”°…‰¥¹•Ñ}¹Õµ‰•È°4(€€€€€€€€€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”°‘•Á…ÉÑÕÉ•}Ñ¥µ”°Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸°É•Á½ÉÑ}İÉ¥Ñ•É}¥°É•…Ñ•‘}‰ä°É•…Ñ•‘}…Ğ°ÕÁ‘…Ñ•‘}…Ğ4(€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…ÑÕ…±}İ½É­}‘…Ñ”ˆ¤½ÈÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ°4(€€€€€€€€€€€€€€€€€€€ÍÕ´¡É½İl‰ÑÉ…Ù•±}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸İ½É­•É}É½İÌ¤°4(€€€€€€€€€€€€€€€€€€€ÍÕ´¡É½İl‰ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸İ½É­•É}É½İÌ¤°4(€€€€€€€€€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰µ¥±•…•}‰¥±±¥¹}µ•Ñ¡½ˆ°€‰Á•É}Á•ÉÍ½¸ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•Á…ÉÑÕÉ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í¥Ñ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…‰¥¹•Ñ}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€‘•Á…ÉÑÕÉ•}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€Á½ÍÑ•‘}É•Á½ÉÑ}İÉ¥Ñ•É}¥ ¤°4(€€€€€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•Á½ÉÑ}¥€ôÕÉÍ½È¹±…ÍÑÉ½İ¥4(€€€€€€€€€€€™¥¹¥Í¡}É•Á½ÉÑ}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°É•Á½ÉÑ}¥¤4(€€€€€€€€€€€Í…Ù•}É•Á½ÉÑ}‘•Ñ…¥±}É½İÌ¡É•Á½ÉÑ}¥¤4(€€€€€€€€€€€Í…Ù•}É•Á½ÉÑ}ÕÁ±½…‘Ì¡É•Á½ÉÑ}¥¤4(€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€‰É•…Ñ”ˆ°4(€€€€€€€€€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉĞˆ°4(€€€€€€€€€€€€€€€É•Á½ÉÑ}¥°4(€€€€€€€€€€€€€€€˜‰í½É‘•Él½É‘•É}¹Õµ‰•Èuô€¼íÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ É•Á½ÉÑ}‘…Ñ”œ¥ôˆ°4(€€€€€€€€€€€€€€€€‹–"o–îë–Ş—’ösš^—š*”ˆ°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}Í•ÉÙ¥•}É•Á½ÉĞˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€™±…Í  ‹–Ş—’ösš^—š*—–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤¤4(€€€Í½ÕÉ•}É•Á½ÉĞ€ô9½¹”4(€€€É•Á½ÉÑ}‘…Ñ„€ôÉ•Á½ÉÑ}™½Éµ}‘•™…Õ±ÑÌ¡½É‘•Èõ½É‘•È¤4(€€€İ½É­•É}É½İÌ€ômt4(€€€Í…Ù•‘}Á…ÉÑÌ€ômíô™½È|¥¸É…¹” Ğ¥t4(€€€É•Á±…•‘}Á…ÉÑÌ€ômíô™½È|¥¸É…¹” Ğ¥t4(€€€½Áå}™É½´€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰½Áå}™É½´ˆ¤4(€€€¥˜½Áå}™É½´¥Ì¹½Ğ9½¹”è4(€€€€€€€¥˜¹½Ğ½Áå}™É½´¹¥Í‘¥¥Ğ ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Í•ÉÙ¥•}É•Á½ÉÑÌˆ°€‰Ù¥•Üˆ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€Í½ÕÉ•}É•Á½ÉĞ°Í½ÕÉ•}½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡¥¹Ğ¡½Áå}™É½´¤¤4(€€€€€€€¥˜Í½ÕÉ•}½É‘•Él‰¥‰t€„ô½É‘•É}¥è4(€€€€€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€€€€€¥˜¥Í}•áÑ•É¹…±}•µÁ±½å•” ¤…¹Í½ÕÉ•}É•Á½ÉÑl‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰tè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€É•Á½ÉÑ}‘…Ñ„€ôÉ•Á½ÉÑ}™½Éµ}‘•™…Õ±ÑÌ¡É•Á½ÉĞõÍ½ÕÉ•}É•Á½ÉĞ¤4(€€€€€€€™½È¹…µ”¥¸€ ‰¥ˆ°€‰É•…Ñ•‘}‰äˆ°€‰É•…Ñ•‘}…Ğˆ°€‰ÕÁ‘…Ñ•‘}…Ğˆ¤è4(€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ„¹Á½À¡¹…µ”°9½¹”¤4(€€€€€€€É•Á½ÉÑ}‘…Ñ…l‰É•Á½ÉÑ}‘…Ñ”‰t€ô‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¤4(€€€€€€€É•Á½ÉÑ}‘…Ñ…l‰…ÑÕ…±}İ½É­}‘…Ñ”‰t€ô‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¤4(€€€€€€€…±±½İ•‘}İ½É­•ÉÌ€ôíÉ½İl‰¥‰t™½ÈÉ½Ü¥¸ÕÍ•ÉÍ}É½İÍô4(€€€€€€€½É¥¥¹…±}İ½É­•ÉÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¡Í½ÕÉ•}É•Á½ÉÑl‰¥‰t¤4(€€€€€€€İ½É­•É}É½İÌ€ômÉ½Ü™½ÈÉ½Ü¥¸½É¥¥¹…±}İ½É­•ÉÌ¥˜É½İl‰¥‰t¥¸…±±½İ•‘}İ½É­•ÉÍt4(€€€€€€€¥˜±•¸¡İ½É­•É}É½İÌ¤€„ô±•¸¡½É¥¥¹…±}İ½É­•ÉÌ¤è4(€€€€€€€€€€€™±…Í  ‹¦£–"–:šr7–*‡’êë–Fc–ŞË’â7–>¿¦'¾ò3¢¾ß¢†—–šr³š²‡š^—š*—jšr7–*‡’êë–Fcˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€¥˜É•Á½ÉÑ}‘…Ñ…l‰É•Á½ÉÑ}İÉ¥Ñ•É}¥‰t¹½Ğ¥¸íÉ½İl‰¥‰t™½ÈÉ½Ü¥¸É•Á½ÉÑ}İÉ¥Ñ•ÉÍôè4(€€€€€€€€€€€É•Á½ÉÑ}‘…Ñ…l‰É•Á½ÉÑ}İÉ¥Ñ•É}¥‰t€ô9½¹”4(€€€€€€€Í…Ù•‘}Á…ÉÑÌ€ô±¥ÍĞ¡É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•‘}Á…ÉÑÌˆ°Í½ÕÉ•}É•Á½ÉÑl‰¥‰t¤¤½ÈÍ…Ù•‘}Á…ÉÑÌ4(€€€€€€€É•Á±…•‘}Á…ÉÑÌ€ô±¥ÍĞ¡É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}É•Á±…•‘}Á…ÉÑÌˆ°Í½ÕÉ•}É•Á½ÉÑl‰¥‰t¤¤½ÈÉ•Á±…•‘}Á…ÉÑÌ4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€É•Á½ÉĞõÉ•Á½ÉÑ}‘…Ñ„°4(€€€€€€€Í½ÕÉ•}É•Á½ÉĞõÍ½ÕÉ•}É•Á½ÉĞ°4(€€€€€€€ÕÍ•ÉÌõÕÍ•ÉÍ}É½İÌ°4(€€€€€€€É•Á½ÉÑ}İÉ¥Ñ•ÉÌõÉ•Á½ÉÑ}İÉ¥Ñ•ÉÌ°4(€€€€€€€İ½É­•É}É½İÌõİ½É­•É}É½İÌ°4(€€€€€€€Í…Ù•‘}Á…ÉÑÌõÍ…Ù•‘}Á…ÉÑÌ°4(€€€€€€€É•Á±…•‘}Á…ÉÑÌõÉ•Á±…•‘}Á…ÉÑÌ°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ À¤°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€€€€¥Í}•‘¥Ğõ…±Í”°4(€€€€€€€…¹}•‘¥Ñ}É•Á½ÉĞõQÉÕ”°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µÉ•Á½ÉÑÌ¼ñ¥¹ĞéÉ•Á½ÉÑ}¥ø½•‘¥Ğˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤è4(€€€É•Á½ÉĞ°½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤4(€€€¥˜¥Í}•áÑ•É¹…±}•µÁ±½å•” ¤…¹É•Á½ÉÑl‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰tè4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÕÍ•ÉÍ}É½İÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•É}½ÁÑ¥½¹Ì¡½É‘•È°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤4(€€€É•Á½ÉÑ}İÉ¥Ñ•ÉÌ€ôÉ•Á½ÉÑ}İÉ¥Ñ•É}½ÁÑ¥½¹Ì ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€¥˜¥Í}•áÑ•É¹…±}µ…¹…•È ¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}É•Á½ÉÑ}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°É•Á½ÉÑ}¥¤è4(€€€€€€€€€€€™±…Í  ‹¢¾—š^—š*—–ŞËî?’şw–¶c¾ò3¢¾ß–.ÿ¦7–’7š>C’ê“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€İ½É­•É}É½İÌ€ôÉ•Á½ÉÑ}İ½É­•É}É½İÍ}™É½µ}™½É´ ¤4(€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”€ôÁ½ÍÑ•‘}É•Á½ÉÑ}Ñ¥µ” ‰…ÉÉ¥Ù…±}Ñ¥µ”ˆ¤4(€€€€€€€€€€€‘•Á…ÉÑÕÉ•}Ñ¥µ”€ôÁ½ÍÑ•‘}É•Á½ÉÑ}Ñ¥µ” ‰‘•Á…ÉÑÕÉ•}Ñ¥µ”ˆ¤4(€€€€€€€€€€€Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ¡…ÉÉ¥Ù…±}Ñ¥µ”°‘•Á…ÉÑÕÉ•}Ñ¥µ”¤4(€€€€€€€€€€€Ñ½Ñ…±}Ñ¥µ”€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}Ñ½Ñ…±}Ñ¥µ” ¤4(€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì€ô…±Õ±…Ñ•‘}É•Á½ÉÑ}‘É¥Ù¥¹}µ¥±•Ì ¤4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ”Í•ÉÙ¥•}É•Á½ÉÑÌ4(€€€€€€€€€€€€€€€Í•ĞÉ•Á½ÉÑ}‘…Ñ”€ô€ü°…ÑÕ…±}İ½É­}‘…Ñ”€ô€ü°Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ€ô€ü°ÑÉ…Ù•±}¡½ÕÉÌ€ô€ü°ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ€ô€ü°4(€€€€€€€€€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì€ô€ü°µ¥±•…•}‰¥±±¥¹}µ•Ñ¡½€ô€ü°‘•Á…ÉÑÕÉ•}…‘‘É•ÍÌ€ô€ü°Í¥Ñ•}…‘‘É•ÍÌ€ô€ü°Ñ½Ñ…±}Ñ¥µ”€ô€ü°…‰¥¹•Ñ}¹Õµ‰•È€ô€ü°4(€€€€€€€€€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”€ô€ü°‘•Á…ÉÑÕÉ•}Ñ¥µ”€ô€ü°Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸€ô€ü°É•Á½ÉÑ}İÉ¥Ñ•É}¥€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…ÑÕ…±}İ½É­}‘…Ñ”ˆ¤½ÈÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•Á½ÉÑ}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ°4(€€€€€€€€€€€€€€€€€€€ÍÕ´¡É½İl‰ÑÉ…Ù•±}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸İ½É­•É}É½İÌ¤°4(€€€€€€€€€€€€€€€€€€€ÍÕ´¡É½İl‰ÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌ‰t™½ÈÉ½Ü¥¸İ½É­•É}É½İÌ¤°4(€€€€€€€€€€€€€€€€€€€‘É¥Ù¥¹}µ¥±•Ì°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰µ¥±•…•}‰¥±±¥¹}µ•Ñ¡½ˆ°€‰Á•É}Á•ÉÍ½¸ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•Á…ÉÑÕÉ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í¥Ñ•}…‘‘É•ÍÌˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…‰¥¹•Ñ}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€…ÉÉ¥Ù…±}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€‘•Á…ÉÑÕÉ•}Ñ¥µ”°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€Á½ÍÑ•‘}É•Á½ÉÑ}İÉ¥Ñ•É}¥ ¤°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}¥°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€Í…Ù•}É•Á½ÉÑ}‘•Ñ…¥±}É½İÌ¡É•Á½ÉÑ}¥¤4(€€€€€€€€€€€É•±½…Ñ•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ¡É•Á½ÉÑ}¥¤4(€€€€€€€€€€€Í…Ù•}É•Á½ÉÑ}ÕÁ±½…‘Ì¡É•Á½ÉÑ}¥¤4(€€€€€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ˆ°4(€€€€€€€€€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉĞˆ°4(€€€€€€€€€€€€€€€É•Á½ÉÑ}¥°4(€€€€€€€€€€€€€€€˜‰í½É‘•Él½É‘•É}¹Õµ‰•Èuô€¼íÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ É•Á½ÉÑ}‘…Ñ”œ¥ôˆ°4(€€€€€€€€€€€€€€€€‹’ş»šRç–Ş—’ösš^—š*”ˆ°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤¤4(€€€€€€€™±…Í  ‹–Ş—’ösš^—š*—–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤¤4(€€€İ½É­•É}É½İÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¡É•Á½ÉÑ}¥¤4(€€€Í…Ù•‘}Á…ÉÑÌ€ô±¥ÍĞ¡É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•‘}Á…ÉÑÌˆ°É•Á½ÉÑ}¥¤¤½Èmíô™½È|¥¸É…¹” Ğ¥t4(€€€É•Á±…•‘}Á…ÉÑÌ€ô±¥ÍĞ¡É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}É•Á±…•‘}Á…ÉÑÌˆ°É•Á½ÉÑ}¥¤¤½Èmíô™½È|¥¸É…¹” Ğ¥t4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€É•Á½ÉĞõÉ•Á½ÉÑ}™½Éµ}‘•™…Õ±ÑÌ¡É•Á½ÉĞõÉ•Á½ÉĞ¤°4(€€€€€€€ÕÍ•ÉÌõÕÍ•ÉÍ}É½İÌ°4(€€€€€€€É•Á½ÉÑ}İÉ¥Ñ•ÉÌõÉ•Á½ÉÑ}İÉ¥Ñ•ÉÌ°4(€€€€€€€İ½É­•É}É½İÌõİ½É­•É}É½İÌ°4(€€€€€€€Í…Ù•‘}Á…ÉÑÌõÍ…Ù•‘}Á…ÉÑÌ°4(€€€€€€€É•Á±…•‘}Á…ÉÑÌõÉ•Á±…•‘}Á…ÉÑÌ°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ¡É•Á½ÉÑ}¥¤°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€€€€¥Í}•‘¥ĞõQÉÕ”°4(€€€€€€€…¹}•‘¥Ñ}É•Á½ÉĞõ¹½Ğ¥Í}•áÑ•É¹…±}µ…¹…•È ¤°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µÉ•Á½ÉÑÌ¼ñ¥¹ĞéÉ•Á½ÉÑ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤è4(€€€É•Á½ÉĞ°½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤4(€€€¥˜É•Á½ÉÑl‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰t…¹¹½Ğ¥Í}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€…ÑÑ…¡µ•¹ÑÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌİ¡•É”É•Á½ÉÑ}¥€ô€üˆ°4(€€€€€€€€¡É•Á½ÉÑ}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€™½È…ÑÑ…¡µ•¹Ğ¥¸…ÑÑ…¡µ•¹ÑÌè4(€€€€€€€…ÑÑ…¡µ•¹Ñ}Á…Ñ €ôÉ•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½Ì¹É•µ½Ù”¡…ÑÑ…¡µ•¹Ñ}Á…Ñ ¤4(€€€€€€€€€€€ÁÉÕ¹•}•µÁÑå}É•Á½ÉÑ}™½±‘•ÉÌ¡…ÑÑ…¡µ•¹Ñ}Á…Ñ ¤4(€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€Á…ÍÌ4(€€€Í¡ÕÑ¥°¹ÉµÑÉ•”¡½Ì¹Á…Ñ ¹©½¥¸¡IA=IQ}QQ!59QM}%H°ÍÑÈ¡É•Á½ÉÑ}¥¤¤°¥¹½É•}•ÉÉ½ÉÌõQÉÕ”¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌİ¡•É”É•Á½ÉÑ}¥€ô€üˆ°€¡É•Á½ÉÑ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌİ¡•É”É•Á½ÉÑ}¥€ô€üˆ°€¡É•Á½ÉÑ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•‘}Á…ÉÑÌİ¡•É”É•Á½ÉÑ}¥€ô€üˆ°€¡É•Á½ÉÑ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}É•Á±…•‘}Á…ÉÑÌİ¡•É”É•Á½ÉÑ}¥€ô€üˆ°€¡É•Á½ÉÑ}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑÌİ¡•É”¥€ô€üˆ°€¡É•Á½ÉÑ}¥°¤¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰‘•±•Ñ”ˆ°4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉĞˆ°4(€€€€€€€É•Á½ÉÑ}¥°4(€€€€€€€˜‰í½É‘•Él½É‘•É}¹Õµ‰•Èuô€¼íÉ•Á½ÉÑlÉ•Á½ÉÑ}‘…Ñ”uôˆ°4(€€€€€€€€‹–"ƒ¦f“–Ş—’ösš^—š*”ˆ°4(€€€€¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–Ş—’ösš^—š*—–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)‘•˜±½­}¥¹}Í¥Ñ•}É½İÌ ¤è4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€¥˜¹½Ğœ¹ÕÍ•Él‰±¥•¹Ñ}¥‰tè4(€€€€€€€€€€€É•ÑÕÉ¸mt4(€€€€€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ¥°‰Õå•É}¹Õµ‰•È°¹…µ”°‘•Ñ…¥±•‘}…‘‘É•ÍÌ™É½´‰Õå•ÉÌİ¡•É”±¥•¹Ñ}¥€ô€ü½É‘•È‰ä¹…µ”ˆ°4(€€€€€€€€€€€€¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t°¤°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ¥°‰Õå•É}¹Õµ‰•È°¹…µ”°‘•Ñ…¥±•‘}…‘‘É•ÍÌ™É½´‰Õå•ÉÌ½É‘•È‰ä¹…µ”ˆ4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)…ÁÀ¹•Ğ ˆ½µ½‰¥±”µ±½¬µ¥¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜µ½‰¥±•}±½­}¥¸ ¤è4(€€€É••¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ±½­}¥¹}Á¡½Ñ½Ì¸¨°‰Õå•ÉÌ¹¹…µ”…ÌÍ¥Ñ•}¹…µ”4(€€€€€€€™É½´±½­}¥¹}Á¡½Ñ½Ì4(€€€€€€€©½¥¸‰Õå•ÉÌ½¸‰Õå•ÉÌ¹¥€ô±½­}¥¹}Á¡½Ñ½Ì¹‰Õå•É}¥4(€€€€€€€İ¡•É”±½­}¥¹}Á¡½Ñ½Ì¹ÕÍ•É}¥€ô€ü4(€€€€€€€½É‘•È‰ä±½­}¥¹}Á¡½Ñ½Ì¹¥‘•ÍŒ±¥µ¥Ğ€Ô4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡œ¹ÕÍ•Él‰¥‰t°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰µ½‰¥±•}±½­}¥¸¹¡Ñµ°ˆ°4(€€€€€€€Í¥Ñ•Ìõ±½­}¥¹}Í¥Ñ•}É½İÌ ¤°4(€€€€€€€½Á•É…Ñ½É}¹…µ”õœ¹ÕÍ•Él‰¹…µ”‰t°4(€€€€€€€É••¹ĞõÉ••¹Ğ°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…Á¤½µ½‰¥±”µ±½¬µ¥¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÕÁ±½…‘}µ½‰¥±•}±½­}¥¸ ¤è4(€€€Í¥Ñ•}¥€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í¥Ñ•}¥ˆ°€ˆˆ¤4(€€€Í¥Ñ”€ô¹•áĞ ¡É½Ü™½ÈÉ½Ü¥¸±½­}¥¹}Í¥Ñ•}É½İÌ ¤¥˜ÍÑÈ¡É½İl‰¥‰t¤€ôôÍ¥Ñ•}¥¤°9½¹”¤4(€€€¥˜¹½ĞÍ¥Ñ”è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹¢¾ß¦'š.§šr'šV#®g
ç‰ô¤°€ĞÈÈ4(€€€ÑÉäè4(€€€€€€€±…Ñ¥ÑÕ‘”€ô™±½…Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±…Ñ¥ÑÕ‘”ˆ°€ˆˆ¤¤4(€€€€€€€±½¹¥ÑÕ‘”€ô™±½…Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±½¹¥ÑÕ‘”ˆ°€ˆˆ¤¤4(€€€€€€€…ÕÉ…ä€ô™±½…Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…ÕÉ…äˆ°€ˆˆ¤½È€À¤4(€€€€€€€½™™Í•Ñ}µ¥¹ÕÑ•Ì€ô¥¹Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Ñ¥µ•}½™™Í•Ñ}µ¥¹ÕÑ•Ìˆ°€ˆÀˆ¤¤4(€€€€€€€…ÁÑÕÉ•‘}…Ğ€ô‘…Ñ•Ñ¥µ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…ÁÑÕÉ•‘}…Ğˆ°€ˆˆ¤¹É•Á±…” ‰hˆ°€ˆ¬ÀÀèÀÀˆ¤¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š^Û¦^Óš"[–vCš‚š‚ó–ò?’â7š¶†»‰ô¤°€ĞÈÈ4(€€€¥˜¹½Ğ€ ´äÀ€ğô±…Ñ¥ÑÕ‘”€ğô€äÀ…¹€´ÄàÀ€ğô±½¹¥ÑÕ‘”€ğô€ÄàÀ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–vCš‚¢Ú–ëšr'šV#¢2–nÓ‰ô¤°€ĞÈÈ4(€€€¥˜¹½Ğ€ ´ÄĞĞÀ€ğô½™™Í•Ñ}µ¥¹ÕÑ•Ì€ğô€ÄĞĞÀ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹š&O–6‡š^Û¦^Óšr–’k–>«¢÷–&7–B;¢ÂšVĞ€ÈĞƒ–Â?š^Û‰ô¤°€ĞÈÈ4(€€€¥˜…ÁÑÕÉ•‘}…Ğ¹Ñé¥¹™¼¥Ì9½¹”è4(€€€€€€€…ÁÑÕÉ•‘}…Ğ€ô…ÁÑÕÉ•‘}…Ğ¹É•Á±…”¡Ñé¥¹™¼õÑ¥µ•é½¹”¹ÕÑŒ¤4(€€€•áÁ•Ñ•‘}…ÁÑÕÉ”€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤€¬Ñ¥µ•‘•±Ñ„¡µ¥¹ÕÑ•Ìõ½™™Í•Ñ}µ¥¹ÕÑ•Ì¤4(€€€¥˜…‰Ì ¡…ÁÑÕÉ•‘}…Ğ¹…ÍÑ¥µ•é½¹”¡Ñ¥µ•é½¹”¹ÕÑŒ¤€´•áÁ•Ñ•‘}…ÁÑÕÉ”¤¹Ñ½Ñ…±}Í•½¹‘Ì ¤¤€ø€ÌÀÀè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šÂÓ–6Ãš^Û¦^Ó’â;¢ÂšVÓ–ó’â7’â¢Ó¾ò3¢¾ß–"ßšZÃ¦†×¦v‹–B;¦7¢¾W‰ô¤°€ĞÈÈ4(€€€Á¡½Ñ¼€ôÉ•ÅÕ•ÍĞ¹™¥±•Ì¹•Ğ ‰Á¡½Ñ¼ˆ¤4(€€€¥˜¹½ĞÁ¡½Ñ¼è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹šÊ‡šr'šRÛ–"ÃŸ&‰ô¤°€ĞÈÈ4(€€€½¹Ñ•¹Ğ€ôÁ¡½Ñ¼¹É•… ÄÔ€¨€ÄÀÈĞ€¨€ÄÀÈĞ€¬€Ä¤4(€€€¥˜¹½Ğ½¹Ñ•¹Ğ½È±•¸¡½¹Ñ•¹Ğ¤€ø€ÄÔ€¨€ÄÀÈĞ€¨€ÄÀÈĞè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹Ÿ&š^ƒšV#š"[¢Ú¢ş€ÄÕ5‰ô¤°€ĞÈÈ4(€€€ÑÉäè4(€€€€€€€İ¥Ñ %µ…”¹½Á•¸¡	åÑ•Í%<¡½¹Ñ•¹Ğ¤¤…Ì¥µ…”è4(€€€€€€€€€€€¥µ…”¹Ù•É¥™ä ¤4(€€€€€€€€€€€¥˜¥µ…”¹™½Éµ…Ğ¹½Ğ¥¸ì‰)Aˆ°€‰A9‰ôè4(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È4(€€€•á•ÁĞ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆè…±Í”°€‰•ÉÉ½Èˆè€‹–>«š:—–>_šr'šV#j)Aƒš"XA9ƒŸ&‰ô¤°€ĞÈÈ4(4(€€€±½…±}…ÁÑÕÉ”€ô…ÁÑÕÉ•‘}…Ğ¹…ÍÑ¥µ•é½¹”¡…ÁÁ}Ñ¥µ•é½¹” ¤¤4(€€€™½±‘•È€ôÍ¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤€¼€‰±½¬µ¥¹Ìˆ€¼±½…±}…ÁÑÕÉ”¹‘…Ñ” ¤¹¥Í½™½Éµ…Ğ ¤€¼Í¥Ñ•l‰‰Õå•É}¹Õµ‰•È‰t4(€€€™½±‘•È¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤4(€€€™¥±•¹…µ”€ô˜‰í±½…±}…ÁÑÕÉ”¹ÍÑÉ™Ñ¥µ” œ• •4•Lœ¥ôµíœ¹ÕÍ•Él¥uôµíÍ•É•ÑÌ¹Ñ½­•¹}¡•à Ğ¥ô¹©Áœˆ4(€€€Ñ…É•Ğ€ô™½±‘•È€¼™¥±•¹…µ”4(€€€Ñ…É•Ğ¹İÉ¥Ñ•}‰åÑ•Ì¡½¹Ñ•¹Ğ¤4(€€€É•±…Ñ¥Ù•}Á…Ñ €ôÑ…É•Ğ¹É•±…Ñ¥Ù•}Ñ¼¡Í¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤¤¹…Í}Á½Í¥à ¤4(€€€É••¥Ù•‘}…Ğ€ô¹½Ü ¤4(€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼±½­}¥¹}Á¡½Ñ½Ì€ 4(€€€€€€€€€€€‰Õå•É}¥°ÕÍ•É}¥°…ÁÑÕÉ•‘}…Ğ°Í•ÉÙ•É}É••¥Ù•‘}…Ğ°Ñ¥µ•}½™™Í•Ñ}µ¥¹ÕÑ•Ì°4(€€€€€€€€€€€±…Ñ¥ÑÕ‘”°±½¹¥ÑÕ‘”°±½…Ñ¥½¹}…ÕÉ…ä°É•±…Ñ¥Ù•}Á…Ñ °½É¥¥¹…±}™¥±•¹…µ”4(€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€ˆˆˆ°4(€€€€€€€€ 4(€€€€€€€€€€€Í¥Ñ•l‰¥‰t°œ¹ÕÍ•Él‰¥‰t°…ÁÑÕÉ•‘}…Ğ¹¥Í½™½Éµ…Ğ ¤°É••¥Ù•‘}…Ğ°½™™Í•Ñ}µ¥¹ÕÑ•Ì°4(€€€€€€€€€€€±…Ñ¥ÑÕ‘”°±½¹¥ÑÕ‘”°…ÕÉ…ä°É•±…Ñ¥Ù•}Á…Ñ °Á¡½Ñ¼¹™¥±•¹…µ”½È€‰±½¬µ¥¸¹©Áœˆ°4(€€€€€€€€¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰É•…Ñ”ˆ°€‰±½­}¥¹}Á¡½Ñ¼ˆ°ÕÉÍ½È¹±…ÍÑÉ½İ¥°Í¥Ñ•l‰¹…µ”‰t°€‹ï–*£š&O–6‡š.7œˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰µ•ÍÍ…”ˆè€‹š&O–6‡Ÿ&–ŞË’â+’òƒ–"À9Oˆ°€‰¥ˆèÕÉÍ½È¹±…ÍÑÉ½İ¥‘ô¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í¡…É•µÁ¡½Ñ½Ì½‰É½İÍ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‰É½İÍ•}Í¡…É•‘}Á¡½Ñ½Ì ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¹½ĞÍ¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤¹¥Í}‘¥È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä 4(€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€‰…Ù…¥±…‰±”ˆè…±Í”°4(€€€€€€€€€€€€€€€€‰ÕÉÉ•¹Ğˆè€ˆˆ°4(€€€€€€€€€€€€€€€€‰Á…É•¹Ğˆè9½¹”°4(€€€€€€€€€€€€€€€€‰™½±‘•ÉÌˆèmt°4(€€€€€€€€€€€€€€€€‰¥µ…•Ìˆèmt°4(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆèì‰İ…¥Ñ¥¹œˆè€À°€‰ÁÉ½•ÍÍ¥¹œˆè€À°€‰½µÁ±•Ñ•ˆè€À°€‰™…¥±•ˆè€Áô°4(€€€€€€€€€€€ô4(€€€€€€€€¤4(€€€É•ÅÕ•ÍÑ•‘}Á…Ñ €ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…Ñ ˆ°€ˆˆ¤4(€€€É•ÅÕ•ÍÑ•‘}‘…ä€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰‘…äˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜É•ÅÕ•ÍÑ•‘}‘…äè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€É•ÅÕ•ÍÑ•‘}‘…ä€ô‘…Ñ”¹™É½µ¥Í½™½Éµ…Ğ¡É•ÅÕ•ÍÑ•‘}‘…ä¤¹¥Í½™½Éµ…Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€½É‘•É}‘¥È€ôÉ•Í½±Ù•}Í¡…É•‘}Á¡½Ñ¼¡É•ÅÕ•ÍÑ•‘}Á…Ñ °…±±½İ}µ¥ÍÍ¥¹œõQÉÕ”¤4(€€€¥˜¹½Ğ½É‘•É}‘¥È¹¥Í}‘¥È ¤è4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä 4(€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€‰…Ù…¥±…‰±”ˆèQÉÕ”°4(€€€€€€€€€€€€€€€€‰™½±‘•É}•á¥ÍÑÌˆè…±Í”°4(€€€€€€€€€€€€€€€€‰ÕÉÉ•¹ĞˆèÉ•ÅÕ•ÍÑ•‘}Á…Ñ °4(€€€€€€€€€€€€€€€€‰Á…É•¹Ğˆè9½¹”°4(€€€€€€€€€€€€€€€€‰™½±‘•ÉÌˆèmt°4(€€€€€€€€€€€€€€€€‰¥µ…•Ìˆèmt°4(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆèì‰İ…¥Ñ¥¹œˆè€À°€‰ÁÉ½•ÍÍ¥¹œˆè€À°€‰½µÁ±•Ñ•ˆè€À°€‰™…¥±•ˆè€Áô°4(€€€€€€€€€€€ô4(€€€€€€€€¤4(€€€Á¥ÑÕÉ•Í}É½½Ğ€ô½É‘•É}‘¥È€¼€‰Á¥ÑÕÉ•Ìˆ4(€€€ÕÉÉ•¹Ğ€ôÁ¥ÑÕÉ•Í}É½½Ğ€¼É•ÅÕ•ÍÑ•‘}‘…ä¥˜É•ÅÕ•ÍÑ•‘}‘…ä•±Í”Á¥ÑÕÉ•Í}É½½Ğ4(€€€™½±‘•ÉÌ€ômt4(€€€¥˜Á¥ÑÕÉ•Í}É½½Ğ¹¥Í}‘¥È ¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‘…å}™½±‘•ÉÌ€ôÍ½ÉÑ• 4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€•¹ÑÉä4(€€€€€€€€€€€€€€€€€€€™½È•¹ÑÉä¥¸Á¥ÑÕÉ•Í}É½½Ğ¹¥Ñ•É‘¥È ¤4(€€€€€€€€€€€€€€€€€€€¥˜•¹ÑÉä¹¥Í}‘¥È ¤4(€€€€€€€€€€€€€€€€€€€…¹É”¹™Õ±±µ…Ñ ¡È‰q‘ìÑôµq‘ìÉôµq‘ìÉôˆ°•¹ÑÉä¹¹…µ”¤4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€€€€­•äõ±…µ‰‘„•¹ÑÉäè•¹ÑÉä¹¹…µ”°4(€€€€€€€€€€€€€€€É•Ù•ÉÍ”õQÉÕ”°4(€€€€€€€€€€€€¤4(€€€€€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€™½±‘•ÉÌ€ôl4(€€€€€€€€€€€ì‰¹…µ”ˆè™½±‘•È¹¹…µ”°€‰½Õ¹Ğˆè½Õ¹Ñ}Í¡…É•‘}¥µ…•Ì¡™½±‘•È¥ô4(€€€€€€€€€€€™½È™½±‘•È¥¸‘…å}™½±‘•ÉÌ4(€€€€€€€t4(€€€¥˜É•ÅÕ•ÍÑ•‘}‘…ä…¹É•ÅÕ•ÍÑ•‘}‘…ä¹½Ğ¥¸í™½±‘•Él‰¹…µ”‰t™½È™½±‘•È¥¸™½±‘•ÉÍôè4(€€€€€€€™½±‘•ÉÌ¹¥¹Í•ÉĞ À°ì‰¹…µ”ˆèÉ•ÅÕ•ÍÑ•‘}‘…ä°€‰½Õ¹Ğˆè€Áô¤4(€€€¥µ…•Ì€ômt4(€€€¥˜ÕÉÉ•¹Ğ¹¥Í}‘¥È ¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€•¹ÑÉ¥•Ì€ôÍ½ÉÑ• 4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€•¹ÑÉä4(€€€€€€€€€€€€€€€€€€€™½È•¹ÑÉä¥¸ÕÉÉ•¹Ğ¹É±½ˆ ˆ¨ˆ¤4(€€€€€€€€€€€€€€€€€€€¥˜•¹ÑÉä¹¥Í}™¥±” ¤4(€€€€€€€€€€€€€€€€€€€…¹¹½Ğ•¹ÑÉä¹¹…µ”¹ÍÑ…ÉÑÍİ¥Ñ  ˆ¸ˆ¤4(€€€€€€€€€€€€€€€€€€€…¹•¹ÑÉä¹ÍÕ™™¥à¹±½İ•È ¤¹±ÍÑÉ¥À ˆ¸ˆ¤¥¸11=]}%5}aQ9M%=9L4(€€€€€€€€€€€€€€€€€€€…¹€‰•…‘¥Èˆ¹½Ğ¥¸íÁ…ÉĞ¹…Í•™½± ¤™½ÈÁ…ÉĞ¥¸•¹ÑÉä¹É•±…Ñ¥Ù•}Ñ¼¡ÕÉÉ•¹Ğ¤¹Á…ÉÑÍô4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€€€€­•äõ±…µ‰‘„¥Ñ•´è¥Ñ•´¹É•±…Ñ¥Ù•}Ñ¼¡ÕÉÉ•¹Ğ¤¹…Í}Á½Í¥à ¤¹…Í•™½± ¤°4(€€€€€€€€€€€€¤4(€€€€€€€•á•ÁĞ=MÉÉ½Èè4(€€€€€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€€€€€™½È•¹ÑÉä¥¸•¹ÑÉ¥•Ìè4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€•¹ÑÉä¹É•±…Ñ¥Ù•}Ñ¼¡ÕÉÉ•¹Ğ¤4(€€€€€€€€€€€•á•ÁĞY…±Õ•ÉÉ½Èè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€¥˜¹½ĞÙ…±¥‘}¥µ…•}™¥±”¡•¹ÑÉä¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€É•±…Ñ¥Ù”€ôÍ¡…É•‘}Á¡½Ñ½}É•±…Ñ¥Ù”¡•¹ÑÉä¤4(€€€€€€€€€€€‘¥ÍÁ±…å}¹…µ”€ô•¹ÑÉä¹É•±…Ñ¥Ù•}Ñ¼¡ÕÉÉ•¹Ğ¤¹…Í}Á½Í¥à ¤4(€€€€€€€€€€€¥µ…•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰¹…µ”ˆè‘¥ÍÁ±…å}¹…µ”°4(€€€€€€€€€€€€€€€€€€€€‰Á…Ñ ˆèÉ•±…Ñ¥Ù”°4(€€€€€€€€€€€€€€€€€€€€‰Ñ¡Õµ‰¹…¥°ˆèÕÉ±}™½È ‰Í¡…É•‘}Á¡½Ñ½}Ñ¡Õµ‰¹…¥°ˆ°Á…Ñ õÉ•±…Ñ¥Ù”¤°4(€€€€€€€€€€€€€€€€€€€€‰ÁÉ•Ù¥•ÜˆèÕÉ±}™½È ‰Í¡…É•‘}Á¡½Ñ½}ÁÉ•Ù¥•Üˆ°Á…Ñ õÉ•±…Ñ¥Ù”¤°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€€¤4(€€€É•ÑÕÉ¸©Í½¹¥™ä 4(€€€€€€€ì4(€€€€€€€€€€€€‰…Ù…¥±…‰±”ˆèQÉÕ”°4(€€€€€€€€€€€€‰™½±‘•É}•á¥ÍÑÌˆèQÉÕ”°4(€€€€€€€€€€€€‰ÕÉÉ•¹Ğˆè˜‰íÉ•ÅÕ•ÍÑ•‘}Á…Ñ¡ô½íÉ•ÅÕ•ÍÑ•‘}‘…åôˆ¥˜É•ÅÕ•ÍÑ•‘}‘…ä•±Í”É•ÅÕ•ÍÑ•‘}Á…Ñ °4(€€€€€€€€€€€€‰Á…É•¹Ğˆè9½¹”°4(€€€€€€€€€€€€‰™½±‘•ÉÌˆè™½±‘•ÉÌ°4(€€€€€€€€€€€€‰Í•±•Ñ•‘}‘…äˆèÉ•ÅÕ•ÍÑ•‘}‘…ä°4(€€€€€€€€€€€€‰¥µ…•Ìˆè¥µ…•Ì°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè½É‘•É}Á¡½Ñ½}ÍÑ…ÑÕÌ¡½É‘•É}‘¥È¤°4(€€€€€€€ô4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í¡…É•µÁ¡½Ñ½Ì½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}Í¡…É•‘}Á¡½Ñ½Ì ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í•±•Ñ•‘}Á…Ñ¡Ì€ô±¥ÍĞ¡‘¥Ğ¹™É½µ­•åÌ¡Á…Ñ ™½ÈÁ…Ñ ¥¸É•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰Á…Ñ ˆ¤¥˜Á…Ñ ¤¤4(€€€¥˜¹½ĞÍ•±•Ñ•‘}Á…Ñ¡Ìè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€™¥±•Ì€ômt4(€€€™½ÈÉ•±…Ñ¥Ù•}Á…Ñ ¥¸Í•±•Ñ•‘}Á…Ñ¡Ìè4(€€€€€€€Í½ÕÉ•}Á…Ñ °É•±…Ñ¥Ù•}Á…ÉÑÌ€ôÉ•Í½±Ù•}Í¡…É•‘}Á¥ÑÕÉ”¡É•±…Ñ¥Ù•}Á…Ñ ¤4(€€€€€€€™¥±•Ì¹…ÁÁ•¹ ¡Í½ÕÉ•}Á…Ñ °A…Ñ  ©É•±…Ñ¥Ù•}Á…ÉÑÍlÈét¤¹…Í}Á½Í¥à ¤¤¤4(€€€¥˜¹½Ğ™¥±•Ìè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(4(€€€…É¡¥Ù•}É½½Ğ€ôÍ•ÕÉ•}™¥±•¹…µ”¡A…Ñ ¡Í•±•Ñ•‘}Á…Ñ¡ÍlÁt¤¹Á…ÉÑÍlÁt¤½È€‰¹…ÌµÁ¡½Ñ½Ìˆ4(€€€ÕÍ•‘}¹…µ•Ì€ôÍ•Ğ ¤4(€€€…É¡¥Ù•}™¥±”€ôÑ•µÁ™¥±”¹9…µ•‘Q•µÁ½É…Éå¥±”¡ÁÉ•™¥àõ˜‰í…É¡¥Ù•}É½½Ñô´ˆ°ÍÕ™™¥àôˆ¹é¥Àˆ°‘•±•Ñ”õ…±Í”¤4(€€€…É¡¥Ù•}Á…Ñ €ô…É¡¥Ù•}™¥±”¹¹…µ”4(€€€…É¡¥Ù•}™¥±”¹±½Í” ¤4(€€€İ¥Ñ é¥Á™¥±”¹i¥Á¥±”¡…É¡¥Ù•}Á…Ñ °€‰Üˆ°½µÁÉ•ÍÍ¥½¸õé¥Á™¥±”¹i%A}MQ=I¤…Ì…É¡¥Ù”è4(€€€€€€€™½ÈÍ½ÕÉ•}Á…Ñ °‘¥ÍÁ±…å}¹…µ”¥¸™¥±•Ìè4(€€€€€€€€€€€…É¹…µ”€ô˜‰í…É¡¥Ù•}É½½Ñô½í‘¥ÍÁ±…å}¹…µ•ôˆ4(€€€€€€€€€€€¥˜…É¹…µ”¥¸ÕÍ•‘}¹…µ•Ìè4(€€€€€€€€€€€€€€€ÍÑ•´°•áÑ•¹Í¥½¸€ô½Ì¹Á…Ñ ¹ÍÁ±¥Ñ•áĞ¡‘¥ÍÁ±…å}¹…µ”¤4(€€€€€€€€€€€€€€€ÍÕ™™¥à€ô€È4(€€€€€€€€€€€€€€€İ¡¥±”˜‰í…É¡¥Ù•}É½½Ñô½íÍÑ•µôµíÍÕ™™¥áõí•áÑ•¹Í¥½¹ôˆ¥¸ÕÍ•‘}¹…µ•Ìè4(€€€€€€€€€€€€€€€€€€€ÍÕ™™¥à€¬ô€Ä4(€€€€€€€€€€€€€€€…É¹…µ”€ô˜‰í…É¡¥Ù•}É½½Ñô½íÍÑ•µôµíÍÕ™™¥áõí•áÑ•¹Í¥½¹ôˆ4(€€€€€€€€€€€ÕÍ•‘}¹…µ•Ì¹…‘¡…É¹…µ”¤4(€€€€€€€€€€€…É¡¥Ù”¹İÉ¥Ñ”¡Í½ÕÉ•}Á…Ñ °…É¹…µ”õ…É¹…µ”¤4(4(€€€É•ÍÁ½¹Í”€ôÍ•¹‘}™¥±” 4(€€€€€€€…É¡¥Ù•}Á…Ñ °4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½é¥Àˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ˜‰í…É¡¥Ù•}É½½ÑôµÁ¡½Ñ½Ì¹é¥Àˆ°4(€€€€¤4(4(€€€‘•˜É•µ½Ù•}…É¡¥Ù” ¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½Ì¹É•µ½Ù”¡…É¡¥Ù•}Á…Ñ ¤4(€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€Á…ÍÌ4(4(€€€É•ÍÁ½¹Í”¹…±±}½¹}±½Í”¡É•µ½Ù•}…É¡¥Ù”¤4(€€€É•ÑÕÉ¸É•ÍÁ½¹Í”4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í¡…É•µÁ¡½Ñ½Ì½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í¡…É•‘}Á¡½Ñ½}ÁÉ•Ù¥•Ü ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í½ÕÉ•}Á…Ñ °|€ôÉ•Í½±Ù•}Í¡…É•‘}Á¥ÑÕÉ”¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…Ñ ˆ°€ˆˆ¤¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡Í½ÕÉ•}Á…Ñ °…Í}…ÑÑ…¡µ•¹Ğõ…±Í”°½¹‘¥Ñ¥½¹…°õQÉÕ”¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í¡…É•µÁ¡½Ñ½Ì½Ñ¡Õµ‰¹…¥°ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í¡…É•‘}Á¡½Ñ½}Ñ¡Õµ‰¹…¥° ¤è4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í½ÕÉ•}Á…Ñ €ôÉ•Í½±Ù•}Í¡…É•‘}Á¡½Ñ¼¡É•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Á…Ñ ˆ°€ˆˆ¤°É•ÅÕ¥É•}™¥±”õQÉÕ”¤4(€€€É•±…Ñ¥Ù•}Á…ÉÑÌ€ôÍ½ÕÉ•}Á…Ñ ¹É•±…Ñ¥Ù•}Ñ¼¡Í¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤¤¹Á…ÉÑÌ4(€€€¥˜±•¸¡É•±…Ñ¥Ù•}Á…ÉÑÌ¤€øô€Ì…¹É•±…Ñ¥Ù•}Á…ÉÑÍlÅt¹…Í•™½± ¤€ôô€‰Á¥ÑÕÉ•Ìˆè4(€€€€€€€Ñ¡Õµ‰¹…¥±}Á…Ñ €ôÍ¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤€¼É•±…Ñ¥Ù•}Á…ÉÑÍlÁt€¼€‰Ñ¡Õµ‰¹…¥±Ìˆ€¼A…Ñ  ©É•±…Ñ¥Ù•}Á…ÉÑÍlÈét¤4(€€€€€€€¥˜Ñ¡Õµ‰¹…¥±}Á…Ñ ¹¥Í}™¥±” ¤…¹Ù…±¥‘}¥µ…•}™¥±”¡Ñ¡Õµ‰¹…¥±}Á…Ñ ¤è4(€€€€€€€€€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡Ñ¡Õµ‰¹…¥±}Á…Ñ °µ¥µ•ÑåÁ”ô‰¥µ…”½©Á•œˆ°µ…á}…”ôÌØÀÀ¤4(€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€ÑÉäè4(€€€€€€€İ¥Ñ %µ…”¹½Á•¸¡Í½ÕÉ•}Á…Ñ ¤…ÌÍ½ÕÉ”è4(€€€€€€€€€€€Í½ÕÉ”¹Í••¬ À¤4(€€€€€€€€€€€¥µ…”€ô%µ…•=ÁÌ¹•á¥™}ÑÉ…¹ÍÁ½Í”¡Í½ÕÉ”¤4(€€€€€€€€€€€¥˜¥µ…”¹µ½‘”¹½Ğ¥¸ì‰Iˆ°€‰0‰ôè4(€€€€€€€€€€€€€€€‰…­É½Õ¹€ô%µ…”¹¹•Ü ‰Iˆ°¥µ…”¹Í¥é”°€‰İ¡¥Ñ”ˆ¤4(€€€€€€€€€€€€€€€¥˜€‰ˆ¥¸¥µ…”¹•Ñ‰…¹‘Ì ¤è4(€€€€€€€€€€€€€€€€€€€‰…­É½Õ¹¹Á…ÍÑ”¡¥µ…”°µ…Í¬õ¥µ…”¹•Ñ¡…¹¹•° ‰ˆ¤¤4(€€€€€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€€€€€‰…­É½Õ¹¹Á…ÍÑ”¡¥µ…”¹½¹Ù•ÉĞ ‰Iˆ¤¤4(€€€€€€€€€€€€€€€¥µ…”€ô‰…­É½Õ¹4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€¥µ…”€ô¥µ…”¹½¹Ù•ÉĞ ‰Iˆ¤4(€€€€€€€€€€€¥µ…”¹Ñ¡Õµ‰¹…¥°  ĞÈÀ°€ÌÈÀ¤°%µ…”¹I•Í…µÁ±¥¹œ¹19i=L¤4(€€€€€€€€€€€¥µ…”¹Í…Ù”¡‰Õ™™•È°™½Éµ…Ğô‰)Aˆ°ÅÕ…±¥ÑäôÜÀ°½ÁÑ¥µ¥é”õQÉÕ”¤4(€€€•á•ÁĞ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€‰Õ™™•È¹Í••¬ À¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡‰Õ™™•È°µ¥µ•ÑåÁ”ô‰¥µ…”½©Á•œˆ°µ…á}…”ôÌØÀÀ¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µÉ•Á½ÉĞµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡…ÑÑ…¡µ•¹Ñl‰É•Á½ÉÑ}¥‰t¤4(€€€É•ÑÕÉ¸Í…™•}…ÑÑ…¡µ•¹Ñ}É•ÍÁ½¹Í” 4(€€€€€€€É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µÉ•Á½ÉĞµ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}É•Á½ÉÑ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•Á½ÉĞ°½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡…ÑÑ…¡µ•¹Ñl‰É•Á½ÉÑ}¥‰t¤4(€€€ÑÉäè4(€€€€€€€…ÑÑ…¡µ•¹Ñ}Á…Ñ €ôÉ•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤4(€€€€€€€½Ì¹É•µ½Ù”¡…ÑÑ…¡µ•¹Ñ}Á…Ñ ¤4(€€€€€€€ÁÉÕ¹•}•µÁÑå}É•Á½ÉÑ}™½±‘•ÉÌ¡…ÑÑ…¡µ•¹Ñ}Á…Ñ ¤4(€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€Á…ÍÌ4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹¦f’îÛ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•‘¥É•Ñ}…¹¡½È€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•‘¥É•Ñ}…¹¡½Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½ĞÉ”¹™Õ±±µ…Ñ ¡Èˆmµi„µèÀ´å|µt¬ˆ°É•‘¥É•Ñ}…¹¡½È¤è4(€€€€€€€É•‘¥É•Ñ}…¹¡½È€ô€ˆˆ4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑl‰¥‰t¤€¬É•‘¥É•Ñ}…¹¡½È¤4(4(4)…ÁÀ¹•Ğ ˆ½Í•ÉÙ¥”µÉ•Á½ÉÑÌ¼ñ¥¹ĞéÉ•Á½ÉÑ}¥ø½Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Ù¥•İ}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤è4(€€€€ˆˆ‰I•…µ½¹±ä°]½Éµ•áÁ½ÉĞµÍÑå±”Ù¥•Ü½˜„Í•ÉÙ¥”É•Á½ÉĞ¸4(4(€€€Q¡¥Ì¥ÌÑ¡”•áÑ•É¹…°µ™…¥¹œ€‹š~—r,ˆÍÕÉ™…”èİ¥Ñ Ñ¡”Í•ÉÙ¥•}É•Á½ÉÑÌÙ¥•Ü4(€€€…Ñ¥½¸É…¹Ñ•€£šv¦fCº‡B€øƒ–Ş—’ösš^—š*”€øƒš~—r,¤°•áÑ•É¹…°µ…¹…•ÉÌ½•µÁ±½å••Ì…¸4(€€€É•…Ñ¡”É•Á½ÉĞ¥¸Ñ¡”Í…µ”±…å½ÕĞ…ÌÑ¡”•áÁ½ÉÑ•]½É‘½Õµ•¹Ğ°‰ÕĞÑ¡•ä4(€€€¹•Ù•ÈÍ•”Ñ¡”¥¹Ñ•É¹…°•‘¥Ğ™½É´¸%¹Ñ•É¹…°ÕÍ•ÉÌ…¸ÕÍ”¥ĞÑ½¼¸4(€€€€ˆˆˆ4(€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Í•ÉÙ¥•}É•Á½ÉÑÌˆ°€‰Ù¥•Üˆ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€É•Á½ÉĞ°½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤4(€€€İ½É­•ÉÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¡É•Á½ÉÑ}¥¤4(€€€İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ì€ôl4(€€€€€€€˜‰íİ½É­•Él¹…µ”u÷¾òiíİ½É­•Élİ½É­}‘•ÍÉ¥ÁÑ¥½¸uôˆ4(€€€€€€€™½Èİ½É­•È¥¸İ½É­•ÉÌ4(€€€€€€€¥˜İ½É­•Él‰İ½É­}‘•ÍÉ¥ÁÑ¥½¸‰t4(€€€t4(€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ€ôÉ•Á½ÉÑl‰Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸‰t½È€ˆˆ4(€€€¥˜İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ìè4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ€ô€‰q¸ˆ¹©½¥¸ 4(€€€€€€€€€€€€¡m‘•ÍÉ¥ÁÑ¥½¹}Ñ•áÑt¥˜‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ•±Í”mt¤€¬İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ì4(€€€€€€€€¤4(€€€…ÑÑ…¡µ•¹Ñ}É½ÕÁÌ€ô•Ñ}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ¡É•Á½ÉÑ}¥¤4(€€€Á¡½Ñ½}±…‰•±Ì€ôì4(€€€€€€€€‰…ÉÉ¥Ù…°ˆè€‹–"Ã¢úû:Ã–rëš^Û¦^ÓŸ&ˆ°4(€€€€€€€€‰‘•Á…ÉÑÕÉ”ˆè€‹šï–ò:Ã–rëš^Û¦^ÓŸ&ˆ°4(€€€€€€€€‰Í•±™}¡•¬ˆè€‹¢«šŸ&ˆ°4(€€€€€€€€‰Í¥Ñ”ˆè€‹:Ã–rëšr7–*‡Ÿ&ˆ°4(€€€ô4(€€€Á¡½Ñ½}Í•Ñ¥½¹Ì€ôl4(€€€€€€€ì‰Ñ¥Ñ±”ˆèÑ¥Ñ±”°€‰Á¡½Ñ½Ìˆè…ÑÑ…¡µ•¹Ñ}É½ÕÁÌ¹•Ğ¡…Ñ•½Éä°mt¥ô4(€€€€€€€™½È…Ñ•½Éä°Ñ¥Ñ±”¥¸Á¡½Ñ½}±…‰•±Ì¹¥Ñ•µÌ ¤4(€€€€€€€¥˜…ÑÑ…¡µ•¹Ñ}É½ÕÁÌ¹•Ğ¡…Ñ•½Éä¤4(€€€t4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰Í•ÉÙ¥•}É•Á½ÉÑ}Ù¥•Ü¹¡Ñµ°ˆ°4(€€€€€€€É•Á½ÉĞõÉ•Á½ÉĞ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€İ½É­•ÉÌõİ½É­•ÉÌ°4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞõ‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ°4(€€€€€€€Í…Ù•‘}Á…ÉÑÌõÉ•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•‘}Á…ÉÑÌˆ°É•Á½ÉÑ}¥¤°4(€€€€€€€É•Á±…•‘}Á…ÉÑÌõÉ•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}É•Á±…•‘}Á…ÉÑÌˆ°É•Á½ÉÑ}¥¤°4(€€€€€€€Á¡½Ñ½}Í•Ñ¥½¹ÌõÁ¡½Ñ½}Í•Ñ¥½¹Ì°4(€€€€€€€½µÁ…¹äõ•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤°4(€€€€€€€…¹}•áÁ½ÉĞõ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰Í•ÉÙ¥•}É•Á½ÉÑÌˆ°€‰•áÁ½ÉĞˆ¤°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½Í•ÉÙ¥”µÉ•Á½ÉÑÌ¼ñ¥¹ĞéÉ•Á½ÉÑ}¥ø½•áÁ½ÉĞˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ½ÉÑ}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤è4(€€€É•Á½ÉĞ°½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}É•Á½ÉĞ¡É•Á½ÉÑ}¥¤4(€€€ÑÉäè4(€€€€€€€‘½Õµ•¹Ñ}‰åÑ•Ì€ô‰Õ¥±‘}Í•ÉÙ¥•}É•Á½ÉÑ}‘½à¡É•Á½ÉĞ°½É‘•È¤4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰…¥±•Ñ¼•áÁ½ÉĞÍ•ÉÙ¥”É•Á½ÉĞ€•Ìˆ°É•Á½ÉÑ}¥¤4(€€€€€€€™±…Í  ‹–Ş—’ösš^—š*—–¾ó–ë–’Ç¢Ò—¾ò3¢¾ßšš~—š^—š*—––ºçš"[Ÿ&–B;¦7¢¾Wˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}Í•ÉÙ¥•}É•Á½ÉĞˆ°É•Á½ÉÑ}¥õÉ•Á½ÉÑ}¥¤¤4(€€€™¥±•¹…µ”€ôÍ•ÕÉ•}™¥±•¹…µ”¡˜‰í½É‘•Él±¥•¹Ñ}½É‘•É}¹Õµ‰•ÈuôµíÉ•Á½ÉÑlÉ•Á½ÉÑ}‘…Ñ”uôµÉ•Á½ÉĞ¹‘½àˆ¤½È€‰Í•ÉÙ¥”µÉ•Á½ÉĞ¹‘½àˆ4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€	åÑ•Í%<¡‘½Õµ•¹Ñ}‰åÑ•Ì¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Ù¹¹½Á•¹áµ±™½Éµ…ÑÌµ½™™¥•‘½Õµ•¹Ğ¹İ½É‘ÁÉ½•ÍÍ¥¹µ°¹‘½Õµ•¹Ğˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ™¥±•¹…µ”°4(€€€€¤4(4(4)‘•˜•áÁ•¹Í•}‘•™…Õ±ÑÌ¡•áÁ•¹Í”õ9½¹”¤è4(€€€¥˜•áÁ•¹Í”è4(€€€€€€€É•ÑÕÉ¸‘¥Ğ¡•áÁ•¹Í”¤ğì‰‰•¹•™¥¥…Éå}¥ˆè•áÁ•¹Í•}‰•¹•™¥¥…Éå}¥¡•áÁ•¹Í”¥ô4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰‰•¹•™¥¥…Éå}¥ˆèœ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€‰•áÁ•¹Í•}¹Õµ‰•Èˆè¹•áÑ}•áÁ•¹Í•}¹Õµ‰•È ¤°4(€€€€€€€€‰ÁÉ½©•Ñ}¥ˆè9½¹”°4(€€€€€€€€‰ÁÉ½©•Ğˆè€ˆˆ°4(€€€€€€€€‰•áÁ•¹Í•}‘…Ñ”ˆè‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰…µ½Õ¹Ğˆè€ˆˆ°4(€€€€€€€€‰ÕÉÉ•¹äˆè€‰UMˆ°4(€€€€€€€€‰‘•ÍÉ¥ÁÑ¥½¸ˆè€ˆˆ°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰‘É…™Ğˆ°4(€€€ô4(4(4)‘•˜•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥¤è4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ•áÁ•¹Í•}¥Ñ•µÌ¸¨°ÁÉ½©•ÑÌ¹¥Í}…Ñ¥Ù”4(€€€€€€€™É½´•áÁ•¹Í•}¥Ñ•µÌ4(€€€€€€€±•™Ğ©½¥¸ÁÉ½©•ÑÌ½¸ÁÉ½©•ÑÌ¹¥€ô•áÁ•¹Í•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥4(€€€€€€€İ¡•É”•áÁ•¹Í•}¥Ñ•µÌ¹•áÁ•¹Í•}¥€ô€ü4(€€€€€€€½É‘•È‰ä•áÁ•¹Í•}¥Ñ•µÌ¹Í½ÉÑ}½É‘•È°•áÁ•¹Í•}¥Ñ•µÌ¹¥4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡•áÁ•¹Í•}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)‘•˜•áÁ•¹Í•}¥Ñ•µÍ}™É½µ}™½É´¡•áÁ•¹Í•}¥õ9½¹”¤è4(€€€ÁÉ½©•Ñ}¥‘Ì€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰ÁÉ½©•Ñ}¥ˆ¤4(€€€±¥¹•}­•åÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰¥Ñ•µ}±¥¹•}­•äˆ¤4(€€€…µ½Õ¹ÑÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰¥Ñ•µ}…µ½Õ¹Ğˆ¤4(€€€‘•ÍÉ¥ÁÑ¥½¹Ì€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰¥Ñ•µ}‘•ÍÉ¥ÁÑ¥½¸ˆ¤4(€€€™Õ•±}Ù•¡¥±•}ÑåÁ•Ì€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰™Õ•±}Ù•¡¥±•}ÑåÁ”ˆ¤4(€€€É½İÌ€ômt4(€€€Í••¹}±¥¹•}­•åÌ€ôÍ•Ğ ¤4(€€€™½È¥¹‘•à°ÁÉ½©•Ñ}¥¥¸•¹Õµ•É…Ñ”¡ÁÉ½©•Ñ}¥‘Ì¤è4(€€€€€€€¥˜¹½ĞÁÉ½©•Ñ}¥è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€ÁÉ½©•Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌ4(€€€€€€€€€€€İ¡•É”¥€ô€ü…¹ÁÉ½©•Ñ}ÑåÁ”€ô€•áÁ•¹Í”œ4(€€€€€€€€€€€€€…¹€¡¥Í}…Ñ¥Ù”€ô€Ä½È¥¥¸€ 4(€€€€€€€€€€€€€€€€€Í•±•ĞÁÉ½©•Ñ}¥™É½´•áÁ•¹Í•}¥Ñ•µÌİ¡•É”•áÁ•¹Í•}¥€ô€ü4(€€€€€€€€€€€€€€¤¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡ÁÉ½©•Ñ}¥°•áÁ•¹Í•}¥½È€À¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€¥˜¹½ĞÁÉ½©•Ğè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¦'š.§j–Fc–Ş—š*—¦R¦†çn»’â7–¶c–r£š"[–ŞË–sR£¾ò3¢¾ß¦7šZÃ¦'š.§ˆ¤4(€€€€€€€…µ½Õ¹Ğ€ôÑ½}™±½…Ğ¡…µ½Õ¹ÑÍm¥¹‘•át¥˜¥¹‘•à€ğ±•¸¡…µ½Õ¹ÑÌ¤•±Í”€À¤4(€€€€€€€¥˜…µ½Õ¹Ğ€ğô€Àè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹š¾?’â«–Fc–Ş—š*—¦R¦†çn»j¦G¦Šw–ş¦†ï–’Ÿ’ê8€Ãˆ¤4(€€€€€€€™Õ•±}Ù•¡¥±•}ÑåÁ”€ô€ˆˆ4(€€€€€€€¥˜¥Í}™Õ•±}ÁÉ½©•Ñ}¹…µ”¡ÁÉ½©•Ñl‰¹…µ”‰t¤è4(€€€€€€€€€€€™Õ•±}Ù•¡¥±•}ÑåÁ”€ô€ 4(€€€€€€€€€€€€€€€™Õ•±}Ù•¡¥±•}ÑåÁ•Ím¥¹‘•át¹ÍÑÉ¥À ¤4(€€€€€€€€€€€€€€€¥˜¥¹‘•à€ğ±•¸¡™Õ•±}Ù•¡¥±•}ÑåÁ•Ì¤4(€€€€€€€€€€€€€€€•±Í”€ˆˆ4(€€€€€€€€€€€€¤4(€€€€€€€€€€€¥˜™Õ•±}Ù•¡¥±•}ÑåÁ”¹½Ğ¥¸ì‰Á•ÉÍ½¹…°ˆ°€‰É•¹Ñ…°‰ôè4(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹šÊç¢Òçš*—¦R–ş¦†ï¦'š.§’â«’êë¾ò?¢«šr'¢ö›¢úš"[¢Ö¢ö›¢úˆ¤4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¸€ô‘•ÍÉ¥ÁÑ¥½¹Ím¥¹‘•át¹ÍÑÉ¥À ¤¥˜¥¹‘•à€ğ±•¸¡‘•ÍÉ¥ÁÑ¥½¹Ì¤•±Í”€ˆˆ4(€€€€€€€±¥¹•}­•ä€ô±¥¹•}­•åÍm¥¹‘•át¹ÍÑÉ¥À ¤¥˜¥¹‘•à€ğ±•¸¡±¥¹•}­•åÌ¤•±Í”€ˆˆ4(€€€€€€€¥˜¹½ĞÉ”¹™Õ±±µ…Ñ ¡È‰mµi„µèÀ´å|µuìÄ°àÁôˆ°±¥¹•}­•ä¤½È±¥¹•}­•ä¥¸Í••¹}±¥¹•}­•åÌè4(€€€€€€€€€€€±¥¹•}­•ä€ô˜‰±¥¹”µíÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÄÈ¥ôˆ4(€€€€€€€Í••¹}±¥¹•}­•åÌ¹…‘¡±¥¹•}­•ä¤4(€€€€€€€É½İÌ¹…ÁÁ•¹ 4(€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€‰±¥¹•}­•äˆè±¥¹•}­•ä°4(€€€€€€€€€€€€€€€€‰ÁÉ½©•ĞˆèÁÉ½©•Ğ°4(€€€€€€€€€€€€€€€€‰…µ½Õ¹Ğˆè…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€€€‰‘•ÍÉ¥ÁÑ¥½¸ˆè‘•ÍÉ¥ÁÑ¥½¸°4(€€€€€€€€€€€€€€€€‰™Õ•±}Ù•¡¥±•}ÑåÁ”ˆè™Õ•±}Ù•¡¥±•}ÑåÁ”½È9½¹”°4(€€€€€€€€€€€€€€€€‰Í½ÉÑ}½É‘•Èˆè±•¸¡É½İÌ¤°4(€€€€€€€€€€€ô4(€€€€€€€€¤4(€€€¥˜¹½ĞÉ½İÌè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¢Ï–ÂGšŞï–*ƒ’â’â«–Fc–Ş—š*—¦R¦†çn»ˆ¤4(€€€É•ÑÕÉ¸É½İÌ4(4(4)‘•˜Í…Ù•}•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥°¥Ñ•µ}É½İÌ¤è4(€€€É•Ñ…¥¹•‘}­•åÌ€ôí¥Ñ•µl‰±¥¹•}­•ä‰t™½È¥Ñ•´¥¸¥Ñ•µ}É½İÍô4(€€€•á¥ÍÑ¥¹}…ÑÑ…¡µ•¹ÑÌ€ô•Ñ}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ¡•áÁ•¹Í•}¥¤4(€€€™½È…ÑÑ…¡µ•¹Ğ¥¸•á¥ÍÑ¥¹}…ÑÑ…¡µ•¹ÑÌè4(€€€€€€€…ÑÑ…¡µ•¹Ñ}­•ä€ô€¡…ÑÑ…¡µ•¹Ñl‰•áÁ•¹Í•}¥Ñ•µ}­•ä‰t½È€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€¥˜…ÑÑ…¡µ•¹Ñ}­•ä…¹…ÑÑ…¡µ•¹Ñ}­•ä¹½Ğ¥¸É•Ñ…¥¹•‘}­•åÌè4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€½Ì¹É•µ½Ù”¡•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤¤4(€€€€€€€€€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€€€€€€€€€Á…ÍÌ4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñl‰¥‰t°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•}¥Ñ•µÌİ¡•É”•áÁ•¹Í•}¥€ô€üˆ°€¡•áÁ•¹Í•}¥°¤¤4(€€€™½È¥Ñ•´¥¸¥Ñ•µ}É½İÌè4(€€€€€€€ÁÉ½©•Ğ€ô¥Ñ•µl‰ÁÉ½©•Ğ‰t4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼•áÁ•¹Í•}¥Ñ•µÌ€ 4(€€€€€€€€€€€€€€€•áÁ•¹Í•}¥°±¥¹•}­•ä°ÁÉ½©•Ñ}¥°ÁÉ½©•Ğ°…µ½Õ¹Ğ°‘•ÍÉ¥ÁÑ¥½¸°4(€€€€€€€€€€€€€€€™Õ•±}Ù•¡¥±•}ÑåÁ”°Í½ÉÑ}½É‘•È4(€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€•áÁ•¹Í•}¥°4(€€€€€€€€€€€€€€€¥Ñ•µl‰±¥¹•}­•ä‰t°4(€€€€€€€€€€€€€€€ÁÉ½©•Ñl‰¥‰t°4(€€€€€€€€€€€€€€€ÁÉ½©•Ñl‰¹…µ”‰t°4(€€€€€€€€€€€€€€€¥Ñ•µl‰…µ½Õ¹Ğ‰t°4(€€€€€€€€€€€€€€€¥Ñ•µl‰‘•ÍÉ¥ÁÑ¥½¸‰t°4(€€€€€€€€€€€€€€€¥Ñ•µl‰™Õ•±}Ù•¡¥±•}ÑåÁ”‰t°4(€€€€€€€€€€€€€€€¥Ñ•µl‰Í½ÉÑ}½É‘•È‰t°4(€€€€€€€€€€€€¤°4(€€€€€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½•áÁ•¹Í•Ì½¹•Üˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¹•İ}•áÁ•¹Í”¡½É‘•É}¥¤è4(€€€¥˜¹½Ğ…¹}É•…Ñ•}•áÁ•¹Í” ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•É}ÍÑ…ÉÑ}‘…Ñ”¡½É‘•È¤4(€€€¥˜ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğè4(€€€€€€€É•ÑÕÉ¸ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ4(€€€•áÁ•¹Í•}ÁÉ½©•ÑÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌİ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€•áÁ•¹Í”œ…¹¥Í}…Ñ¥Ù”€ô€Ä½É‘•È‰ä¹…µ”ˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜¹½Ğ•áÁ•¹Í•}ÁÉ½©•ÑÌè4(€€€€€€€™±…Í  ‹¢¾ß–#RÇî?Bš"[º‡B–Fc–"o–îë–Fc–Ş—š*—¦R¦†çn»ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÁÉ½©•ÑÌˆ¤¥˜¥Í}µ…¹…•È ¤•±Í”ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}•áÁ•¹Í•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸¤è4(€€€€€€€€€€€•á¥ÍÑ¥¹œ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•Ğ•áÁ•¹Í•}¥™É½´•áÁ•¹Í•}Í…Ù•}Ñ½­•¹Ìİ¡•É”Ñ½­•¸€ô€üˆ°4(€€€€€€€€€€€€€€€€¡Í…Ù•}Ñ½­•¸°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€€€€€¥˜•á¥ÍÑ¥¹œ…¹•á¥ÍÑ¥¹l‰•áÁ•¹Í•}¥‰tè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•á¥ÍÑ¥¹l‰•áÁ•¹Í•}¥‰t¤¤4(€€€€€€€€€€€™±…Í  ‹¢¾—š*—¦Rš¶–r£’şw–¶c¾ò3¢¾ß¢7–gˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}•áÁ•¹Í”ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ¥½¸ˆ¤€ôô€‰ÍÕ‰µ¥Ğˆ4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‰•¹•™¥¥…Éä€ôÁ½ÍÑ•‘}•áÁ•¹Í•}‰•¹•™¥¥…Éä ¤4(€€€€€€€€€€€¥Ñ•µ}É½İÌ€ô•áÁ•¹Í•}¥Ñ•µÍ}™É½µ}™½É´ ¤4(€€€€€€€€€€€Ñ½Ñ…±}…µ½Õ¹Ğ€ôÍÕ´¡¥Ñ•µl‰…µ½Õ¹Ğ‰t™½È¥Ñ•´¥¸¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€ÁÉ½©•Ñ}¹…µ•Ì€ô€ˆ°€ˆ¹©½¥¸¡‘¥Ğ¹™É½µ­•åÌ¡¥Ñ•µl‰ÁÉ½©•Ğ‰ul‰¹…µ”‰t™½È¥Ñ•´¥¸¥Ñ•µ}É½İÌ¤¤4(€€€€€€€€€€€™¥ÉÍÑ}ÁÉ½©•Ğ€ô¥Ñ•µ}É½İÍlÁul‰ÁÉ½©•Ğ‰t4(€€€€€€€€€€€•áÁ•¹Í•}¹Õµ‰•È€ô¹•áÑ}•áÁ•¹Í•}¹Õµ‰•È ¤4(€€€€€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼•áÁ•¹Í•Ì€ 4(€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥°•áÁ•¹Í•}¹Õµ‰•È°ÁÉ½©•Ñ}¥°ÁÉ½©•Ğ°•áÁ•¹Í•}‘…Ñ”°…µ½Õ¹Ğ°ÕÉÉ•¹ä°4(€€€€€€€€€€€€€€€€€€€‘•ÍÉ¥ÁÑ¥½¸°ÍÑ…ÑÕÌ°É•…Ñ•‘}‰ä°É•…Ñ•‘}…Ğ°ÕÁ‘…Ñ•‘}…Ğ°‰•¹•™¥¥…Éå}¥4(€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€€•áÁ•¹Í•}¹Õµ‰•È°4(€€€€€€€€€€€€€€€€€€€™¥ÉÍÑ}ÁÉ½©•Ñl‰¥‰t°4(€€€€€€€€€€€€€€€€€€€ÁÉ½©•Ñ}¹…µ•Ì°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰•áÁ•¹Í•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€€€€€€€‰UMˆ°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€€‰ÍÕ‰µ¥ÑÑ•ˆ¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü•±Í”€‰‘É…™Ğˆ°4(€€€€€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€€€€‰•¹•™¥¥…Éål‰¥‰t°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€•áÁ•¹Í•}¥€ôÕÉÍ½È¹±…ÍÑÉ½İ¥4(€€€€€€€€€€€™¥¹¥Í¡}•áÁ•¹Í•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°•áÁ•¹Í•}¥¤4(€€€€€€€€€€€Í…Ù•}•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥°¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€Í…Ù•}•áÁ•¹Í•}ÕÁ±½…‘Ì¡•áÁ•¹Í•}¥°¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€ÉÕ¹}•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•­Ì¡•áÁ•¹Í•}¥°ÕÍ•}‘••ÁÍ••¬õÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü¤4(€€€€€€€€€€€Íå¹}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÍ}Ñ½}Í•ÑÑ±•µ•¹Ğ¡½É‘•Él‰¥‰t¤4(€€€€€€€€€€€•áÁ•¹Í•}ÍÕµµ…Éä€ô˜‹š*—¦R–öK–Æ{–Fc–Ş—¾òií‰•¹•™¥¥…Éål¹…µ”u÷¾òo–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾òo¦G¦Šw¾òiíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥ôˆ4(€€€€€€€€€€€±½}…Ñ¥½¸ ‰É•…Ñ”ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•}¹Õµ‰•È°•áÁ•¹Í•}ÍÕµµ…Éä¤4(€€€€€€€€€€€¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Üè4(€€€€€€€€€€€€€€€±½}…Ñ¥½¸ ‰ÍÕ‰µ¥Ğˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•}¹Õµ‰•È°•áÁ•¹Í•}ÍÕµµ…Éä¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}•áÁ•¹Í”ˆ°½É‘•É}¥õ½É‘•É}¥¤¤4(€€€€€€€¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Üè4(€€€€€€€€€€€¥˜‰•¹•™¥¥…Éål‰¥‰t€„ôœ¹ÕÍ•Él‰¥‰tè4(€€€€€€€€€€€€€€€É•…Ñ•}µ•ÍÍ…” 4(€€€€€€€€€€€€€€€€€€€‰•¹•™¥¥…Éål‰¥‰t°€‹š*—¦R–ŞËš>C’ê“–º‡š‚àˆ°4(€€€€€€€€€€€€€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷’âë’öƒš>C’ê“’êš*—¦R í•áÁ•¹Í•}¹Õµ‰•É÷¾ò3¦G¦Štíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥÷ˆ°4(€€€€€€€€€€€€€€€€€€€ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€¹½Ñ¥™å}É½±” 4(€€€€€€€€€€€€€€€l‰…‘µ¥¸ˆ°€‰µ…¹…•È‰t°4(€€€€€€€€€€€€€€€€‹šZÃš*—¦R–ú–º‡š‚àˆ°4(€€€€€€€€€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷š>C’ê“’ê–öK–Æxí‰•¹•™¥¥…Éål¹…µ”uôƒjš*—¦R í•áÁ•¹Í•}¹Õµ‰•É÷¾ò3–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾ò3¦G¦Štíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥÷ˆ°4(€€€€€€€€€€€€€€€ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€™±…Í  ‹š*—¦R–ŞËš>C’ê“î?B–º‡š‚ãˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€€€€€™±…Í  ‹š*—¦R–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•áÁ•¹Í•}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€•áÁ•¹Í”õ•áÁ•¹Í•}‘•™…Õ±ÑÌ ¤°4(€€€€€€€‰•¹•™¥¥…É¥•Ìõ•áÁ•¹Í•}‰•¹•™¥¥…Éå}½ÁÑ¥½¹Ì ¤°4(€€€€€€€™½Éµ}¥Ñ•µÌõmt°4(€€€€€€€•áÁ•¹Í•}ÁÉ½©•ÑÌõ•áÁ•¹Í•}ÁÉ½©•ÑÌ°4(€€€€€€€¥Í}•‘¥Ğõ…±Í”°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõmt°4(€€€€€€€½µµ½¹}…ÑÑ…¡µ•¹ÑÌõmt°4(€€€€€€€…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´õíô°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½•áÁ•¹Í•Ì¼ñ¥¹Ğé•áÁ•¹Í•}¥ø½•‘¥Ğˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•‘¥Ñ}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤è4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤4(€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€™±…Í  ‹–>«šr'’şw–¶cšr«š>C’ê“š"[¢Š¯¦–n{jš*—¦R–>¿’î—ò[¢úGˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€¥˜•áÁ•¹Í•l‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰t…¹¹½Ğ¥Í}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€•áÁ•¹Í•}ÁÉ½©•ÑÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌ4(€€€€€€€İ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€•áÁ•¹Í”œ…¹€ 4(€€€€€€€€€€€¥Í}…Ñ¥Ù”€ô€Ä½È¥¥¸€ 4(€€€€€€€€€€€€€€€Í•±•ĞÁÉ½©•Ñ}¥™É½´•áÁ•¹Í•}¥Ñ•µÌİ¡•É”•áÁ•¹Í•}¥€ô€ü4(€€€€€€€€€€€€¤4(€€€€€€€€¤4(€€€€€€€½É‘•È‰ä¥Í}…Ñ¥Ù”‘•ÍŒ°¹…µ”4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡•áÁ•¹Í•}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}•áÁ•¹Í•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°•áÁ•¹Í•}¥¤è4(€€€€€€€€€€€™±…Í  ‹¢¾—š*—¦R–ŞËî?’şw–¶c¾ò3¢¾ß–.ÿ¦7–’7š>C’ê“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€€€€€ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰…Ñ¥½¸ˆ¤€ôô€‰ÍÕ‰µ¥Ğˆ4(€€€€€€€ÑÉäè4(€€€€€€€€€€€‰•¹•™¥¥…Éä€ôÁ½ÍÑ•‘}•áÁ•¹Í•}‰•¹•™¥¥…Éä¡•áÁ•¹Í”¤4(€€€€€€€€€€€¥Ñ•µ}É½İÌ€ô•áÁ•¹Í•}¥Ñ•µÍ}™É½µ}™½É´¡•áÁ•¹Í•}¥¤4(€€€€€€€€€€€Ñ½Ñ…±}…µ½Õ¹Ğ€ôÍÕ´¡¥Ñ•µl‰…µ½Õ¹Ğ‰t™½È¥Ñ•´¥¸¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€ÁÉ½©•Ñ}¹…µ•Ì€ô€ˆ°€ˆ¹©½¥¸¡‘¥Ğ¹™É½µ­•åÌ¡¥Ñ•µl‰ÁÉ½©•Ğ‰ul‰¹…µ”‰t™½È¥Ñ•´¥¸¥Ñ•µ}É½İÌ¤¤4(€€€€€€€€€€€™¥ÉÍÑ}ÁÉ½©•Ğ€ô¥Ñ•µ}É½İÍlÁul‰ÁÉ½©•Ğ‰t4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•Ì4(€€€€€€€€€€€€€€€Í•ĞÁÉ½©•Ñ}¥€ô€ü°ÁÉ½©•Ğ€ô€ü°•áÁ•¹Í•}‘…Ñ”€ô€ü°…µ½Õ¹Ğ€ô€ü°ÕÉÉ•¹ä€ô€ü°‘•ÍÉ¥ÁÑ¥½¸€ô€ü°4(€€€€€€€€€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€ü°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°ÕÁ‘…Ñ•‘}…Ğ€ô€ü°‰•¹•™¥¥…Éå}¥€ô€ü4(€€€€€€€€€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€™¥ÉÍÑ}ÁÉ½©•Ñl‰¥‰t°4(€€€€€€€€€€€€€€€€€€€ÁÉ½©•Ñ}¹…µ•Ì°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰•áÁ•¹Í•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}…µ½Õ¹Ğ°4(€€€€€€€€€€€€€€€€€€€€‰UMˆ°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€€‰ÍÕ‰µ¥ÑÑ•ˆ¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü•±Í”€‰‘É…™Ğˆ°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€€€€‰•¹•™¥¥…Éål‰¥‰t°4(€€€€€€€€€€€€€€€€€€€•áÁ•¹Í•}¥°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€Í…Ù•}•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥°¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€Í…Ù•}•áÁ•¹Í•}ÕÁ±½…‘Ì¡•áÁ•¹Í•}¥°¥Ñ•µ}É½İÌ¤4(€€€€€€€€€€€ÉÕ¹}•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•­Ì¡•áÁ•¹Í•}¥°ÕÍ•}‘••ÁÍ••¬õÍÕ‰µ¥Ñ}™½É}É•Ù¥•Ü¤4(€€€€€€€€€€€Íå¹}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÍ}Ñ½}Í•ÑÑ±•µ•¹Ğ¡½É‘•Él‰¥‰t¤4(€€€€€€€€€€€•áÁ•¹Í•}ÍÕµµ…Éä€ô˜‹š*—¦R–öK–Æ{–Fc–Ş—¾òií‰•¹•™¥¥…Éål¹…µ”u÷¾òo–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾òo¦G¦Šw¾òiíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥ôˆ4(€€€€€€€€€€€±½}…Ñ¥½¸ ‰ÕÁ‘…Ñ”ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°•áÁ•¹Í•}ÍÕµµ…Éä¤4(€€€€€€€€€€€¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Üè4(€€€€€€€€€€€€€€€±½}…Ñ¥½¸ ‰ÍÕ‰µ¥Ğˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°•áÁ•¹Í•}ÍÕµµ…Éä¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€€€€€¥˜ÍÕ‰µ¥Ñ}™½É}É•Ù¥•Üè4(€€€€€€€€€€€¥˜‰•¹•™¥¥…Éål‰¥‰t€„ôœ¹ÕÍ•Él‰¥‰tè4(€€€€€€€€€€€€€€€É•…Ñ•}µ•ÍÍ…” 4(€€€€€€€€€€€€€€€€€€€‰•¹•™¥¥…Éål‰¥‰t°€‹š*—¦R–ŞËš>C’ê“–º‡š‚àˆ°4(€€€€€€€€€€€€€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷’âë’öƒš>C’ê“’êš*—¦R í•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èu÷¾ò3¦G¦Štíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥÷ˆ°4(€€€€€€€€€€€€€€€€€€€ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€¹½Ñ¥™å}É½±” 4(€€€€€€€€€€€€€€€l‰…‘µ¥¸ˆ°€‰µ…¹…•È‰t°4(€€€€€€€€€€€€€€€€‹š*—¦R–ŞËš>C’ê“–º‡š‚àˆ°4(€€€€€€€€€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷š>C’ê“’ê–öK–Æxí‰•¹•™¥¥…Éål¹…µ”uôƒjš*—¦R í•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èu÷¾ò3–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾ò3¦G¦Štíµ½¹•ä¡Ñ½Ñ…±}…µ½Õ¹Ğ¥÷ˆ°4(€€€€€€€€€€€€€€€ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€™±…Í  ‹š*—¦R–ŞËš>C’ê“î?B–º‡š‚ãˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€€€€€™±…Í  ‹š*—¦R–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€…ÑÑ…¡µ•¹ÑÌ€ô•Ñ}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ¡•áÁ•¹Í•}¥¤4(€€€½µµ½¹}…ÑÑ…¡µ•¹ÑÌ°…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´€ôÉ½ÕÁ}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ¡…ÑÑ…¡µ•¹ÑÌ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•áÁ•¹Í•}™½É´¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€•áÁ•¹Í”õ•áÁ•¹Í•}‘•™…Õ±ÑÌ¡•áÁ•¹Í”¤°4(€€€€€€€‰•¹•™¥¥…É¥•Ìõ•áÁ•¹Í•}‰•¹•™¥¥…Éå}½ÁÑ¥½¹Ì¡•áÁ•¹Í”¤°4(€€€€€€€™½Éµ}¥Ñ•µÌõ•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥¤°4(€€€€€€€•áÁ•¹Í•}ÁÉ½©•ÑÌõ•áÁ•¹Í•}ÁÉ½©•ÑÌ°4(€€€€€€€¥Í}•‘¥ĞõQÉÕ”°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ…ÑÑ…¡µ•¹ÑÌ°4(€€€€€€€½µµ½¹}…ÑÑ…¡µ•¹ÑÌõ½µµ½¹}…ÑÑ…¡µ•¹ÑÌ°4(€€€€€€€…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´õ…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´°4(€€€€€€€ÑÉ…¹Í™•É}É•¥µ‰ÕÉÍ•µ•¹Ğõ±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•Él‰¥‰t¤°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½•áÁ•¹Í•Ì¼ñ¥¹Ğé•áÁ•¹Í•}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ•¹Í•}‘•Ñ…¥°¡•áÁ•¹Í•}¥¤è4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤4(€€€…¹}ÑÉ…¹Í™•É}…ÑÑ…¡µ•¹ÑÌ€ô¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌˆ°€‰•‘¥Ğˆ¤4(€€€É•…Ñ½È€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ¹…µ”°•µ…¥°™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°€¡•áÁ•¹Í•l‰É•…Ñ•‘}‰ä‰t°¤¤¹™•Ñ¡½¹” ¤4(€€€É•Ù¥•İ•È€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ¹…µ”°•µ…¥°™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°€¡•áÁ•¹Í•l‰É•Ù¥•İ•‘}‰ä‰t°¤¤¹™•Ñ¡½¹” ¤¥˜•áÁ•¹Í•l‰É•Ù¥•İ•‘}‰ä‰t•±Í”9½¹”4(€€€É•¥µ‰ÕÉÍ•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ¹…µ”°•µ…¥°™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡•áÁ•¹Í•l‰É•¥µ‰ÕÉÍ•‘}‰ä‰t°¤°4(€€€€¤¹™•Ñ¡½¹” ¤¥˜•áÁ•¹Í•l‰É•¥µ‰ÕÉÍ•‘}‰ä‰t•±Í”9½¹”4(€€€…ÑÑ…¡µ•¹ÑÌ€ô•Ñ}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ¡•áÁ•¹Í•}¥¤4(€€€½µµ½¹}…ÑÑ…¡µ•¹ÑÌ°…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´€ôÉ½ÕÁ}•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌ¡…ÑÑ…¡µ•¹ÑÌ¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•áÁ•¹Í•}‘•Ñ…¥°¹¡Ñµ°ˆ°4(€€€€€€€½É‘•Èõ½É‘•È°4(€€€€€€€•áÁ•¹Í”õ•áÁ•¹Í”°4(€€€€€€€¥Ñ•µÌõ•áÁ•¹Í•}¥Ñ•µÌ¡•áÁ•¹Í•}¥¤°4(€€€€€€€É•…Ñ½ÈõÉ•…Ñ½È°4(€€€€€€€‰•¹•™¥¥…Éäõ‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ¹…µ”™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°€¡•áÁ•¹Í•}‰•¹•™¥¥…Éå}¥¡•áÁ•¹Í”¤°¤¤¹™•Ñ¡½¹” ¤°4(€€€€€€€É•Ù¥•İ•ÈõÉ•Ù¥•İ•È°4(€€€€€€€É•¥µ‰ÕÉÍ•ÈõÉ•¥µ‰ÕÉÍ•È°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ…ÑÑ…¡µ•¹ÑÌ°4(€€€€€€€½µµ½¹}…ÑÑ…¡µ•¹ÑÌõ½µµ½¹}…ÑÑ…¡µ•¹ÑÌ°4(€€€€€€€…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´õ…ÑÑ…¡µ•¹ÑÍ}‰å}¥Ñ•´°4(€€€€€€€ÑÉ…¹Í™•É}É•¥µ‰ÕÉÍ•µ•¹Ğõ±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•Él‰¥‰t¤¥˜…¹}ÑÉ…¹Í™•É}…ÑÑ…¡µ•¹ÑÌ•±Í”9½¹”°4(€€€€€€€…¹}ÑÉ…¹Í™•É}…ÑÑ…¡µ•¹ÑÌõ…¹}ÑÉ…¹Í™•É}…ÑÑ…¡µ•¹ÑÌ°4(€€€€€€€…¹}‘•±•Ñ”õ…¹}‘•±•Ñ•}•áÁ•¹Í”¡•áÁ•¹Í”¤°4(€€€€€€€‘ÕÁ±¥…Ñ•}¡•­Ìõ•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•­Ì¡•áÁ•¹Í•}¥¤°4(€€€€€€€±…‰•±ÌõaA9M}MQQUM}1	1L°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í”µ‘ÕÁ±¥…Ñ”µ¡•­Ì¼ñ¥¹Ğé¡•­}¥ø½É•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•Ù¥•İ}•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•¬¡¡•­}¥¤è4(€€€¥˜¹½Éµ…±¥é•‘}É½±” ¤¹½Ğ¥¸ì‰…‘µ¥¸ˆ°€‰µ…¹…•È‰ôè4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¡•¬€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•­Ìİ¡•É”¥€ô€üˆ°€¡¡•­}¥°¤4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ¡•¬è4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•Ù¥•İ}ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€±…‰•±Ì€ôì4(€€€€€€€€‰½¹™¥Éµ•ˆè€‹–ŞË†»¢º“¦7–’4ˆ°4(€€€€€€€€‰¹½Ñ}‘ÕÁ±¥…Ñ”ˆè€‹–ŞËš‚¢ºÃ’âë¦v{¦7–’4ˆ°4(€€€€€€€€‰±•…±}ÍÁ±¥Ğˆè€‹–ŞËš‚¢ºÃ’âë–B#šÎW–"šF(ˆ°4(€€€ô4(€€€¥˜ÍÑ…ÑÕÌ¹½Ğ¥¸±…‰•±Ìè4(€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•­Ì4(€€€€€€€Í•ĞÉ•Ù¥•İ}ÍÑ…ÑÕÌ€ô€ü°É•Ù¥•İ•‘}‰ä€ô€ü°É•Ù¥•İ•‘}…Ğ€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡ÍÑ…ÑÕÌ°œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°¹½Ü ¤°¡•­}¥¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰É•Ù¥•Üˆ°€‰•áÁ•¹Í•}‘ÕÁ±¥…Ñ•}¡•¬ˆ°¡•­}¥°±…‰•±ÍmÍÑ…ÑÕÍt°˜‹š*—¦R%¾òií¡•­l•áÁ•¹Í•}¥uôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í ¡±…‰•±ÍmÍÑ…ÑÕÍt°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ¡•­l‰•áÁ•¹Í•}¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í•Ì¼ñ¥¹Ğé•áÁ•¹Í•}¥ø½…ÁÁÉ½Ù”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…ÁÁÉ½Ù•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤è4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤4(€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t€„ô€‰ÍÕ‰µ¥ÑÑ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–úî?B–º‡š‚ãjš*—¦R–>¿’î—¦k¢şˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”•áÁ•¹Í•Ì4(€€€€€€€Í•ĞÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°°É•Ù¥•İ•‘}‰ä€ô€ü°É•Ù¥•İ•‘}…Ğ€ô€ü°4(€€€€€€€€€€€Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á•¹‘¥¹œœ°É•¥µ‰ÕÉÍ•‘}‰ä€ô¹Õ±°°É•¥µ‰ÕÉÍ•‘}…Ğ€ô¹Õ±°°ÕÁ‘…Ñ•‘}…Ğ€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°¹½Ü ¤°•áÁ•¹Í•}¥¤°4(€€€€¤4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•Él‰¥‰t¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ğ…¹É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€ÕÁ‘…Ñ•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}Ñ½Ñ…±Ì¡É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤4(€€€µ•ÍÍ…•}±¥¹¬€ôÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤4(€€€µ•ÍÍ…•}‰½‘ä€ô€ 4(€€€€€€€˜‰íœ¹ÕÍ•Él¹…µ”u÷–ŞË–º‡š‚ã¦k¢şš*—¦R í•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èu÷¾ò0ˆ4(€€€€€€€˜‹–Ş—–6Tí½É‘•Él½É‘•É}¹Õµ‰•Èu÷¾ò3¦G¦Štíµ½¹•ä¡•áÁ•¹Í•l…µ½Õ¹Ğt°•áÁ•¹Í•lÕÉÉ•¹ät¥÷ˆ4(€€€€¤4(€€€¹½Ñ¥™å}•áÁ•¹Í•}Á…ÉÑ¥¥Á…¹ÑÌ¡•áÁ•¹Í”°€‹š*—¦R–ŞË–º‡š‚ã¦k¢şˆ°µ•ÍÍ…•}‰½‘ä°µ•ÍÍ…•}±¥¹¬¤4(€€€¹½Ñ¥™å}É½±” 4(€€€€€€€l‰…‘µ¥¸‰t°4(€€€€€€€€‹š*—¦R–ŞË–º‡š‚ã¦k¢şˆ°4(€€€€€€€µ•ÍÍ…•}‰½‘ä°4(€€€€€€€µ•ÍÍ…•}±¥¹¬°4(€€€€€€€•á±Õ‘•}ÕÍ•É}¥‘Ìõí•áÁ•¹Í•l‰É•…Ñ•‘}‰ä‰t°•áÁ•¹Í•}‰•¹•™¥¥…Éå}¥¡•áÁ•¹Í”¤°œ¹ÕÍ•Él‰¥‰uô°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰…ÁÁÉ½Ù”ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°˜‹–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èuôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹š*—¦R–ŞË–º‡š‚ã¦k¢şˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í•Ì¼ñ¥¹Ğé•áÁ•¹Í•}¥ø½É•ÑÕÉ¸ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜É•ÑÕÉ¹}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤è4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤4(€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t€„ô€‰ÍÕ‰µ¥ÑÑ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–úî?B–º‡š‚ãjš*—¦R–>¿’î—¦–n{ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€É•…Í½¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•ÑÕÉ¹}É•…Í½¸ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½ĞÉ•…Í½¸è4(€€€€€€€™±…Í  ‹¢¾ß–†¯–g¦–n{–:–nƒˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”•áÁ•¹Í•ÌÍ•ĞÍÑ…ÑÕÌ€ô€É•ÑÕÉ¹•œ°É•ÑÕÉ¹}É•…Í½¸€ô€ü°É•Ù¥•İ•‘}‰ä€ô€ü°É•Ù¥•İ•‘}…Ğ€ô€ü°ÕÁ‘…Ñ•‘}…Ğ€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡É•…Í½¸°œ¹ÕÍ•Él‰¥‰t°¹½Ü ¤°¹½Ü ¤°•áÁ•¹Í•}¥¤°4(€€€€¤4(€€€¹½Ñ¥™å}•áÁ•¹Í•}Á…ÉÑ¥¥Á…¹ÑÌ¡•áÁ•¹Í”°€‹š*—¦R–ŞË¢Š¯¦–nxˆ°˜‹š*—¦R í•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èuôƒ–ŞË¢Š¯¦–n{–:–nƒ¾òiíÉ•…Í½¹ôˆ°ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€±½}…Ñ¥½¸ ‰É•ÑÕÉ¸ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°˜‹–:–nƒ¾òiíÉ•…Í½¹ôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹š*—¦R–ŞË¦–n{ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í•Ì¼ñ¥¹Ğé•áÁ•¹Í•}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤è4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡•áÁ•¹Í•}¥¤4(€€€¥˜¹½Ğ…¹}‘•±•Ñ•}•áÁ•¹Í”¡•áÁ•¹Í”¤è4(€€€€€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€€€€€™±…Í  ‹–>«šr'’şw–¶cšr«š>C’ê“š"[¢Š¯¦–n{jš*—¦R–>¿’î—–"ƒ¦f“ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•áÁ•¹Í•}‘•Ñ…¥°ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•}¥¤¤4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í¡ÕÑ¥°¹ÉµÑÉ•”¡•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}‘¥È¡•áÁ•¹Í•}¥¤°¥¹½É•}•ÉÉ½ÉÌõQÉÕ”¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”•áÁ•¹Í•}¥€ô€üˆ°€¡•áÁ•¹Í•}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•}¥Ñ•µÌİ¡•É”•áÁ•¹Í•}¥€ô€üˆ°€¡•áÁ•¹Í•}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•Ìİ¡•É”¥€ô€üˆ°€¡•áÁ•¹Í•}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰‘•±•Ñ”ˆ°€‰•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥°•áÁ•¹Í•l‰•áÁ•¹Í•}¹Õµ‰•È‰t°˜‹–Ş—–6W¾òií½É‘•Él½É‘•É}¹Õµ‰•Èuôˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹š*—¦R–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•É}‘•Ñ…¥°ˆ°½É‘•É}¥õ½É‘•Él‰¥‰t¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½•áÁ•¹Í”µ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}•áÁ•¹Í”¡…ÑÑ…¡µ•¹Ñl‰•áÁ•¹Í•}¥‰t¤4(€€€É•ÑÕÉ¸Í…™•}…ÑÑ…¡µ•¹Ñ}É•ÍÁ½¹Í” 4(€€€€€€€•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½•áÁ•¹Í”µ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘½İ¹±½…ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}•áÁ•¹Í”¡…ÑÑ…¡µ•¹Ñl‰•áÁ•¹Í•}¥‰t¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”õ…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í”µ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡…ÑÑ…¡µ•¹Ñl‰•áÁ•¹Í•}¥‰t¤4(€€€¥˜•áÁ•¹Í•l‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰t…¹¹½Ğ¥Í}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜•áÁ•¹Í•l‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€ÑÉäè4(€€€€€€€½Ì¹É•µ½Ù”¡•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤¤4(€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€Á…ÍÌ4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹¦f’îÛ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}•áÁ•¹Í”ˆ°•áÁ•¹Í•}¥õ•áÁ•¹Í•l‰¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½•áÁ•¹Í”µ…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½ÑÉ…¹Í™•Èˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÑÉ…¹Í™•É}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´•áÁ•¹Í•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€•áÁ•¹Í”°½É‘•È€ôÉ•ÅÕ¥É•}•áÁ•¹Í”¡…ÑÑ…¡µ•¹Ñl‰•áÁ•¹Í•}¥‰t¤4(€€€¥˜¹½Ğ…¹}ÑÉ…¹Í™•É}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğ¡•áÁ•¹Í”¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€É•ÑÕÉ¹}ÕÉ°€ôÕÉ±}™½È 4(€€€€€€€€‰•áÁ•¹Í•}‘•Ñ…¥°ˆ¥˜É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰É•ÑÕÉ¹}Ñ¼ˆ¤€ôô€‰‘•Ñ…¥°ˆ•±Í”€‰•‘¥Ñ}•áÁ•¹Í”ˆ°4(€€€€€€€•áÁ•¹Í•}¥õ•áÁ•¹Í•l‰¥‰t°4(€€€€¤4(4(€€€‘•˜™¥¹¥Í¡}ÑÉ…¹Í™•È¡µ•ÍÍ…”°…Ñ•½Éäô‰ÍÕ•ÍÌˆ¤è4(€€€€€€€™±…Í ¡µ•ÍÍ…”°…Ñ•½Éä¤4(€€€€€€€¥˜É•ÅÕ•ÍĞ¹¡•…‘•ÉÌ¹•Ğ ‰`µ!¥ÍÑ½ÉäµI•Á±…”ˆ¤€ôô€ˆÄˆè4(€€€€€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡½¬õ…Ñ•½Éä€ôô€‰ÍÕ•ÍÌˆ°É•‘¥É•ĞõÉ•ÑÕÉ¹}ÕÉ°¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡É•ÑÕÉ¹}ÕÉ°¤4(4(€€€•á¥ÍÑ¥¹œ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ¥™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”Í½ÕÉ•}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}¥€ô€üˆ°4(€€€€€€€€¡…ÑÑ…¡µ•¹Ñ}¥°¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€¥˜•á¥ÍÑ¥¹œè4(€€€€€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È ‹¢¾—¦f’îÛ–ŞËî?’òƒ¦K–"Ã–Ş—–6WîOº_ˆ¤4(4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡½É‘•Él‰¥‰t¤4(€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ğè4(€€€€€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È ‹¢¾—–Ş—–6W–Âkšr«Rš"C–Ş—–6WîOº_¾ò3šjš^Ûš^ƒšÎW’òƒ¦K¦f’îÛˆ°€‰•ÉÉ½Èˆ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t¹½Ğ¥¸ì‰‘É…™Ğˆ°€‰É•ÑÕÉ¹•‰ôè4(€€€€€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È ‹–Ş—–6WîOº_–ŞËî?š>C’ê“š"[–º‡š‚ã–º3š"C¾ò3’â7¢÷–7’òƒ¦K¦f’îÛˆ°€‰•ÉÉ½Èˆ¤4(4(€€€ÑÉäè4(€€€€€€€½Á¥•€ô½Áå}™¥±•}Ñ½}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ğ 4(€€€€€€€€€€€É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t°4(€€€€€€€€€€€•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°4(€€€€€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€…ÑÑ…¡µ•¹Ñl‰½¹Ñ•¹Ñ}ÑåÁ”‰t°4(€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€Í½ÕÉ•}•áÁ•¹Í•}…ÑÑ…¡µ•¹Ñ}¥õ…ÑÑ…¡µ•¹Ñ}¥°4(€€€€€€€€¤4(€€€€€€€¥˜¹½Ğ½Á¥•è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¦f’îÛšZ’îÛ’â7–¶c–r£¾ò3š^ƒšÎW’òƒ¦Kˆ¤4(€€€€€€€±½}…Ñ¥½¸ 4(€€€€€€€€€€€€‰ÑÉ…¹Í™•Èˆ°4(€€€€€€€€€€€€‰•áÁ•¹Í•}…ÑÑ…¡µ•¹Ğˆ°4(€€€€€€€€€€€…ÑÑ…¡µ•¹Ñ}¥°4(€€€€€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€˜‹š*—¦R¾òií•áÁ•¹Í•l•áÁ•¹Í•}¹Õµ‰•Èu÷¾òo–Ş—–6WîOº_¾òiíÉ•¥µ‰ÕÉÍ•µ•¹Ñl™¥±•}¹…µ”uôˆ°4(€€€€€€€€¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€•á•ÁĞÍÅ±¥Ñ”Ì¹%¹Ñ•É¥ÑåÉÉ½Èè4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È ‹¢¾—¦f’îÛ–ŞËî?’òƒ¦K–"Ã–Ş—–6WîOº_ˆ¤4(€€€•á•ÁĞ€¡=MÉÉ½È°ÍÅ±¥Ñ”Ì¹ÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•ÉÉ½Èè4(€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰…¥±•Ñ¼ÑÉ…¹Í™•È•áÁ•¹Í”…ÑÑ…¡µ•¹Ğ€•Ìˆ°…ÑÑ…¡µ•¹Ñ}¥¤4(€€€€€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È¡ÍÑÈ¡•ÉÉ½È¤½È€‹¦f’îÛ’òƒ¦K–’Ç¢Ò—¾ò3¢¾ß¢7–B;¦7¢¾Wˆ°€‰•ÉÉ½Èˆ¤4(4(€€€É•ÑÕÉ¸™¥¹¥Í¡}ÑÉ…¹Í™•È ‹¦f’îÛ–ŞË–’7–"Û–"Ã–Ş—–6WîOº_ˆ¤4(4(4)…ÁÀ¹•Ğ ˆ½Í•ÉÙ¥”µ½É‘•ÉÌ¼ñ¥¹Ğé½É‘•É}¥ø½…ÑÑ…¡µ•¹ÑÌ¹é¥Àˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}Í•ÉÙ¥•}½É‘•É}…ÑÑ…¡µ•¹ÑÌ¡½É‘•É}¥¤è4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€…Ñ•½É¥•Ì€ôì‰Í•±™}¡•¬ˆè€‹¢«šŸ&ˆ°€‰…ÉÉ¥Ù…°ˆè€‹¢şo–rëŸ&ˆ°€‰‘•Á…ÉÑÕÉ”ˆè€‹šï–rëŸ&ˆ°4(€€€€€€€€€€€€€€€€€€‰Í¥Ñ”ˆè€‹:Ã–rë–Ş—’ösŸ&ˆ°€‰µ¥±•…•}ÁÉ½½˜ˆè€‹¦3¢/’öC¢¾‰ô4(€€€™½±‘•ÉÌ€ôl©…Ñ•½É¥•Ì¹Ù…±Õ•Ì ¤°€‹–Ş—–6WîOº\‰t4(€€€™¥±•Ì€ômt4(€€€‘•˜½±±•Ğ¡™½±‘•È°Á…Ñ °¹…µ”°É½½Ğ¤è4(€€€€€€€Á…Ñ €ôA…Ñ ¡Á…Ñ ¤¹É•Í½±Ù” ¤4(€€€€€€€¥˜¹½ĞÁ…Ñ ¹¥Í}É•±…Ñ¥Ù•}Ñ¼¡A…Ñ ¡É½½Ğ¤¹É•Í½±Ù” ¤¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ°‘•ÍÉ¥ÁÑ¥½¸ô‹¦f’îÛ¢Ş¿–úš^ƒšV#ˆ¤4(€€€€€€€¹…µ”€ôÉ”¹ÍÕˆ¡Èmqp¼è¨üˆğùñqàÀÀµqàÅ™tœ°€|œ°ÍÑÈ¡¹…µ”¤¤¹ÍÑÉ¥À œ¸€œ¤½È€Ÿ¦f’îØœ4(€€€€€€€™¥±•Ì¹…ÁÁ•¹ ¡™½±‘•È°Á…Ñ °¹…µ”¤¤4(€€€¥˜¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ Í•ÉÙ¥•}É•Á½ÉÑÌœ°€Ù¥•Üœ¤è4(€€€€€€€™½ÈÉ½Ü¥¸‘ˆ ¤¹•á•ÕÑ” ˆˆ‰Í•±•Ğ„¸¨°È¹É•Á½ÉÑ}‘…Ñ”™É½´Í•ÉÙ¥•}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ„4(€€€€€€€€€€€€€€€©½¥¸Í•ÉÙ¥•}É•Á½ÉÑÌÈ½¸È¹¥õ„¹É•Á½ÉÑ}¥İ¡•É”È¹Í•ÉÙ¥•}½É‘•É}¥ôü½É‘•È‰äÈ¹É•Á½ÉÑ}‘…Ñ”±„¹¥ˆˆˆ°€¡½É‘•É}¥°¤¤è4(€€€€€€€€€€€¥˜É½İl…Ñ•½Éät¥¸…Ñ•½É¥•Ìè4(€€€€€€€€€€€€€€€½±±•Ğ¡…Ñ•½É¥•ÍmÉ½İl…Ñ•½Éäut°É•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡É½Ü¤°4(€€€€€€€€€€€€€€€€€€€€€€€˜‰íÉ½İlÉ•Á½ÉÑ}‘…Ñ”uô·š^—š*•íÉ½İlÉ•Á½ÉÑ}¥uôµíÉ½İl½É¥¥¹…±}™¥±•¹…µ”uôˆ°IA=IQ}QQ!59QM}%H¤4(€€€¥˜¡…Í}µ•¹Õ}Á•Éµ¥ÍÍ¥½¸ ™¥•±‘}İ½É¬œ¤è4(€€€€€€€É½½Ğ€ôÍ¡…É•‘}Á¡½Ñ½Í}É½½Ğ ¤4(€€€€€€€€Œ%¹±Õ‘”Í•ÉÙ•Èµ¥µÁ½ÉÑ•Á¡½Ñ½Ì…Ìİ•±°…ÌÁ¡½¹”ÕÁ±½…‘Ì°İ¥Ñ¡½ÕĞÑ¡Õµ‰¹…¥±Ì¸4(€€€€€€€Á¥ÑÕÉ•}‘¥È€ô€¡É½½Ğ€¼ÍÑÈ¡½É‘•Él½É‘•É}¹Õµ‰•Èt¤€¼€Á¥ÑÕÉ•Ìœ¤¹É•Í½±Ù” ¤4(€€€€€€€¥˜¹½ĞÁ¥ÑÕÉ•}‘¥È¹¥Í}É•±…Ñ¥Ù•}Ñ¼¡É½½Ğ¹É•Í½±Ù” ¤¤è4(€€€€€€€€€€€…‰½ÉĞ ĞÀÀ¤4(€€€€€€€¥˜Á¥ÑÕÉ•}‘¥È¹¥Í}‘¥È ¤è4(€€€€€€€€€€€™½ÈÁ…Ñ ¥¸Í½ÉÑ•¡Á¥ÑÕÉ•}‘¥È¹É±½ˆ œ¨œ¤¤è4(€€€€€€€€€€€€€€€¥˜Á…Ñ ¹¥Í}™¥±” ¤…¹Á…Ñ ¹ÍÕ™™¥à¹±½İ•È ¤¹±ÍÑÉ¥À œ¸œ¤¥¸11=]}%5}aQ9M%=9Lè4(€€€€€€€€€€€€€€€€€€€½±±•Ğ Ÿ:Ã–rë–Ş—’ösŸ&œ°Á…Ñ °€œ´œ¹©½¥¸¡Á…Ñ ¹É•±…Ñ¥Ù•}Ñ¼¡Á¥ÑÕÉ•}‘¥È¤¹Á…ÉÑÌ¤°Á¥ÑÕÉ•}‘¥È¤4(€€€¥˜…¹}Ù¥•İ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ ¤è4(€€€€€€€™½ÈÉ•¥µ‰ÕÉÍ•µ•¹Ğ¥¸‘ˆ ¤¹•á•ÕÑ” Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”Í•ÉÙ¥•}½É‘•É}¥ôü½É‘•È‰ä¥œ°€¡½É‘•É}¥°¤¤è4(€€€€€€€€€€€½±±•Ğ Ÿ–Ş—–6WîOº\œ°ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™¥±•}Á…Ñ ¡É•¥µ‰ÕÉÍ•µ•¹Ğ¤°É•¥µ‰ÕÉÍ•µ•¹Ñl™¥±•}¹…µ”t°UMQ=5I}I%5	UIM59Q}%H¤4(€€€€€€€€€€€™½ÈÉ½Ü¥¸‘ˆ ¤¹•á•ÕÑ” Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÌİ¡•É”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥ôü½É‘•È‰ä¥œ°€¡É•¥µ‰ÕÉÍ•µ•¹Ñl¥t°¤¤è4(€€€€€€€€€€€€€€€½±±•Ğ Ÿ–Ş—–6WîOº\œ°ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡É½Ü¤°É½İl½É¥¥¹…±}™¥±•¹…µ”t°UMQ=5I}I%5	UIM59Q}%H¤4(€€€…É¡¥Ù•}™¥±”€ôÑ•µÁ™¥±”¹MÁ½½±•‘Q•µÁ½É…Éå¥±”¡µ…á}Í¥é”ôÄØ€¨€ÄÀÈĞ€¨€ÄÀÈĞ°µ½‘”ôÜ­ˆœ¤4(€€€ÑÉäè4(€€€€€€€İ¥Ñ é¥Á™¥±”¹i¥Á¥±”¡…É¡¥Ù•}™¥±”°€Üœ°½µÁÉ•ÍÍ¥½¸õé¥Á™¥±”¹i%A}MQ=I°…±±½İi¥ÀØĞõQÉÕ”¤…Ì…É¡¥Ù”è4(€€€€€€€€€€€ÕÍ•€ôÍ•Ğ ¤4(€€€€€€€€€€€µ¥ÍÍ¥¹œ€ôí™½±‘•Èèmt™½È™½±‘•È¥¸™½±‘•ÉÍô4(€€€€€€€€€€€™½È™½±‘•È¥¸™½±‘•ÉÌè4(€€€€€€€€€€€€€€€…É¡¥Ù”¹İÉ¥Ñ•ÍÑÈ¡™½±‘•È¬œ¼œ°ˆœœ¤4(€€€€€€€€€€€™½È™½±‘•È°Á…Ñ °¹…µ”¥¸™¥±•Ìè4(€€€€€€€€€€€€€€€¥˜¹½ĞÁ…Ñ ¹¥Í}™¥±” ¤è4(€€€€€€€€€€€€€€€€€€€µ¥ÍÍ¥¹m™½±‘•Ét¹…ÁÁ•¹¡¹…µ”¤4(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€€€€€ÍÑ•´°ÍÕ™™¥à€ô½Ì¹Á…Ñ ¹ÍÁ±¥Ñ•áĞ¡¹…µ”¤4(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ô™½±‘•È¬œ¼œ­¹…µ”4(€€€€€€€€€€€€€€€¥¹‘•à€ô€È4(€€€€€€€€€€€€€€€İ¡¥±”…¹‘¥‘…Ñ”¹…Í•™½± ¤¥¸ÕÍ•è4(€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ô˜í™½±‘•Éô½íÍÑ•µôµí¥¹‘•áõíÍÕ™™¥áôœ4(€€€€€€€€€€€€€€€€€€€¥¹‘•à€¬ô€Ä4(€€€€€€€€€€€€€€€ÕÍ•¹…‘¡…¹‘¥‘…Ñ”¹…Í•™½± ¤¤4(€€€€€€€€€€€€€€€…É¡¥Ù”¹İÉ¥Ñ”¡Á…Ñ °…¹‘¥‘…Ñ”¤4(€€€€€€€€€€€™½È™½±‘•È°¹…µ•Ì¥¸µ¥ÍÍ¥¹œ¹¥Ñ•µÌ ¤è4(€€€€€€€€€€€€€€€¥˜¹…µ•Ìè4(€€€€€€€€€€€€€€€€€€€…É¡¥Ù”¹İÉ¥Ñ•ÍÑÈ¡™½±‘•È¬œ¿òë–’ÇšZ’îÛ¢¾Óšb8¹ÑáĞœ°€ Ÿ’î—’â/¦f’îÛšr'¢ºÃ–öW¾ò3’öšr7–*‡–f£šZ’îÛ’â7–¶c–r£¾òiq¸œ¬q¸œ¹©½¥¸¡¹…µ•Ì¤¤¹•¹½‘” ÕÑ˜´àœ¤¤4(€€€€€€€…É¡¥Ù•}™¥±”¹Í••¬ À¤4(€€€€€€€É•ÍÁ½¹Í”€ôÍ•¹‘}™¥±”¡…É¡¥Ù•}™¥±”°µ¥µ•ÑåÁ”ô…ÁÁ±¥…Ñ¥½¸½é¥Àœ°…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‘½İ¹±½…‘}¹…µ”õ˜‰íÍ•ÕÉ•}™¥±•¹…µ”¡½É‘•Él½É‘•É}¹Õµ‰•Èt¤½È½É‘•É}¥‘ô·¦f’îØ¹é¥Àˆ¤4(€€€€€€€É•ÍÁ½¹Í”¹…±±}½¹}±½Í”¡…É¡¥Ù•}™¥±”¹±½Í”¤4(€€€€€€€É•ÑÕÉ¸É•ÍÁ½¹Í”4(€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€…É¡¥Ù•}™¥±”¹±½Í” ¤4(€€€€€€€É…¥Í”4(4(4)…ÁÀ¹É½ÕÑ” ˆ½¥¹Ù½¥•Ì½¹•Üˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¹•İ}¥¹Ù½¥” ¤è4(€€€¥˜¹½Ğ…¹}É•…Ñ•}¥¹Ù½¥” ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€É•ÅÕ•ÍÑ•‘}½É‘•É}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰Í•ÉÙ¥•}½É‘•É}¥ˆ°€ˆˆ¤4(€€€É•ÅÕ•ÍÑ•‘}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥€ôÉ•ÅÕ•ÍĞ¹…ÉÌ¹•Ğ ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥ˆ°€ˆˆ¤4(€€€Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô9½¹”4(€€€ÁÉ•™¥±±•‘}¥Ñ•µÌ€ômt4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰Pˆ…¹¹½ĞÉ•ÅÕ•ÍÑ•‘}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€™±…Í  ‹¢¾ß’î;–º‡š‚ã¦k¢şj–Ş—–6WîOº_Rš"C–>G–£ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€¥˜É•ÅÕ•ÍÑ•‘}½É‘•É}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õ¥¹Ğ¡É•ÅÕ•ÍÑ•‘}½É‘•É}¥¤¤¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰Í•ÉÙ¥•}½É‘•ÉÌˆ¤¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰Pˆ…¹É•ÅÕ•ÍÑ•‘}½É‘•É}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€É•ÅÕ•ÍÑ•‘}½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡¥¹Ğ¡É•ÅÕ•ÍÑ•‘}½É‘•É}¥¤¤4(€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•É}ÍÑ…ÉÑ}‘…Ñ”¡É•ÅÕ•ÍÑ•‘}½É‘•È¤4(€€€€€€€¥˜ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğè4(€€€€€€€€€€€É•ÑÕÉ¸ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰Pˆ…¹É•ÅÕ•ÍÑ•‘}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ°Í½ÕÉ•}½É‘•È€ôÉ•ÅÕ¥É•}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡¥¹Ğ¡É•ÅÕ•ÍÑ•‘}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤¤4(€€€€€€€ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•É}ÍÑ…ÉÑ}‘…Ñ”¡Í½ÕÉ•}½É‘•È¤4(€€€€€€€¥˜ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğè4(€€€€€€€€€€€É•ÑÕÉ¸ÍÑ…ÉÑ}‘…Ñ•}É•‘¥É•Ğ4(€€€€€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰tè4(€€€€€€€€€€€™±…Í  ‹¢şg’î÷–Ş—–6WîOº_–ŞËî?Rš"C¢ş–>G–£ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õÍ½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰t¤¤4(€€€€€€€±¥¹­•‘}¥¹Ù½¥”€ôÍ•ÉÙ¥•}½É‘•É}…Ñ¥Ù•}¥¹Ù½¥”¡Í½ÕÉ•}½É‘•Él‰¥‰t¤4(€€€€€€€¥˜±¥¹­•‘}¥¹Ù½¥”è4(€€€€€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô€üİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€€€€€¡±¥¹­•‘}¥¹Ù½¥•l‰¥‰t°Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€€€€€™±…Í  ‹¢şg’â«–Ş—–6W–ŞËî?šr'–Ï¢S–>G–£ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ±¥¹­•‘}¥¹Ù½¥•l‰¥‰t¤¤4(€€€€€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€™±…Í  ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷Rš"C–>G–£ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õÍ½ÕÉ•}½É‘•Él‰¥‰t¤¤4(€€€€€€€É•ÅÕ•ÍÑ•‘}½É‘•É}¥€ôÍÑÈ¡Í½ÕÉ•}½É‘•Él‰¥‰t¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€ÁÉ•™¥±±•‘}¥Ñ•µÌ€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¹Ù½¥•}¥Ñ•µÌ¡Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}™½É´ˆ°½É‘•É}¥õÍ½ÕÉ•}½É‘•Él‰¥‰t¤¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€¥˜¹½Ğœ¹ÕÍ•Él‰±¥•¹Ñ}¥‰tè4(€€€€€€€€€€€™±…Í  ‹¢¾ß¢SÎïº‡B–FcîG–ºk–º‹š"ßˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰‘…Í¡‰½…Éˆ¤¤4(€€€€€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌİ¡•É”¥€ô€üˆ°€¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t°¤¤¹™•Ñ¡…±° ¤4(€€€•±Í”è4(€€€€€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌ½É‘•È‰ä±¥•¹Ñ}¹Õµ‰•Èˆ¤¹™•Ñ¡…±° ¤4(€€€¥˜¹½Ğ±¥•¹ÑÍ}É½İÌè4(€€€€€€€™±…Í  ‹¢¾ß–#–"o–îë’â’â«–º‹š"ßˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰±¥•¹ÑÌˆ¤¤4(€€€ÁÉ½©•ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌİ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€¥¹Ù½¥”œ…¹¥Í}…Ñ¥Ù”€ô€Ä½É‘•È‰ä¹…µ”ˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Í•ÉÙ¥•}½É‘•ÉÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”ÍÑ…ÑÕÌ€„ô€±½Í•œ½É‘•È‰äÉ•…Ñ•‘}…Ğ‘•ÍŒ°¥‘•ÍŒˆ¤¹™•Ñ¡…±° ¤4(€€€¥˜¹½ĞÁÉ½©•ÑÍ}É½İÌè4(€€€€€€€™±…Í  ‹¢¾ß–#RÇî?Bš"[º‡B–FcîÓš*“¦†çn»ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰ÁÉ½©•ÑÌˆ¤¥˜¥Í}µ…¹…•È ¤•±Í”ÕÉ±}™½È ‰‘…Í¡‰½…Éˆ¤¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}¥¹Ù½¥•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸¤è4(€€€€€€€€€€€•á¥ÍÑ¥¹œ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€‰Í•±•Ğ¥¹Ù½¥•}¥™É½´¥¹Ù½¥•}Í…Ù•}Ñ½­•¹Ìİ¡•É”Ñ½­•¸€ô€üˆ°4(€€€€€€€€€€€€€€€€¡Í…Ù•}Ñ½­•¸°¤°4(€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€€€€€¥˜•á¥ÍÑ¥¹œ…¹•á¥ÍÑ¥¹l‰¥¹Ù½¥•}¥‰tè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ•á¥ÍÑ¥¹l‰¥¹Ù½¥•}¥‰t¤¤4(€€€€€€€€€€€™±…Í  ‹¢¾—–>G–£š¶–r£’şw–¶c¾ò3¢¾ß¢7–gˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}¥¹Ù½¥”ˆ¤¤4(€€€€€€€ÕÁ±½…‘Ì€ôÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤4(€€€€€€€¥˜¹½ĞÙ…±¥‘…Ñ•}…ÑÑ…¡µ•¹Ñ}ÕÁ±½…‘Ì¡ÕÁ±½…‘Ì¤è4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}¥¹Ù½¥”ˆ¤¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€¥¹Ù½¥•}¥€ôÉ•…Ñ•}¥¹Ù½¥•}™É½µ}™½É´¡Í…Ù•}Ñ½­•¸õÍ…Ù•}Ñ½­•¸¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}¥¹Ù½¥”ˆ¤¤4(€€€€€€€•á•ÁĞÍÅ±¥Ñ”Ì¹%¹Ñ•É¥ÑåÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í  ‹’şw–¶c–>G–£š^Û–>GRšVÃš6»–êO–Ëª¾ò3¢¾ß¦7šZÃš>C’ê“ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¹•İ}¥¹Ù½¥”ˆ¤¤4(€€€€€€€™±…Í  ‹–>G–£–ŞËRš"Cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€Ñ½‘…ä€ô‘…Ñ”¹Ñ½‘…ä ¤4(€€€Í•±•Ñ•‘}±¥•¹Ñ}¥€ôÍ½ÕÉ•}½É‘•Él‰±¥•¹Ñ}¥‰t¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ•±Í”±¥•¹ÑÍ}É½İÍlÁul‰¥‰t4(€€€Í•±•Ñ•‘}±¥•¹Ğ€ô¹•áĞ ¡É½Ü™½ÈÉ½Ü¥¸±¥•¹ÑÍ}É½İÌ¥˜É½İl‰¥‰t€ôôÍ•±•Ñ•‘}±¥•¹Ñ}¥¤°±¥•¹ÑÍ}É½İÍlÁt¤4(€€€Í•±•Ñ•‘}Ñ•É´€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ€¨™É½´Á…åµ•¹Ñ}Ñ•ÉµÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€¡Í•±•Ñ•‘}±¥•¹Ñl‰Á…åµ•¹Ñ}Ñ•Éµ}¥‰t°¤°4(€€€€¤¹™•Ñ¡½¹” ¤4(€€€‘•™…Õ±ÑÌ€ôì4(€€€€€€€€‰¥¹Ù½¥•}¹Õµ‰•Èˆè¹•áÑ}¥¹Ù½¥•}¹Õµ‰•È ¤°4(€€€€€€€€‰¥ÍÍÕ•}‘…Ñ”ˆèÑ½‘…ä¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰‘Õ•}‘…Ñ”ˆè…±Õ±…Ñ•}Á…åµ•¹Ñ}‘Õ•}‘…Ñ”¡Ñ½‘…ä¹¥Í½™½Éµ…Ğ ¤°Í•±•Ñ•‘}Ñ•É´¤¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}¥ˆè¥¹Ğ¡É•ÅÕ•ÍÑ•‘}½É‘•É}¥¤¥˜É•ÅÕ•ÍÑ•‘}½É‘•É}¥¹¥Í‘¥¥Ğ ¤•±Í”9½¹”°4(€€€€€€€€‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥ˆèÍ½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ•±Í”9½¹”°4(€€€€€€€€‰±¥•¹Ñ}¥ˆèÍ½ÕÉ•}½É‘•Él‰±¥•¹Ñ}¥‰t¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ•±Í”9½¹”°4(€€€ô4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰¥¹Ù½¥•}™½É´¹¡Ñµ°ˆ°4(€€€€€€€±¥•¹ÑÌõ±¥•¹ÑÍ}É½İÌ°4(€€€€€€€ÁÉ½©•ÑÌõÁÉ½©•ÑÍ}É½İÌ°4(€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌõÍ•ÉÙ¥•}½É‘•ÉÍ}É½İÌ°4(€€€€€€€‘•™…Õ±ÑÌõ‘•™…Õ±ÑÌ°4(€€€€€€€™½Éµ}Ñ¥Ñ±”ô‹š‚çš6»–Ş—–6WîOº_Rš"C–>G– ˆ¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ•±Í”€‹šZÃ–îë–>G– ˆ°4(€€€€€€€™½Éµ}¥Ñ•µÌõÁÉ•™¥±±•‘}¥Ñ•µÌ°4(€€€€€€€¥Í}•‘¥Ğõ…±Í”°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõmt°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€€€€Á…åµ•¹Ñ}Ñ•ÉµÌõÁ…åµ•¹Ñ}Ñ•Éµ}É½İÌ ¤°4(€€€€¤4(4(4)‘•˜¥¹Ù½¥•}¥Ñ•µÍ}™É½µ}™½É´ ¤è4(€€€É½İÌ€ômt4(€€€™½ÈÁÉ½©•Ñ}¥°…µ½Õ¹Ğ¥¸é¥À¡É•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰ÁÉ½©•Ñ}¥ˆ¤°É•ÅÕ•ÍĞ¹™½É´¹•Ñ±¥ÍĞ ‰…µ½Õ¹Ğˆ¤¤è4(€€€€€€€¥˜¹½ĞÁÉ½©•Ñ}¥è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€ÁÉ½©•Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌİ¡•É”¥€ô€ü…¹ÁÉ½©•Ñ}ÑåÁ”€ô€¥¹Ù½¥”œ…¹¥Í}…Ñ¥Ù”€ô€Äˆ°4(€€€€€€€€€€€€¡ÁÉ½©•Ñ}¥°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€¥˜¹½ĞÁÉ½©•Ğè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¦'š.§j¦†çn»’â7–¶c–r£š"[–ŞË–sR£¾ò3¢¾ß¦7šZÃ¦'š.§¦†çn»ˆ¤4(€€€€€€€É½İÌ¹…ÁÁ•¹ ¡ÁÉ½©•Ğ°Ñ½}™±½…Ğ¡…µ½Õ¹Ğ¤¤¤4(€€€¥˜¹½ĞÉ½İÌè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¢Ï–ÂG¦'š.§’â’â«¦†çn»šb;îˆ¤4(€€€¥˜ÍÕ´¡…µ½Õ¹Ğ™½È|°…µ½Õ¹Ğ¥¸É½İÌ¤€ğô€Àè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–>G–£¦G¦Šw–ş¦†ï–’Ÿ’ê8€Ãˆ¤4(€€€É•ÑÕÉ¸É½İÌ4(4(4)‘•˜Á½ÍÑ•‘}±¥•¹Ñ}¥ ¤è4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸¥¹Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰±¥•¹Ñ}¥ˆ¤¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¦'š.§–º‹š"ßˆ¤4(4(4)‘•˜Á½ÍÑ•‘}Í•ÉÙ¥•}½É‘•É}¥¡É•ÅÕ¥É•}ÍÑ…ÉÑ}‘…Ñ”õ…±Í”°É•ÅÕ¥É•õ…±Í”¤è4(€€€Ù…±Õ”€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í•ÉÙ¥•}½É‘•É}¥ˆ¤4(€€€¥˜¹½ĞÙ…±Õ”è4(€€€€€€€¥˜É•ÅÕ¥É•è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¦'š.§–Ï¢S–Ş—–6Wˆ¤4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€ÑÉäè4(€€€€€€€½É‘•É}¥€ô¥¹Ğ¡Ù…±Õ”¤4(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢¾ß¦'š.§šr'šV#–Ş—–6Wˆ¤4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡½É‘•É}¥¤4(€€€¥˜É•ÅÕ¥É•}ÍÑ…ÉÑ}‘…Ñ”…¹¹½Ğ½É‘•Él‰ÍÑ…ÉÑ}‘…Ñ”‰tè4(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹š&¦'–Ş—–6W¢şcšÊ‡šr'–ò–/š^—šr¾ò3¢¾ß–#ò[¢úG–Ş—–6W–æÛîÓš*“–ò–/š^—šrˆ¤4(€€€É•ÑÕÉ¸½É‘•É}¥4(4(4)‘•˜É•…Ñ•}¥¹Ù½¥•}™É½µ}™½É´¡Í…Ù•}Ñ½­•¸õ9½¹”¤è4(€€€±¥•¹Ñ}¥€ôÁ½ÍÑ•‘}±¥•¹Ñ}¥ ¤4(€€€¥˜¹½Ğ…¹}…•ÍÍ}±¥•¹Ğ¡±¥•¹Ñ}¥¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í•ÉÙ¥•}½É‘•É}¥€ôÁ½ÍÑ•‘}Í•ÉÙ¥•}½É‘•É}¥¡É•ÅÕ¥É•}ÍÑ…ÉÑ}‘…Ñ”õQÉÕ”°É•ÅÕ¥É•õQÉÕ”¤4(€€€Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô9½¹”4(€€€É•¥µ‰ÕÉÍ•µ•¹Ñ}¥€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}¥ˆ°€ˆˆ¤4(€€€¥˜É•¥µ‰ÕÉÍ•µ•¹Ñ}¥è4(€€€€€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ñ}¥¹¥Í‘¥¥Ğ ¤è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_šv—šêCš^ƒšV#ˆ¤4(€€€€€€€Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ€¨™É½´ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡¥¹Ğ¡É•¥µ‰ÕÉÍ•µ•¹Ñ}¥¤°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€¥˜¹½ĞÍ½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğ½ÈÍ½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰Í•ÉÙ¥•}½É‘•É}¥‰t€„ôÍ•ÉÙ¥•}½É‘•É}¥è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_’â;š&¦'–Ş—–6W’â7–2ç¦7ˆ¤4(€€€€€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–Ş—–6WîOº_–º‡š‚ã¦k¢ş–B;š&7¢÷Rš"C–>G–£ˆ¤4(€€€€€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥¹Ù½¥•}¥‰tè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢şg’î÷–Ş—–6WîOº_–ŞËî?Rš"C¢ş–>G–£ˆ¤4(€€€€€€€¥˜Í•ÉÙ¥•}½É‘•É}…Ñ¥Ù•}¥¹Ù½¥”¡Í•ÉÙ¥•}½É‘•É}¥¤è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹¢şg’â«–Ş—–6W–ŞËî?šr'–Ï¢S–>G–£¾ò3’â7¢÷¦7–’7Rš"Cˆ¤4(€€€€€€€Í½ÕÉ•}½É‘•È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰Í•±•Ğ±¥•¹Ñ}¥™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”¥€ô€üˆ°4(€€€€€€€€€€€€¡Í•ÉÙ¥•}½É‘•É}¥°¤°4(€€€€€€€€¤¹™•Ñ¡½¹” ¤4(€€€€€€€¥˜Í½ÕÉ•}½É‘•È…¹Í½ÕÉ•}½É‘•Él‰±¥•¹Ñ}¥‰t…¹±¥•¹Ñ}¥€„ôÍ½ÕÉ•}½É‘•Él‰±¥•¹Ñ}¥‰tè4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹–>G–£–º‹š"ß–ş¦†ï’â;–Ş—–6W–Ï¢S–º‹š"ß’â¢Óˆ¤4(€€€¥¹Ù½¥•}¹Õµ‰•È€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¥¹Ù½¥•}¹Õµ‰•Èˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€¥˜¹½Ğ¥¹Ù½¥•}¹Õµ‰•Èè4(€€€€€€€¥¹Ù½¥•}¹Õµ‰•È€ô¹•áÑ}¥¹Ù½¥•}¹Õµ‰•È ¤4(€€€¥˜¥¹Ù½¥•}¹Õµ‰•É}•á¥ÍÑÌ¡¥¹Ù½¥•}¹Õµ‰•È¤è4(€€€€€€€¥¹Ù½¥•}¹Õµ‰•È€ô¹•áÑ}¥¹Ù½¥•}¹Õµ‰•È ¤4(€€€¥Ñ•µ}É½İÌ€ô¥¹Ù½¥•}¥Ñ•µÍ}™É½µ}™½É´ ¤4(€€€ÍÑ…ÑÕÌ€ô€‰½µÁ±•Ñ•ˆ4(€€€ÕÉÍ½È€ô9½¹”4(€€€™½È|¥¸É…¹” ÌÀ¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€ÕÉÍ½È€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼¥¹Ù½¥•Ì€ 4(€€€€€€€€€€€€€€€€€€€¥¹Ù½¥•}¹Õµ‰•È°±¥•¹Ñ}¥°Í•ÉÙ¥•}½É‘•É}¥°¥ÍÍÕ•}‘…Ñ”°‘Õ•}‘…Ñ”°ÕÉÉ•¹ä°¹½Ñ•Ì°ÍÑ…ÑÕÌ°É•…Ñ•‘}‰ä°É•…Ñ•‘}…Ğ4(€€€€€€€€€€€€€€€€¤Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€€€€€¥¹Ù½¥•}¹Õµ‰•È°4(€€€€€€€€€€€€€€€€€€€±¥•¹Ñ}¥°4(€€€€€€€€€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¥ÍÍÕ•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘Õ•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÕÉÉ•¹äˆ°€‰UMˆ¤°4(€€€€€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¹½Ñ•Ìˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€€€€€€€€€ÍÑ…ÑÕÌ°4(€€€€€€€€€€€€€€€€€€€œ¹ÕÍ•Él‰¥‰t°4(€€€€€€€€€€€€€€€€€€€¹½Ü ¤°4(€€€€€€€€€€€€€€€€¤°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€‰É•…¬4(€€€€€€€•á•ÁĞÍÅ±¥Ñ”Ì¹%¹Ñ•É¥ÑåÉÉ½Èè4(€€€€€€€€€€€¥¹Ù½¥•}¹Õµ‰•È€ô¹•áÑ}¥¹Ù½¥•}¹Õµ‰•È ¤4(€€€¥˜ÕÉÍ½È¥Ì9½¹”è4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰U¹…‰±”Ñ¼•¹•É…Ñ”„Õ¹¥ÅÕ”¥¹Ù½¥”¹Õµ‰•È¸ˆ¤4(€€€¥¹Ù½¥•}¥€ôÕÉÍ½È¹±…ÍÑÉ½İ¥4(€€€¥˜Í…Ù•}Ñ½­•¸è4(€€€€€€€™¥¹¥Í¡}¥¹Ù½¥•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°¥¹Ù½¥•}¥¤4(€€€™½ÈÁÉ½©•Ğ°…µ½Õ¹Ğ¥¸¥Ñ•µ}É½İÌè4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼¥¹Ù½¥•}¥Ñ•µÌ€¡¥¹Ù½¥•}¥°ÁÉ½©•Ñ}¥°‘•ÍÉ¥ÁÑ¥½¸°…µ½Õ¹Ğ°Ñ…á}É…Ñ”¤4(€€€€€€€€€€€Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡¥¹Ù½¥•}¥°ÁÉ½©•Ñl‰¥‰t°ÁÉ½©•Ñl‰¹…µ”‰t°Ñ½}™±½…Ğ¡…µ½Õ¹Ğ¤°ÁÉ½©•Ñl‰Ñ…á}É…Ñ”‰t¤°4(€€€€€€€€¤4(€€€™½ÈÕÁ±½…‘•¥¸ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤è4(€€€€€€€¥˜ÕÁ±½…‘•…¹ÕÁ±½…‘•¹™¥±•¹…µ”è4(€€€€€€€€€€€Í…Ù•}ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹Ğ¡¥¹Ù½¥•}¥°ÕÁ±½…‘•¤4(€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğè4(€€€€€€€½Áå}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}…ÑÑ…¡µ•¹ÑÍ}Ñ½}¥¹Ù½¥”¡¥¹Ù½¥•}¥°Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤4(€€€€€€€½Áå}µ¥±•…•}ÁÉ½½™Í}Ñ½}¥¹Ù½¥”¡¥¹Ù½¥•}¥°Í•ÉÙ¥•}½É‘•É}¥¤4(€€€¥˜Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ğè4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô€üİ¡•É”¥€ô€ü…¹¥¹Ù½¥•}¥¥Ì¹Õ±°ˆ°4(€€€€€€€€€€€€¡¥¹Ù½¥•}¥°Í½ÕÉ•}É•¥µ‰ÕÉÍ•µ•¹Ñl‰¥‰t¤°4(€€€€€€€€¤4(€€€¥¹Ù½¥•}ÍÕµµ…Éä€ô˜‹¦G¦Šw¾òiíµ½¹•ä¡¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•}¥¥lÑ½Ñ…°t°É•ÅÕ•ÍĞ¹™½É´¹•Ğ ÕÉÉ•¹äœ°€UMœ¤¥ôˆ4(€€€±½}…Ñ¥½¸ ‰É•…Ñ”ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•}¹Õµ‰•È°¥¹Ù½¥•}ÍÕµµ…Éä¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€É•ÑÕÉ¸¥¹Ù½¥•}¥4(4(4)…ÁÀ¹É½ÕÑ” ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½•‘¥Ğˆ°µ•Ñ¡½‘Ìõl‰Pˆ°€‰A=MP‰t¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•‘¥Ñ}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ€ô±½…‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤4(€€€¥˜¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t€„ô€‰‘É…™Ğˆè4(€€€€€€€™±…Í  ‹–>«šr'º‡B–Fc¢ÂšVÓ’âë¢6'¢ÿj–>G–£–>¿’î—ò[¢úGˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€¥˜¥¹Ù½¥•l‰É•…Ñ•‘}‰ä‰t€„ôœ¹ÕÍ•Él‰¥‰t…¹¹½Ğ¥Í}µ…¹…•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌİ¡•É”¥€ô€üˆ°€¡œ¹ÕÍ•Él‰±¥•¹Ñ}¥‰t°¤¤¹™•Ñ¡…±° ¤4(€€€•±Í”è4(€€€€€€€±¥•¹ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´±¥•¹ÑÌ½É‘•È‰ä±¥•¹Ñ}¹Õµ‰•Èˆ¤¹™•Ñ¡…±° ¤4(€€€ÁÉ½©•ÑÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ€¨™É½´ÁÉ½©•ÑÌ4(€€€€€€€İ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€¥¹Ù½¥”œ…¹€¡¥Í}…Ñ¥Ù”€ô€Ä½È¥¥¸€ 4(€€€€€€€€€€€Í•±•ĞÁÉ½©•Ñ}¥™É½´¥¹Ù½¥•}¥Ñ•µÌİ¡•É”¥¹Ù½¥•}¥€ô€ü4(€€€€€€€€¤¤4(€€€€€€€½É‘•È‰ä¥Í}…Ñ¥Ù”‘•ÍŒ°¹…µ”4(€€€€€€€€ˆˆˆ°4(€€€€€€€€¡¥¹Ù½¥•}¥°¤°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Í•ÉÙ¥•}½É‘•ÉÍ}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´Í•ÉÙ¥•}½É‘•ÉÌİ¡•É”ÍÑ…ÑÕÌ€„ô€±½Í•œ½È¥€ô€ü½É‘•È‰äÉ•…Ñ•‘}…Ğ‘•ÍŒ°¥‘•ÍŒˆ°€¡¥¹Ù½¥•l‰Í•ÉÙ¥•}½É‘•É}¥‰t½È€À°¤¤¹™•Ñ¡…±° ¤4(€€€¥˜É•ÅÕ•ÍĞ¹µ•Ñ¡½€ôô€‰A=MPˆè4(€€€€€€€Í…Ù•}Ñ½­•¸€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Í…Ù•}Ñ½­•¸ˆ°€ˆˆ¤4(€€€€€€€¥˜¹½Ğ±…¥µ}¥¹Ù½¥•}Í…Ù•}Ñ½­•¸¡Í…Ù•}Ñ½­•¸°¥¹Ù½¥•}¥¤è4(€€€€€€€€€€€™±…Í  ‹¢¾—–>G–£–ŞËî?’şw–¶c¾ò3¢¾ß–.ÿ¦7–’7š>C’ê“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€€€€€ÕÁ±½…‘Ì€ôÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤4(€€€€€€€¥˜¹½ĞÙ…±¥‘…Ñ•}…ÑÑ…¡µ•¹Ñ}ÕÁ±½…‘Ì¡ÕÁ±½…‘Ì°¥¹Ù½¥•}¥¤è4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€ÕÁ‘…Ñ•}¥¹Ù½¥•}™É½µ}™½É´¡¥¹Ù½¥•}¥¤4(€€€€€€€€€€€±½}…Ñ¥½¸ ‰ÕÁ‘…Ñ”ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°€‹’ş»šRç–>G–£––ºäˆ¤4(€€€€€€€•á•ÁĞY…±Õ•ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€€€€€•á•ÁĞÍÅ±¥Ñ”Ì¹%¹Ñ•É¥ÑåÉÉ½Èè4(€€€€€€€€€€€‘ˆ ¤¹É½±±‰…¬ ¤4(€€€€€€€€€€€™±…Í  ‹–>G–£ò[–>ß–ŞËî?–¶c–r£¾ò3¢¾ßšnÓš6‹’â’â«–>G–£ò[–>ß–B;–7’şw–¶cˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€€€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€€€€€™±…Í  ‹–>G–£–ŞË’şw–¶cˆ°€‰ÍÕ•ÍÌˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€‘•™…Õ±ÑÌ€ôì4(€€€€€€€€‰¥ˆè¥¹Ù½¥•l‰¥‰t°4(€€€€€€€€‰¥¹Ù½¥•}¹Õµ‰•Èˆè¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°4(€€€€€€€€‰±¥•¹Ñ}¥ˆè¥¹Ù½¥•l‰±¥•¹Ñ}¥‰t°4(€€€€€€€€‰É•…Ñ•‘}‰äˆè¥¹Ù½¥•l‰É•…Ñ•‘}‰ä‰t°4(€€€€€€€€‰ÍÑ…ÑÕÌˆè¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€€‰¥ÍÍÕ•}‘…Ñ”ˆè¥¹Ù½¥•l‰¥ÍÍÕ•}‘…Ñ”‰t°4(€€€€€€€€‰‘Õ•}‘…Ñ”ˆè¥¹Ù½¥•l‰‘Õ•}‘…Ñ”‰t°4(€€€€€€€€‰ÕÉÉ•¹äˆè¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t°4(€€€€€€€€‰¹½Ñ•Ìˆè¥¹Ù½¥•l‰¹½Ñ•Ì‰t°4(€€€€€€€€‰Í•ÉÙ¥•}½É‘•É}¥ˆè¥¹Ù½¥•l‰Í•ÉÙ¥•}½É‘•É}¥‰t°4(€€€ô4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰¥¹Ù½¥•}™½É´¹¡Ñµ°ˆ°4(€€€€€€€±¥•¹ÑÌõ±¥•¹ÑÍ}É½İÌ°4(€€€€€€€ÁÉ½©•ÑÌõÁÉ½©•ÑÍ}É½İÌ°4(€€€€€€€Í•ÉÙ¥•}½É‘•ÉÌõÍ•ÉÙ¥•}½É‘•ÉÍ}É½İÌ°4(€€€€€€€‘•™…Õ±ÑÌõ‘•™…Õ±ÑÌ°4(€€€€€€€™½Éµ}Ñ¥Ñ±”ô‹ò[¢úG–>G– ˆ°4(€€€€€€€™½Éµ}¥Ñ•µÌõ¥Ñ•µÌ°4(€€€€€€€¥Í}•‘¥ĞõQÉÕ”°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥•}¥¤°4(€€€€€€€Í…Ù•}Ñ½­•¸õÍ•É•ÑÌ¹Ñ½­•¹}ÕÉ±Í…™” ÈĞ¤°4(€€€€€€€Á…åµ•¹Ñ}Ñ•ÉµÌõÁ…åµ•¹Ñ}Ñ•Éµ}É½İÌ ¤°4(€€€€¤4(4(4)‘•˜ÕÁ‘…Ñ•}¥¹Ù½¥•}™É½µ}™½É´¡¥¹Ù½¥•}¥¤è4(€€€±¥•¹Ñ}¥€ôÁ½ÍÑ•‘}±¥•¹Ñ}¥ ¤4(€€€¥˜¹½Ğ…¹}…•ÍÍ}±¥•¹Ğ¡±¥•¹Ñ}¥¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€Í•ÉÙ¥•}½É‘•É}¥€ôÁ½ÍÑ•‘}Í•ÉÙ¥•}½É‘•É}¥ ¤4(€€€¥Ñ•µ}É½İÌ€ô¥¹Ù½¥•}¥Ñ•µÍ}™É½µ}™½É´ ¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”¥¹Ù½¥•Ì4(€€€€€€€Í•Ğ±¥•¹Ñ}¥€ô€ü°Í•ÉÙ¥•}½É‘•É}¥€ô€ü°¥ÍÍÕ•}‘…Ñ”€ô€ü°‘Õ•}‘…Ñ”€ô€ü°ÕÉÉ•¹ä€ô€ü°4(€€€€€€€€€€€¹½Ñ•Ì€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€ 4(€€€€€€€€€€€±¥•¹Ñ}¥°4(€€€€€€€€€€€Í•ÉÙ¥•}½É‘•É}¥°4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¥ÍÍÕ•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰‘Õ•}‘…Ñ”ˆ¤°4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÕÉÉ•¹äˆ°€‰UMˆ¤°4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰¹½Ñ•Ìˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€¥¹Ù½¥•}¥°4(€€€€€€€€¤°4(€€€€¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´¥¹Ù½¥•}¥Ñ•µÌİ¡•É”¥¹Ù½¥•}¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(€€€™½ÈÁÉ½©•Ğ°…µ½Õ¹Ğ¥¸¥Ñ•µ}É½İÌè4(€€€€€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€€ˆˆˆ4(€€€€€€€€€€€¥¹Í•ÉĞ¥¹Ñ¼¥¹Ù½¥•}¥Ñ•µÌ€¡¥¹Ù½¥•}¥°ÁÉ½©•Ñ}¥°‘•ÍÉ¥ÁÑ¥½¸°…µ½Õ¹Ğ°Ñ…á}É…Ñ”¤4(€€€€€€€€€€€Ù…±Õ•Ì€ ü°€ü°€ü°€ü°€ü¤4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€€¡¥¹Ù½¥•}¥°ÁÉ½©•Ñl‰¥‰t°ÁÉ½©•Ñl‰¹…µ”‰t°…µ½Õ¹Ğ°ÁÉ½©•Ñl‰Ñ…á}É…Ñ”‰t¤°4(€€€€€€€€¤4(€€€™½ÈÕÁ±½…‘•¥¸ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤è4(€€€€€€€¥˜ÕÁ±½…‘•…¹ÕÁ±½…‘•¹™¥±•¹…µ”è4(€€€€€€€€€€€Í…Ù•}ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹Ğ¡¥¹Ù½¥•}¥°ÕÁ±½…‘•¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°İ¡•É”¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜¥¹Ù½¥•}‘•Ñ…¥°¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ€ô±½…‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤4(€€€¥˜¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t€ôô€‰‘É…™Ğˆ…¹¥¹Ù½¥•l‰É•…Ñ•‘}‰ä‰t€ôôœ¹ÕÍ•Él‰¥‰tè4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€É•…Ñ½È€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ¹…µ”°•µ…¥°™É½´ÕÍ•ÉÌİ¡•É”¥€ô€üˆ°€¡¥¹Ù½¥•l‰É•…Ñ•‘}‰ä‰t°¤¤¹™•Ñ¡½¹” ¤4(€€€Í•ÉÙ¥•}½É‘•È€ô¥¹Ù½¥•}Í•ÉÙ¥•}½É‘•È¡¥¹Ù½¥”¤4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰¥¹Ù½¥•}‘•Ñ…¥°¹¡Ñµ°ˆ°4(€€€€€€€¥¹Ù½¥”õ¥¹Ù½¥”°4(€€€€€€€±¥•¹Ğõ±¥•¹Ğ°4(€€€€€€€¥Ñ•µÌõ¥Ñ•µÌ°4(€€€€€€€Ñ½Ñ…±Ìõ¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•}¥¤°4(€€€€€€€É•…Ñ½ÈõÉ•…Ñ½È°4(€€€€€€€Í•ÉÙ¥•}½É‘•ÈõÍ•ÉÙ¥•}½É‘•È°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõ•Ñ}¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥•}¥¤°4(€€€€€€€½µÁ…¹äõ•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤°4(€€€€€€€Ñ•ÉµÌõ•Ñ}¥¹Ù½¥•}Ñ•ÉµÌ ¤°4(€€€€€€€Á…åµ•¹Ğõ•Ñ}Á…åµ•¹Ñ}¥¹ÍÑÉÕÑ¥½¹Ì ¤°4(€€€€€€€±…‰•±ÌõMQQUM}1	1L°4(€€€€€€€Ñ½‘…äõ‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€•µ…¥±}‘•±¥Ù•Éäõ•µ…¥±}‘•±¥Ù•Éå}ÍÕµµ…Éä ‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥¤°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½…‘µ¥¸µÍÑ…ÑÕÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜…‘µ¥¹}ÕÁ‘…Ñ•}¥¹Ù½¥•}ÍÑ…ÑÕÌ¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€¥˜œ¹ÕÍ•Él‰É½±”‰t€„ô€‰…‘µ¥¸ˆè4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¥¹Ù½¥•l‰Á…¥‘}…Ğ‰tè4(€€€€€€€™±…Í  ‹¢şg–òƒ–>G–£–ŞËî?š‚ã¦R¾ò3’â7¢÷–7’ş»šRç’âë’şw–¶cšr«š>C’ê“*Ûšˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€Ñ…É•Ñ}ÍÑ…ÑÕÌ€ôÉ•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤4(€€€¥˜Ñ…É•Ñ}ÍÑ…ÑÕÌ€„ô€‰‘É…™Ğˆè4(€€€€€€€™±…Í  ‹–öO–&7–>«–¢ºãº‡B–Fc–Âšr«š‚ã¦R–>G–£šRç’âë’şw–¶cšr«š>C’ê“*Ûšˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÍÑ…ÑÕÌ€ô€‘É…™Ğœ°É•ÑÕÉ¹}É•…Í½¸€ô¹Õ±°İ¡•É”¥€ô€üˆ°4(€€€€€€€€¡¥¹Ù½¥•}¥°¤°4(€€€€¤4(€€€É•…Ñ•}µ•ÍÍ…” 4(€€€€€€€¥¹Ù½¥•l‰É•…Ñ•‘}‰ä‰t°4(€€€€€€€€‹–>G–£*Ûš–ŞË¢ÂšVĞˆ°4(€€€€€€€˜‹º‡B–Fc–ŞË–Â–>G– í¥¹Ù½¥•l¥¹Ù½¥•}¹Õµ‰•Èuôƒ¢ÂšVÓ’âë’şw–¶cšr«š>C’ê“*Ûšˆ°4(€€€€€€€ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰ÍÑ…ÑÕÍ}¡…¹”ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°€‹¢ÂšVÓ’âë’şw–¶cšr«š>C’êˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–>G–£*Ûš–ŞË’ş»šRç’âë’şw–¶cšr«š>C’ê“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½Ù½¥ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Ù½¥‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€¥˜¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t€ôô€‰½µÁ±•Ñ•ˆè4(€€€€€€€™±…Í  ‹–º‡š‚ã–º3š"C–B;j–>G–£’â7¢÷’ös–êˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÍÑ…ÑÕÌ€ô€Ù½¥œİ¡•É”¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰Ù½¥ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°€‹’ös–ê–>G– ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–>G–£–ŞË’ös–êˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€€ŒØÀ¸Ä¸ÈÌäèƒ–"ƒ¦f“šv¦fC–º3–£RÇ¢>s–6W¦7ö»3–>G– ·–"ƒ¦f“7–Ï–ºk¾ò#¦îc¢º“¢Ò‹–*„¿î?B¿º‡B–Fc¾ò'¾ò04(€€€€Œƒ’â7–7š2'¢K¢&Ë–gš¶ï¾òoš^ƒšv¦fCš^Û¦n’â·–ò?¢Ş¿RÇ¦^ã¦^£’òk–#¢†3š.›š"«¾ò ĞÀÏ¾ò'4(€€€¥˜¹½Ğ¡…Í}…Ñ¥½¹}Á•Éµ¥ÍÍ¥½¸ ‰¥¹Ù½¥•Ìˆ°€‰‘•±•Ñ”ˆ¤è4(€€€€€€€™±…Í  ‹’öƒšÊ‡šr'–"ƒ¦f“–>G–£jšv¦fC¾ò3¢¾ß¢SÎïº‡B–Fc–r£šv¦fCº‡B’â·–ò–B¿ˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€Í¡ÕÑ¥°¹ÉµÑÉ•”¡¥¹Ù½¥•}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡¥¹Ù½¥•}¥¤°¥¹½É•}•ÉÉ½ÉÌõQÉÕ”¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰ÕÁ‘…Ñ”µ•ÍÍ…•ÌÍ•Ğ±¥¹¬€ô¹Õ±°İ¡•É”±¥¹¬€ô€ü½È±¥¹¬€ô€üˆ°4(€€€€€€€€¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤°ÕÉ±}™½È ‰•‘¥Ñ}¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤°4(€€€€¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹ÑÌÍ•Ğ¥¹Ù½¥•}¥€ô¹Õ±°İ¡•É”¥¹Ù½¥•}¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´¥¹Ù½¥•Ìİ¡•É”¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰‘•±•Ñ”ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°˜‹–"ƒ¦f“*Ûš’âèí¥¹Ù½¥•lÍÑ…ÑÕÌuôƒj–>G– ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–>G–£–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•Ìˆ¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½µ…É¬µÁ…¥ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜µ…É­}¥¹Ù½¥•}Á…¥¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€¥˜¹½Ğ¥Í}¥¹Ñ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t€„ô€‰½µÁ±•Ñ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'–ŞË–º3š"Cj–>G–£š&7¢÷š‚ã¦Rˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€Ñ½Ñ…±Ì€ô¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•}¥¤4(€€€‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÁ…¥‘}…Ğ€ô€ü°Á…åµ•¹Ñ}…µ½Õ¹Ğ€ô€ü°Á…åµ•¹Ñ}¹½Ñ”€ô€ü4(€€€€€€€İ¡•É”¥€ô€ü4(€€€€€€€€ˆˆˆ°4(€€€€€€€€ 4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Á…¥‘}…Ğˆ¤½È‘…Ñ”¹Ñ½‘…ä ¤¹¥Í½™½Éµ…Ğ ¤°4(€€€€€€€€€€€Ñ½}™±½…Ğ¡É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Á…åµ•¹Ñ}…µ½Õ¹Ğˆ¤°Ñ½Ñ…±Íl‰Ñ½Ñ…°‰t¤°4(€€€€€€€€€€€É•ÅÕ•ÍĞ¹™½É´¹•Ğ ‰Á…åµ•¹Ñ}¹½Ñ”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤°4(€€€€€€€€€€€¥¹Ù½¥•}¥°4(€€€€€€€€¤°4(€€€€¤4(€€€±½}…Ñ¥½¸ ‰µ…É­}Á…¥ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°€‹¢ºÃ–öW–>G–£š‚ã¦R ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–>G–£–ŞËš‚ã¦Rˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½Õ¹µ…É¬µÁ…¥ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Õ¹µ…É­}¥¹Ù½¥•}Á…¥¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÁ…¥‘}…Ğ€ô¹Õ±°°Á…åµ•¹Ñ}…µ½Õ¹Ğ€ô¹Õ±°°Á…åµ•¹Ñ}¹½Ñ”€ô¹Õ±°İ¡•É”¥€ô€üˆ°€¡¥¹Ù½¥•}¥°¤¤4(€€€±½}…Ñ¥½¸ ‰Õ¹µ…É­}Á…¥ˆ°€‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°€‹–>[šÚ#–>G–£š‚ã¦R ˆ¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í  ‹–>G–£š‚ã¦R¢ºÃ–öW–ŞË–>[šÚ#ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½Í•¹ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜Í•¹‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”€ôÉ•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…‰½ÉĞ ĞÀÌ¤4(€€€¥˜¥¹Ù½¥•l‰ÍÑ…ÑÕÌ‰t€„ô€‰½µÁ±•Ñ•ˆè4(€€€€€€€™±…Í  ‹–>«šr'î?B–º‡š‚ã–º3š"C–B;j–>G–£š&7¢÷–>G¦¦
»’îÛˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€ÑÉäè4(€€€€€€€Í•¹‘}¥¹Ù½¥•}•µ…¥°¡¥¹Ù½¥•}¥¤4(€€€•á•ÁĞá•ÁÑ¥½¸…Ì•ÉÉ½Èè4(€€€€€€€…ÁÀ¹±½•È¹•á•ÁÑ¥½¸ ‰U¹…‰±”Ñ¼Í•¹¥¹Ù½¥”€•Ìˆ°¥¹Ù½¥•}¥¤4(€€€€€€€™±…Í ¡˜‹–>G–£¦
»’îÛ–>G¦–’Ç¢Ò—¾òií•ÉÉ½Éôˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€™±…Í  ‹–>G–£¦
»’îÛ–ŞË–>G¦ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½…ÑÑ…¡µ•¹ÑÌˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÕÁ±½…‘}…ÑÑ…¡µ•¹Ğ¡¥¹Ù½¥•}¥¤è4(€€€É•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡¥¹Ù½¥•}¥¤4(€€€ÕÁ±½…‘Ì€ôÕÁ±½…‘•‘}…ÑÑ…¡µ•¹ÑÍ}™É½µ}É•ÅÕ•ÍĞ ¤4(€€€¥˜¹½ĞÕÁ±½…‘Ìè4(€€€€€€€™±…Í  ‹¢¾ß¦'š.§¢š’â+’òƒj¦f’îÛˆ°€‰•ÉÉ½Èˆ¤4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€¥˜¹½ĞÙ…±¥‘…Ñ•}…ÑÑ…¡µ•¹Ñ}ÕÁ±½…‘Ì¡ÕÁ±½…‘Ì°¥¹Ù½¥•}¥¤è4(€€€€€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(€€€™½ÈÕÁ±½…‘•¥¸ÕÁ±½…‘Ìè4(€€€€€€€Í…Ù•}ÕÁ±½…‘•‘}…ÑÑ…¡µ•¹Ğ¡¥¹Ù½¥•}¥°ÕÁ±½…‘•¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€™±…Í ¡˜‹–ŞË’â+’ò€í±•¸¡ÕÁ±½…‘Ì¥ôƒ’â«¦f’îÛˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ¥¹Ù½¥•}¥¤¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥øˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘½İ¹±½…‘}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡…ÑÑ…¡µ•¹Ñl‰¥¹Ù½¥•}¥‰t¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±”¡…ÑÑ…¡µ•¹Ñ}™¥±•}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°‘½İ¹±½…‘}¹…µ”õ…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t¤4(4(4)…ÁÀ¹É½ÕÑ” ˆ½…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½ÁÉ•Ù¥•Üˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜ÁÉ•Ù¥•İ}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡…ÑÑ…¡µ•¹Ñl‰¥¹Ù½¥•}¥‰t¤4(€€€É•ÑÕÉ¸Í…™•}…ÑÑ…¡µ•¹Ñ}É•ÍÁ½¹Í” 4(€€€€€€€…ÑÑ…¡µ•¹Ñ}™¥±•}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½…ÑÑ…¡µ•¹ÑÌ¼ñ¥¹Ğé…ÑÑ…¡µ•¹Ñ}¥ø½‘•±•Ñ”ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜‘•±•Ñ•}…ÑÑ…¡µ•¹Ğ¡…ÑÑ…¡µ•¹Ñ}¥¤è4(€€€…ÑÑ…¡µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” ‰Í•±•Ğ€¨™É½´¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤¹™•Ñ¡½¹” ¤4(€€€¥˜¹½Ğ…ÑÑ…¡µ•¹Ğè4(€€€€€€€…‰½ÉĞ ĞÀĞ¤4(€€€É•ÅÕ¥É•}¥¹Ù½¥•}…•ÍÌ¡…ÑÑ…¡µ•¹Ñl‰¥¹Ù½¥•}¥‰t¤4(€€€ÑÉäè4(€€€€€€€½Ì¹É•µ½Ù”¡…ÑÑ…¡µ•¹Ñ}™¥±•}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤¤4(€€€•á•ÁĞ¥±•9½Ñ½Õ¹‘ÉÉ½Èè4(€€€€€€€Á…ÍÌ4(€€€‘ˆ ¤¹•á•ÕÑ” ‰‘•±•Ñ”™É½´¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌİ¡•É”¥€ô€üˆ°€¡…ÑÑ…¡µ•¹Ñ}¥°¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(€€€¥˜É•ÅÕ•ÍĞ¹¡•…‘•ÉÌ¹•Ğ ‰`µI•ÅÕ•ÍÑ•µ]¥Ñ ˆ¤€ôô€‰a51!ÑÑÁI•ÅÕ•ÍĞˆè4(€€€€€€€É•ÑÕÉ¸©Í½¹¥™ä¡ì‰½¬ˆèQÉÕ”°€‰…ÑÑ…¡µ•¹Ñ}¥ˆè…ÑÑ…¡µ•¹Ñ}¥‘ô¤4(€€€™±…Í  ‹¦f’îÛ–ŞË–"ƒ¦f“ˆ°€‰ÍÕ•ÍÌˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡ÕÉ±}™½È ‰¥¹Ù½¥•}‘•Ñ…¥°ˆ°¥¹Ù½¥•}¥õ…ÑÑ…¡µ•¹Ñl‰¥¹Ù½¥•}¥‰t¤¤4(4(4)…ÁÀ¹Á½ÍĞ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½•áÁ½ÉĞˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ½ÉÑ}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ€ô±½…‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤4(€€€…É¡¥Ù•}¹…µ”°…É¡¥Ù•}‰åÑ•Ì€ô‰Õ¥±‘}¥¹Ù½¥•}é¥À¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€	åÑ•Í%<¡…É¡¥Ù•}‰åÑ•Ì¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½é¥Àˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ˜‰í…É¡¥Ù•}¹…µ•ô¹é¥Àˆ°4(€€€€¤4(4(4)…ÁÀ¹•Ğ ˆ½¥¹Ù½¥•Ì¼ñ¥¹Ğé¥¹Ù½¥•}¥ø½•áÁ½ÉĞµÁ‘˜ˆ¤4)±½¥¹}É•ÅÕ¥É•4)‘•˜•áÁ½ÉÑ}¥¹Ù½¥•}Á‘˜¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ€ô±½…‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤4(€€€¥¹Ù½¥•}¹…µ”€ôÍ•ÕÉ•}™¥±•¹…µ”¡¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t¤½È˜‰¥¹Ù½¥”µí¥¹Ù½¥•l¥uôˆ4(€€€É•ÑÕÉ¸Í•¹‘}™¥±” 4(€€€€€€€	åÑ•Í%<¡É•¹‘•É}¥¹Ù½¥•}Á‘˜¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤¤°4(€€€€€€€µ¥µ•ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€…Í}…ÑÑ…¡µ•¹ĞõQÉÕ”°4(€€€€€€€‘½İ¹±½…‘}¹…µ”õ˜‰í¥¹Ù½¥•}¹…µ•ô¹Á‘˜ˆ°4(€€€€¤4(4(4)‘•˜‰Õ¥±‘}¥¹Ù½¥•}é¥À¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤è4(€€€…É¡¥Ù•}¹…µ”€ôÍ•ÕÉ•}™¥±•¹…µ”¡¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t¤4(€€€¥˜¹½Ğ…É¡¥Ù•}¹…µ”è4(€€€€€€€…É¡¥Ù•}¹…µ”€ô˜‰¥¹Ù½¥”µí¥¹Ù½¥•l¥uôˆ4(€€€Á‘™}‰åÑ•Ì€ôÉ•¹‘•É}¥¹Ù½¥•}Á‘˜¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤4(€€€…ÑÑ…¡µ•¹ÑÌ€ô•Ñ}¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥•l‰¥‰t¤4(€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€İ¥Ñ é¥Á™¥±”¹i¥Á¥±”¡‰Õ™™•È°€‰Üˆ°½µÁÉ•ÍÍ¥½¸õé¥Á™¥±”¹i%A}1Q¤…Ì…É¡¥Ù”è4(€€€€€€€…É¡¥Ù”¹İÉ¥Ñ•ÍÑÈ¡˜‰í…É¡¥Ù•}¹…µ•ô½í…É¡¥Ù•}¹…µ•ô¹Á‘˜ˆ°Á‘™}‰åÑ•Ì¤4(€€€€€€€™½È…ÑÑ…¡µ•¹Ğ¥¸…ÑÑ…¡µ•¹ÑÌè4(€€€€€€€€€€€…É¡¥Ù”¹İÉ¥Ñ”¡…ÑÑ…¡µ•¹Ñ}™¥±•}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤°…É¹…µ”õ˜‰í…É¡¥Ù•}¹…µ•ô½í…ÑÑ…¡µ•¹Ñl½É¥¥¹…±}™¥±•¹…µ”uôˆ¤4(€€€É•ÑÕÉ¸…É¡¥Ù•}¹…µ”°‰Õ™™•È¹•ÑÙ…±Õ” ¤4(4(4)‘•˜ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•µ…¥±}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥”¤è4(€€€¥˜¹½Ğ¥¹Ù½¥•l‰Í•ÉÙ¥•}½É‘•É}¥‰tè4(€€€€€€€É•ÑÕÉ¸mt4(€€€É•¥µ‰ÕÉÍ•µ•¹Ğ€ô±…Ñ•ÍÑ}ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ğ¡¥¹Ù½¥•l‰Í•ÉÙ¥•}½É‘•É}¥‰t¤4(€€€¥˜¹½ĞÉ•¥µ‰ÕÉÍ•µ•¹Ğè4(€€€€€€€É•ÑÕÉ¸mt4(€€€½É‘•È€ôÉ•ÅÕ¥É•}Í•ÉÙ¥•}½É‘•È¡¥¹Ù½¥•l‰Í•ÉÙ¥•}½É‘•É}¥‰t¤4(€€€|°…ÑÑ…¡µ•¹ÑÌ€ôÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}½ÕÑ½¥¹}…ÑÑ…¡µ•¹ÑÌ¡É•¥µ‰ÕÉÍ•µ•¹Ğ°½É‘•È¤4(€€€É•ÑÕÉ¸…ÑÑ…¡µ•¹ÑÌ4(4(4)‘•˜Õ¹¥ÅÕ•}•µ…¥±}…ÑÑ…¡µ•¹Ñ}™¥±•¹…µ”¡™¥±•¹…µ”°ÕÍ•‘}¹…µ•Ì¤è4(€€€™¥±•¹…µ”€ô½Ì¹Á…Ñ ¹‰…Í•¹…µ”¡™¥±•¹…µ”½È€ˆˆ¤¹ÍÑÉ¥À ¤½È€‰…ÑÑ…¡µ•¹Ğˆ4(€€€ÍÑ•´°•áÑ•¹Í¥½¸€ô½Ì¹Á…Ñ ¹ÍÁ±¥Ñ•áĞ¡™¥±•¹…µ”¤4(€€€…¹‘¥‘…Ñ”€ô™¥±•¹…µ”4(€€€½Õ¹Ñ•È€ô€È4(€€€İ¡¥±”…¹‘¥‘…Ñ”¹…Í•™½± ¤¥¸ÕÍ•‘}¹…µ•Ìè4(€€€€€€€…¹‘¥‘…Ñ”€ô˜‰íÍÑ•´½È€…ÑÑ…¡µ•¹Ğôµí½Õ¹Ñ•Éõí•áÑ•¹Í¥½¹ôˆ4(€€€€€€€½Õ¹Ñ•È€¬ô€Ä4(€€€ÕÍ•‘}¹…µ•Ì¹…‘¡…¹‘¥‘…Ñ”¹…Í•™½± ¤¤4(€€€É•ÑÕÉ¸…¹‘¥‘…Ñ”4(4(4)‘•˜¥¹Ù½¥•}•µ…¥±}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤è4(€€€¥¹Ù½¥•}¹…µ”€ôÍ•ÕÉ•}™¥±•¹…µ”¡¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t¤½È˜‰¥¹Ù½¥”µí¥¹Ù½¥•l¥uôˆ4(€€€…ÑÑ…¡µ•¹ÑÌ€ôl4(€€€€€€€ì4(€€€€€€€€€€€€‰™¥±•¹…µ”ˆè˜‰í¥¹Ù½¥•}¹…µ•ô¹Á‘˜ˆ°4(€€€€€€€€€€€€‰½¹Ñ•¹ĞˆèÉ•¹‘•É}¥¹Ù½¥•}Á‘˜¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤°4(€€€€€€€€€€€€‰µ…¥¹ÑåÁ”ˆè€‰…ÁÁ±¥…Ñ¥½¸ˆ°4(€€€€€€€€€€€€‰ÍÕ‰ÑåÁ”ˆè€‰Á‘˜ˆ°4(€€€€€€€ô4(€€€t4(€€€™½È…ÑÑ…¡µ•¹Ğ¥¸•Ñ}¥¹Ù½¥•}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥•l‰¥‰t¤è4(€€€€€€€Á…Ñ €ô…ÑÑ…¡µ•¹Ñ}™¥±•}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤4(€€€€€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹•á¥ÍÑÌ¡Á…Ñ ¤è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€µ…¥¹ÑåÁ”°|°ÍÕ‰ÑåÁ”€ô€¡…ÑÑ…¡µ•¹Ñl‰½¹Ñ•¹Ñ}ÑåÁ”‰t½È€‰…ÁÁ±¥…Ñ¥½¸½½Ñ•ĞµÍÑÉ•…´ˆ¤¹Á…ÉÑ¥Ñ¥½¸ ˆ¼ˆ¤4(€€€€€€€İ¥Ñ ½Á•¸¡Á…Ñ °€‰Éˆˆ¤…Ì™¥±”è4(€€€€€€€€€€€…ÑÑ…¡µ•¹ÑÌ¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€ì4(€€€€€€€€€€€€€€€€€€€€‰™¥±•¹…µ”ˆè…ÑÑ…¡µ•¹Ñl‰½É¥¥¹…±}™¥±•¹…µ”‰t°4(€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•¹Ğˆè™¥±”¹É•… ¤°4(€€€€€€€€€€€€€€€€€€€€‰µ…¥¹ÑåÁ”ˆèµ…¥¹ÑåÁ”½È€‰…ÁÁ±¥…Ñ¥½¸ˆ°4(€€€€€€€€€€€€€€€€€€€€‰ÍÕ‰ÑåÁ”ˆèÍÕ‰ÑåÁ”½È€‰½Ñ•ĞµÍÑÉ•…´ˆ°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€€¤4(€€€…ÑÑ…¡µ•¹ÑÌ¹•áÑ•¹¡ÕÍÑ½µ•É}É•¥µ‰ÕÉÍ•µ•¹Ñ}•µ…¥±}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥”¤¤4(4(€€€ÕÍ•‘}¹…µ•Ì€ôÍ•Ğ ¤4(€€€™½È…ÑÑ…¡µ•¹Ğ¥¸…ÑÑ…¡µ•¹ÑÌè4(€€€€€€€…ÑÑ…¡µ•¹Ñl‰™¥±•¹…µ”‰t€ôÕ¹¥ÅÕ•}•µ…¥±}…ÑÑ…¡µ•¹Ñ}™¥±•¹…µ”¡…ÑÑ…¡µ•¹Ñl‰™¥±•¹…µ”‰t°ÕÍ•‘}¹…µ•Ì¤4(€€€É•ÑÕÉ¸…ÑÑ…¡µ•¹ÑÌ4(4(4)‘•˜É•¹‘•É}¥¹Ù½¥•}•áÁ½ÉÑ}¡Ñµ°¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤è4(€€€É•ÑÕÉ¸É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰¥¹Ù½¥•}•áÁ½ÉĞ¹¡Ñµ°ˆ°4(€€€€€€€¥¹Ù½¥”õ¥¹Ù½¥”°4(€€€€€€€±¥•¹Ğõ±¥•¹Ğ°4(€€€€€€€Í•ÉÙ¥•}½É‘•Èõ¥¹Ù½¥•}Í•ÉÙ¥•}½É‘•È¡¥¹Ù½¥”¤°4(€€€€€€€¥Ñ•µÌõ¥Ñ•µÌ°4(€€€€€€€Ñ½Ñ…±Ìõ¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•l‰¥‰t¤°4(€€€€€€€½µÁ…¹äõ•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤°4(€€€€€€€Ñ•ÉµÌõ•Ñ}¥¹Ù½¥•}Ñ•ÉµÌ ¤°4(€€€€€€€Á…åµ•¹Ğõ•Ñ}Á…åµ•¹Ñ}¥¹ÍÑÉÕÑ¥½¹Ì ¤°4(€€€€€€€±…‰•±ÌõMQQUM}1	1L°4(€€€€¤4(4(4)‘•˜É•¹‘•É}¥¹Ù½¥•}Á‘˜¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤è4(€€€Á‘™µ•ÑÉ¥Ì¹É•¥ÍÑ•É½¹Ğ¡U¹¥½‘•%½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ¤¤4(€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€Á…”€ô…¹Ù…Ì¹…¹Ù…Ì¡‰Õ™™•È°Á…•Í¥é”õĞ¤4(€€€İ¥‘Ñ °¡•¥¡Ğ€ôĞ4(€€€±•™Ğ€ô€ĞÈ4(€€€Ñ½À€ô¡•¥¡Ğ€´€ĞÈ4(€€€±¥¹”€ô€ÄÔ4(€€€½µÁ…¹ä€ô•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤4(€€€Á…åµ•¹Ğ€ô•Ñ}Á…åµ•¹Ñ}¥¹ÍÑÉÕÑ¥½¹Ì ¤4(€€€Ñ•ÉµÌ€ô•Ñ}¥¹Ù½¥•}Ñ•ÉµÌ ¤4(€€€Ñ½Ñ…±Ì€ô¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•l‰¥‰t¤4(€€€Í•ÉÙ¥•}½É‘•È€ô¥¹Ù½¥•}Í•ÉÙ¥•}½É‘•È¡¥¹Ù½¥”¤4(4(€€€‘•˜•¹ÍÕÉ•}ÍÁ…”¡É•ÅÕ¥É•¤è4(€€€€€€€¹½¹±½…°ä4(€€€€€€€¥˜ä€´É•ÅÕ¥É•€ğ€ĞÈè4(€€€€€€€€€€€Á…”¹Í¡½İA…” ¤4(€€€€€€€€€€€ä€ô¡•¥¡Ğ€´€ĞÈ4(4(€€€‘•˜İÉ…ÁÁ•‘}±¥¹•Ì¡Ñ•áĞ°µ…á}İ¥‘Ñ °™½¹Ñ}¹…µ”ô‰MQM½¹œµ1¥¡Ğˆ°™½¹Ñ}Í¥é”ôà¤è4(€€€€€€€±¥¹•Ì€ômt4(€€€€€€€™½ÈÍ½ÕÉ•}±¥¹”¥¸Á‘™}Ñ•áĞ¡Ñ•áĞ¤¹ÍÁ±¥Ñ±¥¹•Ì ¤½Èlˆ‰tè4(€€€€€€€€€€€İ½É‘Ì€ôÍ½ÕÉ•}±¥¹”¹ÍÁ±¥Ğ ˆ€ˆ¤4(€€€€€€€€€€€ÕÉÉ•¹Ğ€ô€ˆˆ4(€€€€€€€€€€€™½Èİ½É¥¸İ½É‘Ìè4(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ôİ½É¥˜¹½ĞÕÉÉ•¹Ğ•±Í”˜‰íÕÉÉ•¹Ñôíİ½É‘ôˆ4(€€€€€€€€€€€€€€€¥˜Á‘™µ•ÑÉ¥Ì¹ÍÑÉ¥¹]¥‘Ñ ¡…¹‘¥‘…Ñ”°™½¹Ñ}¹…µ”°™½¹Ñ}Í¥é”¤€ğôµ…á}İ¥‘Ñ è4(€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ€ô…¹‘¥‘…Ñ”4(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€€€€€¥˜ÕÉÉ•¹Ğè4(€€€€€€€€€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ÕÉÉ•¹Ğ¤4(€€€€€€€€€€€€€€€¥˜Á‘™µ•ÑÉ¥Ì¹ÍÑÉ¥¹]¥‘Ñ ¡İ½É°™½¹Ñ}¹…µ”°™½¹Ñ}Í¥é”¤€ğôµ…á}İ¥‘Ñ è4(€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ€ôİ½É4(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ€ô€ˆˆ4(€€€€€€€€€€€€€€€¡Õ¹¬€ô€ˆˆ4(€€€€€€€€€€€€€€€™½È¡…È¥¸İ½Éè4(€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ô¡Õ¹¬€¬¡…È4(€€€€€€€€€€€€€€€€€€€¥˜Á‘™µ•ÑÉ¥Ì¹ÍÑÉ¥¹]¥‘Ñ ¡…¹‘¥‘…Ñ”°™½¹Ñ}¹…µ”°™½¹Ñ}Í¥é”¤€ğôµ…á}İ¥‘Ñ è4(€€€€€€€€€€€€€€€€€€€€€€€¡Õ¹¬€ô…¹‘¥‘…Ñ”4(€€€€€€€€€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€€€€€€€€€¥˜¡Õ¹¬è4(€€€€€€€€€€€€€€€€€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡¡Õ¹¬¤4(€€€€€€€€€€€€€€€€€€€€€€€¡Õ¹¬€ô¡…È4(€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ€ô¡Õ¹¬4(€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡ÕÉÉ•¹Ğ¤4(€€€€€€€É•ÑÕÉ¸m±¥¹”™½È±¥¹”¥¸±¥¹•Ì¥˜±¥¹•t4(4(€€€‘•˜‘É…İ}Í•Ñ¥½¸¡Ñ¥Ñ±”°‰½‘ä¤è4(€€€€€€€¹½¹±½…°ä4(€€€€€€€¥˜¹½ĞÁ‘™}Ñ•áĞ¡‰½‘ä¤¹ÍÑÉ¥À ¤è4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€‰½‘å}±¥¹•Ì€ôİÉ…ÁÁ•‘}±¥¹•Ì¡‰½‘ä°İ¥‘Ñ €´±•™Ğ€¨€È°™½¹Ñ}Í¥é”ôà¤4(€€€€€€€•¹ÍÕÉ•}ÍÁ…” ÄØ€¬±•¸¡‰½‘å}±¥¹•Ì¤€¨€ÄÄ¤4(€€€€€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ä¤4(€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°Ñ¥Ñ±”¤4(€€€€€€€ä€´ô€ÄÈ4(€€€€€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€à¤4(€€€€€€€Á…”¹Í•Ñ¥±±½±½È¡½±½ÉÌ¹!•á½±½È ˆŒÌĞĞÀÔĞˆ¤¤4(€€€€€€€™½È±¥¹•}Ñ•áĞ¥¸‰½‘å}±¥¹•Ìè4(€€€€€€€€€€€•¹ÍÕÉ•}ÍÁ…” ÄÄ¤4(€€€€€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°±¥¹•}Ñ•áĞ¤4(€€€€€€€€€€€ä€´ô€ÄÄ4(€€€€€€€Á…”¹Í•Ñ¥±±½±½È¡½±½ÉÌ¹‰±…¬¤4(€€€€€€€ä€´ô€Ø4(4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€Äà¤4(€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°Ñ½À°Á‘™}Ñ•áĞ¡½µÁ…¹ål‰¹…µ”‰t¤¤4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ä¤4(€€€ä€ôÑ½À€´€ÈÈ4(€€€™½ÈÁ…ÉĞ¥¸Á‘™}Ñ•áĞ¡½µÁ…¹ål‰…‘‘É•ÍÌ‰t¤¹ÍÁ±¥Ñ±¥¹•Ì ¤è4(€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°Á…ÉĞ¤4(€€€€€€€ä€´ô±¥¹”4(4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ÈØ¤4(€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ¡İ¥‘Ñ €´±•™Ğ°Ñ½À°€‰%9Y=%ˆ¤4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ÄÀ¤4(€€€µ•Ñ…}ä€ôÑ½À€´€ĞÀ4(€€€µ•Ñ…‘…Ñ„€ôl ‰%¹Ù½¥”9¼¸ˆ°¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t¥t4(€€€¥˜Í•ÉÙ¥•}½É‘•È…¹Í•ÉÙ¥•}½É‘•Él‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰tè4(€€€€€€€µ•Ñ…‘…Ñ„¹…ÁÁ•¹  ‰M•ÉÙ¥”=É‘•Èˆ°Í•ÉÙ¥•}½É‘•Él‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰t¤¤4(€€€µ•Ñ…‘…Ñ„¹•áÑ•¹ 4(€€€€€€€€ 4(€€€€€€€€€€€€ ‰%ÍÍÕ”…Ñ”ˆ°¥¹Ù½¥•l‰¥ÍÍÕ•}‘…Ñ”‰t¤°4(€€€€€€€€€€€€ ‰Õ”…Ñ”ˆ°¥¹Ù½¥•l‰‘Õ•}‘…Ñ”‰t¤°4(€€€€€€€€€€€€ ‰ÕÉÉ•¹äˆ°¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t¤°4(€€€€€€€€¤4(€€€€¤4(€€€™½È±…‰•°°Ù…±Õ”¥¸µ•Ñ…‘…Ñ„è4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ¡İ¥‘Ñ €´±•™Ğ€´€ÄÈÔ°µ•Ñ…}ä°±…‰•°¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ¡İ¥‘Ñ €´±•™Ğ°µ•Ñ…}ä°Á‘™}Ñ•áĞ¡Ù…±Õ”¤¤4(€€€€€€€µ•Ñ…}ä€´ô±¥¹”4(4(€€€ä€´ô€Äà4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ÄÄ¤4(€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°€‰	¥±°Q¼ˆ¤4(€€€ä€´ô±¥¹”4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ÄÀ¤4(€€€™½ÈÙ…±Õ”¥¸€¡±¥•¹Ñl‰¹…µ”‰t°±¥•¹Ñl‰…‘‘É•ÍÌ‰t°±¥•¹Ñl‰½Õ¹ÑÉä‰t¤è4(€€€€€€€™½ÈÁ…ÉĞ¥¸Á‘™}Ñ•áĞ¡Ù…±Õ”¤¹ÍÁ±¥Ñ±¥¹•Ì ¤è4(€€€€€€€€€€€¥˜Á…ÉĞè4(€€€€€€€€€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°Á…ÉĞ¤4(€€€€€€€€€€€€€€€ä€´ô±¥¹”4(4(€€€ä€´ô€ÄÀ4(€€€Á…”¹Í•ÑMÑÉ½­•½±½È¡½±½ÉÌ¹!•á½±½È ˆå‘•”Üˆ¤¤4(€€€Á…”¹±¥¹”¡±•™Ğ°ä°İ¥‘Ñ €´±•™Ğ°ä¤4(€€€ä€´ô€Äà4(€€€Á…”¹Í•Ñ½¹Ğ ‰MQM½¹œµ1¥¡Ğˆ°€ä¤4(€€€¡•…‘•ÉÌ€ôl ‰•ÍÉ¥ÁÑ¥½¸ˆ°±•™Ğ¤°€ ‰µ½Õ¹Ğˆ°€ÈàÔ¤°€ ‰Q…àI…Ñ”ˆ°€ÌØÀ¤°€ ‰Q…àˆ°€ĞÌÀ¤°€ ‰1¥¹”Q½Ñ…°ˆ°€ÔÀÀ¥t4(€€€™½ÈÑ•áĞ°à¥¸¡•…‘•ÉÌè4(€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡à°ä°Ñ•áĞ¤4(€€€ä€´ô€à4(€€€Á…”¹±¥¹”¡±•™Ğ°ä°İ¥‘Ñ €´±•™Ğ°ä¤4(€€€ä€´ô€ÄØ4(€€€™½È¥Ñ•´¥¸¥Ñ•µÌè4(€€€€€€€…µ½Õ¹Ğ€ô™±½…Ğ¡¥Ñ•µl‰…µ½Õ¹Ğ‰t½È€À¤4(€€€€€€€Ñ…à€ô…µ½Õ¹Ğ€¨™±½…Ğ¡¥Ñ•µl‰Ñ…á}É…Ñ”‰t½È€À¤€¼€ÄÀÀ4(€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ¡±•™Ğ°ä°Á‘™}Ñ•áĞ¡¥Ñ•µl‰‘•ÍÉ¥ÁÑ¥½¸‰t¥lèĞÙt¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ ÌĞÀ°ä°µ½¹•ä¡…µ½Õ¹Ğ°¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t¤¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ ĞÄÀ°ä°˜‰í™±½…Ğ¡¥Ñ•µlÑ…á}É…Ñ”t¤è¸É™ô”ˆ¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ ĞàÀ°ä°µ½¹•ä¡Ñ…à°¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t¤¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ¡İ¥‘Ñ €´±•™Ğ°ä°µ½¹•ä¡…µ½Õ¹Ğ€¬Ñ…à°¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t¤¤4(€€€€€€€ä€´ô±¥¹”4(4(€€€ä€´ô€ÄÈ4(€€€Á…”¹±¥¹” ÌØÀ°ä°İ¥‘Ñ €´±•™Ğ°ä¤4(€€€ä€´ô€ÄØ4(€€€™½È±…‰•°°Ù…±Õ”¥¸€  ‰MÕ‰Ñ½Ñ…°ˆ°Ñ½Ñ…±Íl‰ÍÕ‰Ñ½Ñ…°‰t¤°€ ‰Q…àˆ°Ñ½Ñ…±Íl‰Ñ…à‰t¤°€ ‰µ½Õ¹ĞÕ”ˆ°Ñ½Ñ…±Íl‰Ñ½Ñ…°‰t¤¤è4(€€€€€€€Á…”¹‘É…İMÑÉ¥¹œ ÌØÔ°ä°±…‰•°¤4(€€€€€€€Á…”¹‘É…İI¥¡ÑMÑÉ¥¹œ¡İ¥‘Ñ €´±•™Ğ°ä°µ½¹•ä¡Ù…±Õ”°¥¹Ù½¥•l‰ÕÉÉ•¹ä‰t¤¤4(€€€€€€€ä€´ô±¥¹”4(4(€€€ä€´ô€ÄÀ4(€€€‘É…İ}Í•Ñ¥½¸ ‰9½Ñ•Ìˆ°¥¹Ù½¥•l‰¹½Ñ•Ì‰t¤4(€€€‘É…İ}Í•Ñ¥½¸ ‰Q•ÉµÌˆ°Ñ•ÉµÌ¤4(€€€‘É…İ}Í•Ñ¥½¸ 4(€€€€€€€€‰A…åµ•¹Ğ%¹ÍÑÉÕÑ¥½¹Ìˆ°4(€€€€€€€€‰q¸ˆ¹©½¥¸ 4(€€€€€€€€€€€€ 4(€€€€€€€€€€€€€€€˜‰5•Ñ¡½èíÁ…åµ•¹Ñlµ•Ñ¡½uôˆ°4(€€€€€€€€€€€€€€€˜‰	•¹•™¥¥…Éä9…µ”èíÁ…åµ•¹Ñl‰•¹•™¥¥…Éäuôˆ°4(€€€€€€€€€€€€€€€˜‰	…¹¬9…µ”èíÁ…åµ•¹Ñl‰…¹­}¹…µ”uôˆ°4(€€€€€€€€€€€€€€€˜‰½Õ¹Ğ9Õµ‰•ÈèíÁ…åµ•¹Ñl…½Õ¹Ñ}¹Õµ‰•Èuôˆ°4(€€€€€€€€€€€€€€€˜‰I½ÕÑ¥¹œ9Õµ‰•ÈèíÁ…åµ•¹ÑlÉ½ÕÑ¥¹}¹Õµ‰•Èuôˆ°4(€€€€€€€€€€€€€€€˜‰M]%P½	%èíÁ…åµ•¹ÑlÍİ¥™Ñ}‰¥Œuôˆ°4(€€€€€€€€€€€€¤4(€€€€€€€€¤°4(€€€€¤4(€€€‘É…İ}Í•Ñ¥½¸ ‰Q…à9½Ñ”ˆ°½µÁ…¹ål‰Ñ…á}¹½Ñ”‰t¤4(€€€Á…”¹Í¡½İA…” ¤4(€€€Á…”¹Í…Ù” ¤4(€€€‰Õ™™•È¹Í••¬ À¤4(€€€É•ÑÕÉ¸‰Õ™™•È¹•ÑÙ…±Õ” ¤4(4(4)‘•˜‰Õ¥±‘}Í•ÉÙ¥•}É•Á½ÉÑ}‘½à¡É•Á½ÉĞ°½É‘•È¤è4(€€€‘½Õµ•¹Ğ€ô½Õµ•¹Ğ ¤4(€€€Í•Ñ¥½¸€ô‘½Õµ•¹Ğ¹Í•Ñ¥½¹ÍlÁt4(€€€Í•Ñ¥½¸¹Á…•}İ¥‘Ñ €ô%¹¡•Ì à¸Ô¤4(€€€Í•Ñ¥½¸¹Á…•}¡•¥¡Ğ€ô%¹¡•Ì ÄÄ¤4(€€€Í•Ñ¥½¸¹Ñ½Á}µ…É¥¸€ô%¹¡•Ì À¸ÔÔ¤4(€€€Í•Ñ¥½¸¹É¥¡Ñ}µ…É¥¸€ô%¹¡•Ì À¸ØÔ¤4(€€€Í•Ñ¥½¸¹‰½ÑÑ½µ}µ…É¥¸€ô%¹¡•Ì À¸Ô¤4(€€€Í•Ñ¥½¸¹±•™Ñ}µ…É¥¸€ô%¹¡•Ì À¸ØÔ¤4(4(€€€¹½Éµ…±}ÍÑå±”€ô‘½Õµ•¹Ğ¹ÍÑå±•Íl‰9½Éµ…°‰t4(€€€¹½Éµ…±}ÍÑå±”¹™½¹Ğ¹¹…µ”€ô€‰5¥É½Í½™Ğe…!•¤ˆ4(€€€¹½Éµ…±}ÍÑå±”¹}•±•µ•¹Ğ¹ÉAÈ¹É½¹ÑÌ¹Í•Ğ¡Å¸ ‰Üé•…ÍÑÍ¥„ˆ¤°€‰5¥É½Í½™Ğe…!•¤ˆ¤4(€€€¹½Éµ…±}ÍÑå±”¹™½¹Ğ¹Í¥é”€ôAĞ ä¤4(€€€¹½Éµ…±}ÍÑå±”¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ È¤4(4(€€€‘•˜‘½á}Ñ•áĞ¡Ù…±Õ”¤è4(€€€€€€€Ñ•áĞ€ô€ˆˆ¥˜Ù…±Õ”¥Ì9½¹”•±Í”ÍÑÈ¡Ù…±Õ”¤4(€€€€€€€É•ÑÕÉ¸€ˆˆ¹©½¥¸ 4(€€€€€€€€€€€¡…È4(€€€€€€€€€€€™½È¡…È¥¸Ñ•áĞ4(€€€€€€€€€€€¥˜¡…È¥¸€‰qÑq¹qÈˆ½È½É¡¡…È¤€øô€ÁàÈÀ4(€€€€€€€€¤4(4(€€€‘•˜™½Éµ…Ñ}ÉÕ¸¡ÉÕ¸°Í¥é”ôä°‰½±õ…±Í”°½±½Èõ9½¹”¤è4(€€€€€€€ÉÕ¸¹™½¹Ğ¹¹…µ”€ô€‰5¥É½Í½™Ğe…!•¤ˆ4(€€€€€€€ÉÕ¸¹}•±•µ•¹Ğ¹•Ñ}½É}…‘‘}ÉAÈ ¤¹É½¹ÑÌ¹Í•Ğ¡Å¸ ‰Üé•…ÍÑÍ¥„ˆ¤°€‰5¥É½Í½™Ğe…!•¤ˆ¤4(€€€€€€€ÉÕ¸¹™½¹Ğ¹Í¥é”€ôAĞ¡Í¥é”¤4(€€€€€€€ÉÕ¸¹‰½±€ô‰½±4(€€€€€€€¥˜½±½Èè4(€€€€€€€€€€€ÉÕ¸¹™½¹Ğ¹½±½È¹Éˆ€ôI	½±½È ©½±½È¤4(4(€€€‘•˜Í•Ñ}•±±}Í¡…‘¥¹œ¡•±°°™¥±°¤è4(€€€€€€€Ñ}ÁÈ€ô•±°¹}ÑŒ¹•Ñ}½É}…‘‘}ÑAÈ ¤4(€€€€€€€Í¡…‘¥¹œ€ôÑ}ÁÈ¹™¥¹¡Å¸ ‰ÜéÍ¡ˆ¤¤4(€€€€€€€¥˜Í¡…‘¥¹œ¥Ì9½¹”è4(€€€€€€€€€€€Í¡…‘¥¹œ€ô=áµ±±•µ•¹Ğ ‰ÜéÍ¡ˆ¤4(€€€€€€€€€€€Ñ}ÁÈ¹…ÁÁ•¹¡Í¡…‘¥¹œ¤4(€€€€€€€Í¡…‘¥¹œ¹Í•Ğ¡Å¸ ‰Üé™¥±°ˆ¤°™¥±°¤4(4(€€€‘•˜Í•Ñ}•±±}Ñ•áĞ¡•±°°Ñ•áĞ°‰½±õ…±Í”°…±¥¸õ]}1%9}AIIA ¹1P°Í¥é”ôà¸Ô¤è4(€€€€€€€•±°¹Ñ•áĞ€ô€ˆˆ4(€€€€€€€Á…É…É…Á €ô•±°¹Á…É…É…Á¡ÍlÁt4(€€€€€€€Á…É…É…Á ¹…±¥¹µ•¹Ğ€ô…±¥¸4(€€€€€€€Á…É…É…Á ¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ À¤4(€€€€€€€Á…É…É…Á ¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}‰•™½É”€ôAĞ À¤4(€€€€€€€ÉÕ¸€ôÁ…É…É…Á ¹…‘‘}ÉÕ¸¡‘½á}Ñ•áĞ¡Ñ•áĞ¤¤4(€€€€€€€™½Éµ…Ñ}ÉÕ¸¡ÉÕ¸°Í¥é”õÍ¥é”°‰½±õ‰½±¤4(€€€€€€€•±°¹Ù•ÉÑ¥…±}…±¥¹µ•¹Ğ€ô]}11}YIQ%1}1%959P¹9QH4(4(€€€‘•˜ÍÑå±•}Ñ…‰±”¡Ñ…‰±”°¡•…‘•É}É½İÌôÀ°½±Õµ¹}İ¥‘Ñ¡Ìõ9½¹”¤è4(€€€€€€€Ñ…‰±”¹ÍÑå±”€ô€‰Q…‰±”É¥ˆ4(€€€€€€€Ñ…‰±”¹…±¥¹µ•¹Ğ€ô]}Q	1}1%959P¹9QH4(€€€€€€€Ñ…‰±”¹…ÕÑ½™¥Ğ€ô…±Í”4(€€€€€€€¥˜½±Õµ¹}İ¥‘Ñ¡Ìè4(€€€€€€€€€€€™½ÈÉ½Ü¥¸Ñ…‰±”¹É½İÌè4(€€€€€€€€€€€€€€€™½È¥¹‘•à°İ¥‘Ñ ¥¸•¹Õµ•É…Ñ”¡½±Õµ¹}İ¥‘Ñ¡Ì¤è4(€€€€€€€€€€€€€€€€€€€É½Ü¹•±±Ím¥¹‘•át¹İ¥‘Ñ €ô%¹¡•Ì¡İ¥‘Ñ ¤4(€€€€€€€™½ÈÉ½İ}¥¹‘•à°É½Ü¥¸•¹Õµ•É…Ñ”¡Ñ…‰±”¹É½İÌ¤è4(€€€€€€€€€€€™½È•±°¥¸É½Ü¹•±±Ìè4(€€€€€€€€€€€€€€€•±°¹Ù•ÉÑ¥…±}…±¥¹µ•¹Ğ€ô]}11}YIQ%1}1%959P¹9QH4(€€€€€€€€€€€€€€€¥˜É½İ}¥¹‘•à€ğ¡•…‘•É}É½İÌè4(€€€€€€€€€€€€€€€€€€€Í•Ñ}•±±}Í¡…‘¥¹œ¡•±°°€‰åÌˆ¤4(€€€€€€€€€€€€€€€€€€€™½ÈÁ…É…É…Á ¥¸•±°¹Á…É…É…Á¡Ìè4(€€€€€€€€€€€€€€€€€€€€€€€™½ÈÉÕ¸¥¸Á…É…É…Á ¹ÉÕ¹Ìè4(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÉÕ¸¹‰½±€ôQÉÕ”4(4(€€€‘•˜…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±”¡Ñ¥Ñ±”¤è4(€€€€€€€Á…É…É…Á €ô‘½Õµ•¹Ğ¹…‘‘}Á…É…É…Á  ¤4(€€€€€€€Á…É…É…Á ¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}‰•™½É”€ôAĞ Ô¤4(€€€€€€€Á…É…É…Á ¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ Ì¤4(€€€€€€€ÉÕ¸€ôÁ…É…É…Á ¹…‘‘}ÉÕ¸¡Ñ¥Ñ±”¤4(€€€€€€€™½Éµ…Ñ}ÉÕ¸¡ÉÕ¸°Í¥é”ôÄÀ°‰½±õQÉÕ”°½±½Èô ÄÔ°€ÄÄà°€ÄÄÀ¤¤4(4(€€€‘•˜‘½á}Á¡½Ñ½}ÍÑÉ•…´¡Á…Ñ ¤è4(€€€€€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€€€€€İ¥Ñ %µ…”¹½Á•¸¡Á…Ñ ¤…ÌÍ½ÕÉ”è4(€€€€€€€€€€€Í½ÕÉ”¹Í••¬ À¤4(€€€€€€€€€€€¥µ…”€ô%µ…•=ÁÌ¹•á¥™}ÑÉ…¹ÍÁ½Í”¡Í½ÕÉ”¤4(€€€€€€€€€€€¥˜¥µ…”¹µ½‘”¹½Ğ¥¸ì‰Iˆ°€‰0‰ôè4(€€€€€€€€€€€€€€€‰…­É½Õ¹€ô%µ…”¹¹•Ü ‰Iˆ°¥µ…”¹Í¥é”°€‰İ¡¥Ñ”ˆ¤4(€€€€€€€€€€€€€€€¥˜€‰ˆ¥¸¥µ…”¹•Ñ‰…¹‘Ì ¤è4(€€€€€€€€€€€€€€€€€€€‰…­É½Õ¹¹Á…ÍÑ”¡¥µ…”°µ…Í¬õ¥µ…”¹•Ñ¡…¹¹•° ‰ˆ¤¤4(€€€€€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€€€€€‰…­É½Õ¹¹Á…ÍÑ”¡¥µ…”¹½¹Ù•ÉĞ ‰Iˆ¤¤4(€€€€€€€€€€€€€€€¥µ…”€ô‰…­É½Õ¹4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€¥µ…”€ô¥µ…”¹½¹Ù•ÉĞ ‰Iˆ¤4(€€€€€€€€€€€¥µ…”¹Ñ¡Õµ‰¹…¥°  äØÀ°€äØÀ¤°%µ…”¹I•Í…µÁ±¥¹œ¹19i=L¤4(€€€€€€€€€€€¥µ…”¹Í…Ù”¡‰Õ™™•È°™½Éµ…Ğô‰)Aˆ°ÅÕ…±¥ÑäôØà°½ÁÑ¥µ¥é”õQÉÕ”¤4(€€€€€€€‰Õ™™•È¹Í••¬ À¤4(€€€€€€€É•ÑÕÉ¸‰Õ™™•È4(4(€€€Ñ¥Ñ±”€ô‘½Õµ•¹Ğ¹…‘‘}Á…É…É…Á  ¤4(€€€Ñ¥Ñ±”¹…±¥¹µ•¹Ğ€ô]}1%9}AIIA ¹9QH4(€€€Ñ¥Ñ±”¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ à¤4(€€€™½Éµ…Ñ}ÉÕ¸¡Ñ¥Ñ±”¹…‘‘}ÉÕ¸ ‹:Ã–rëšr7–*‡š^—š*”ˆ¤°Í¥é”ôÄà°‰½±õQÉÕ”¤4(4(€€€™½È±…‰•°°Ù…±Õ”¥¸€ 4(€€€€€€€€ ‹®g
ç–B7Àˆ°½É‘•Él‰±¥•¹Ñ}¹…µ”‰t¤°4(€€€€€€€€ ‹’âk’âìˆ°½É‘•Él‰‰Õå•É}½İ¹•È‰t¤°4(€€€€€€€€ ‹šr7–*‡:Ã–rë–rÃ–v ˆ°½É‘•Él‰Í¥Ñ•}…‘‘É•ÍÌ‰t¤°4(€€€€€€€€ ‹šr7–*‡¢º‹–6W–>ß‚ˆ°½É‘•Él‰±¥•¹Ñ}½É‘•É}¹Õµ‰•È‰t¤°4(€€€€¤è4(€€€€€€€Á…É…É…Á €ô‘½Õµ•¹Ğ¹…‘‘}Á…É…É…Á  ¤4(€€€€€€€Á…É…É…Á ¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ È¤4(€€€€€€€™½Éµ…Ñ}ÉÕ¸¡Á…É…É…Á ¹…‘‘}ÉÕ¸¡˜‰í±…‰•±÷¾òhˆ¤°‰½±õQÉÕ”¤4(€€€€€€€™½Éµ…Ñ}ÉÕ¸¡Á…É…É…Á ¹…‘‘}ÉÕ¸¡‘½á}Ñ•áĞ¡Ù…±Õ”¤¤¤4(4(€€€İ½É­•ÉÌ€ôÍ•ÉÙ¥•}É•Á½ÉÑ}İ½É­•ÉÌ¡É•Á½ÉÑl‰¥‰t¤4(€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±” ‹:Ã–rëšr7–*‡’êë–F`ˆ¤4(€€€İ½É­•É}É½İÌ€ôµ…à¡±•¸¡İ½É­•ÉÌ¤°€È¤4(€€€İ½É­•É}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌõİ½É­•É}É½İÌ€¬€Ä°½±ÌôÈ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡İ½É­•É}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°€‹–O–B4ˆ°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡İ½É­•É}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÅt°€‹–³–>àˆ°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€™½È¥¹‘•à¥¸É…¹”¡İ½É­•É}É½İÌ¤è4(€€€€€€€İ½É­•È€ôİ½É­•ÉÍm¥¹‘•át¥˜¥¹‘•à€ğ±•¸¡İ½É­•ÉÌ¤•±Í”9½¹”4(€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡İ½É­•É}Ñ…‰±”¹É½İÍm¥¹‘•à€¬€Åt¹•±±ÍlÁt°İ½É­•Él‰¹…µ”‰t¥˜İ½É­•È•±Í”€ˆˆ¤4(€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡İ½É­•É}Ñ…‰±”¹É½İÍm¥¹‘•à€¬€Åt¹•±±ÍlÅt°•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¥l‰¹…µ”‰t¥˜İ½É­•È•±Í”€ˆˆ¤4(€€€ÍÑå±•}Ñ…‰±”¡İ½É­•É}Ñ…‰±”°¡•…‘•É}É½İÌôÄ°½±Õµ¹}İ¥‘Ñ¡ÌõlÌ¸Ô°€Ì¸Õt¤4(4(€€€ÍÕµµ…Éä€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌôÈ°½±ÌôÌ¤4(€€€±…‰•±Ì€ôl‹š*—–F+š^—šr|ˆ°€‹šï¢º‡šr7–*‡–Ş—š^Û¾ò#–Â?š^Û¾ò$ˆ°€‹’ê“¦kš^Û¦Vÿ¾ò#–Â?š^Û¾ò$‰t4(€€€Ù…±Õ•Ì€ômÉ•Á½ÉÑl‰É•Á½ÉÑ}‘…Ñ”‰t°É•Á½ÉÑl‰Ñ½Ñ…±}Í•ÉÙ¥•}¡½ÕÉÌ‰t°É•Á½ÉÑl‰ÑÉ…Ù•±}¡½ÕÉÌ‰ut4(€€€™½È¥¹‘•à°±…‰•°¥¸•¹Õµ•É…Ñ”¡±…‰•±Ì¤è4(€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡ÍÕµµ…Éä¹É½İÍlÁt¹•±±Ím¥¹‘•át°±…‰•°°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡ÍÕµµ…Éä¹É½İÍlÅt¹•±±Ím¥¹‘•át°Ù…±Õ•Ím¥¹‘•át°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€ÍÑå±•}Ñ…‰±”¡ÍÕµµ…Éä°¡•…‘•É}É½İÌôÄ°½±Õµ¹}İ¥‘Ñ¡ÌõlÈ¸ÌÔ°€È¸ÌÔ°€È¸Ít¤4(4(€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±” ‹:Ã–rëšr7–*‡š>?¢şÀˆ¤4(€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌôÈ°½±ÌôÄ¤4(€€€İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ì€ôl4(€€€€€€€˜‰íİ½É­•Él¹…µ”u÷¾òiíİ½É­•Élİ½É­}‘•ÍÉ¥ÁÑ¥½¸uôˆ4(€€€€€€€™½Èİ½É­•È¥¸İ½É­•ÉÌ¥˜İ½É­•Él‰İ½É­}‘•ÍÉ¥ÁÑ¥½¸‰t4(€€€t4(€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ€ôÉ•Á½ÉÑl‰Í•ÉÙ¥•}‘•ÍÉ¥ÁÑ¥½¸‰t½È€ˆˆ4(€€€¥˜İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ìè4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ€ô€‰q¸ˆ¹©½¥¸ ¡m‘•ÍÉ¥ÁÑ¥½¹}Ñ•áÑt¥˜‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ•±Í”mt¤€¬İ½É­•É}‘•ÍÉ¥ÁÑ¥½¹Ì¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡‘•ÍÉ¥ÁÑ¥½¹}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°‘•ÍÉ¥ÁÑ¥½¹}Ñ•áĞ¤4(€€€‘•ÍÉ¥ÁÑ¥½¹}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt¹Á…É…É…Á¡ÍlÁt¹Á…É…É…Á¡}™½Éµ…Ğ¹ÍÁ…•}…™Ñ•È€ôAĞ ÈÀ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡‘•ÍÉ¥ÁÑ¥½¹}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÁt°˜‹šrëš~sò[–>ß¾òiíÉ•Á½ÉÑl…‰¥¹•Ñ}¹Õµ‰•Èt½È€œôˆ°‰½±õQÉÕ”¤4(€€€ÍÑå±•}Ñ…‰±”¡‘•ÍÉ¥ÁÑ¥½¹}Ñ…‰±”°½±Õµ¹}İ¥‘Ñ¡ÌõlÜ¸Át¤4(4(€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±” ‹–³–Ç’ê“¦kš^Û¦Vÿ–>+¢«¦¦û¢ö›¦3¢/î¢*ˆ¤4(€€€ÑÉ…Ù•±}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌôÌ°½±ÌôÈ¤4(€€€ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt¹µ•É”¡ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÅt¤4(€€€Í•Ñ}•±±}Ñ•áĞ 4(€€€€€€€ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°4(€€€€€€€˜‹–³–Ç’ê“¦kš^Û¦Vÿ¾òiíÉ•Á½ÉÑlÁÕ‰±¥}ÑÉ…¹ÍÁ½ÉÑ}¡½ÕÉÌt½È€Áôƒ–Â?š^Ø€€€ƒ¢«¦¦û¦3¢/šï¢º‡¾òiíÉ•Á½ÉÑl‘É¥Ù¥¹}µ¥±•Ìt½È€Áôƒ¢.Ç¦0ˆ°4(€€€€€€€‰½±õQÉÕ”°4(€€€€¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÁt°˜‹–ë–>G–rÃ–v¾òiíÉ•Á½ÉÑl‘•Á…ÉÑÕÉ•}…‘‘É•ÍÌt½È€œôˆ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÅt°˜‹–rë–rÃ–rÃ–v¾òiíÉ•Á½ÉÑlÍ¥Ñ•}…‘‘É•ÍÌt½È½É‘•ÉlÍ¥Ñ•}…‘‘É•ÍÌt½È€œôˆ¤4(€€€ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÉt¹•±±ÍlÁt¹µ•É”¡ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÉt¹•±±ÍlÅt¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡ÑÉ…Ù•±}Ñ…‰±”¹É½İÍlÉt¹•±±ÍlÁt°˜‹–B#¢º‡R£š^Û¾òiíÉ•Á½ÉÑlÑ½Ñ…±}Ñ¥µ”t½È€œôˆ¤4(€€€ÍÑå±•}Ñ…‰±”¡ÑÉ…Ù•±}Ñ…‰±”°½±Õµ¹}İ¥‘Ñ¡ÌõlÌ¸Ô°€Ì¸Õt¤4(4(€€€‘•˜…‘‘}Á…ÉÑÍ}Ñ…‰±”¡Ñ¥Ñ±•}Ñ•áĞ°¡•…‘•ÉÌ°­•åÌ°É½İÌ°µ¥¹¥µÕµ}É½İÌôĞ¤è4(€€€€€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±”¡Ñ¥Ñ±•}Ñ•áĞ¤4(€€€€€€€Ñ…‰±•}É½İÌ€ô±¥ÍĞ¡É½İÌ¤4(€€€€€€€İ¡¥±”±•¸¡Ñ…‰±•}É½İÌ¤€ğµ¥¹¥µÕµ}É½İÌè4(€€€€€€€€€€€Ñ…‰±•}É½İÌ¹…ÁÁ•¹¡9½¹”¤4(€€€€€€€Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌõ±•¸¡Ñ…‰±•}É½İÌ¤€¬€È°½±Ìõ±•¸¡¡•…‘•ÉÌ¤¤4(€€€€€€€Ñ¥Ñ±•}•±°€ôÑ…‰±”¹É½İÍlÁt¹•±±ÍlÁt4(€€€€€€€™½È¥¹‘•à¥¸É…¹” Ä°±•¸¡¡•…‘•ÉÌ¤¤è4(€€€€€€€€€€€Ñ¥Ñ±•}•±°€ôÑ¥Ñ±•}•±°¹µ•É”¡Ñ…‰±”¹É½İÍlÁt¹•±±Ím¥¹‘•át¤4(€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°Ñ¥Ñ±•}Ñ•áĞ°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€€€€€Í•Ñ}•±±}Í¡…‘¥¹œ¡Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°€‰Ùİàˆ¤4(€€€€€€€™½È¥¹‘•à°¡•…‘•È¥¸•¹Õµ•É…Ñ”¡¡•…‘•ÉÌ¤è4(€€€€€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ…‰±”¹É½İÍlÅt¹•±±Ím¥¹‘•át°¡•…‘•È°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH°Í¥é”ôà¤4(€€€€€€€™½ÈÉ½İ}¥¹‘•à°Á…ÉĞ¥¸•¹Õµ•É…Ñ”¡Ñ…‰±•}É½İÌ°ÍÑ…ÉĞôÈ¤è4(€€€€€€€€€€€™½È½±Õµ¹}¥¹‘•à°­•ä¥¸•¹Õµ•É…Ñ”¡­•åÌ¤è4(€€€€€€€€€€€€€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ…‰±”¹É½İÍmÉ½İ}¥¹‘•át¹•±±Ím½±Õµ¹}¥¹‘•át°Á…ÉÑm­•åt¥˜Á…ÉĞ•±Í”€ˆˆ°…±¥¸õ]}1%9}AIIA ¹9QH°Í¥é”ôà¤4(€€€€€€€İ¥‘Ñ¡Ì€ôlÜ¸À€¼±•¸¡¡•…‘•ÉÌ¥t€¨±•¸¡¡•…‘•ÉÌ¤4(€€€€€€€ÍÑå±•}Ñ…‰±”¡Ñ…‰±”°¡•…‘•É}É½İÌôÈ°½±Õµ¹}İ¥‘Ñ¡Ìõİ¥‘Ñ¡Ì¤4(4(€€€…‘‘}Á…ÉÑÍ}Ñ…‰±” 4(€€€€€€€€‹’şw–¶cj¦7’îØˆ°4(€€€€€€€l‹¦nÛ’îÛ–>Üˆ°€‹¦nÛ’îÛ–B7Àˆ°€‹šVÃ¦<ˆ°€‹*Ûš¾ò#šZÀ¿š*—–ê¾ò$‰t°4(€€€€€€€l‰Á…ÉÑ}¹Õµ‰•Èˆ°€‰Á…ÉÑ}¹…µ”ˆ°€‰ÅÕ…¹Ñ¥Ñäˆ°€‰ÍÑ…ÑÕÌ‰t°4(€€€€€€€É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}Í…Ù•‘}Á…ÉÑÌˆ°É•Á½ÉÑl‰¥‰t¤°4(€€€€¤4(€€€…‘‘}Á…ÉÑÍ}Ñ…‰±” 4(€€€€€€€€‹:Ã–rëšnÓš6‹j¦7’îØˆ°4(€€€€€€€l‹¦nÛ’îÛ–>Üˆ°€‹¦nÛ’îÛ–B7Àˆ°€‹š^Ÿ¦7’îÛ–ê?–"_–>Üˆ°€‹šZÃ¦7’îÛ–ê?–"_–>Üˆ°€‹šVÃ¦<‰t°4(€€€€€€€l‰Á…ÉÑ}¹Õµ‰•Èˆ°€‰Á…ÉÑ}¹…µ”ˆ°€‰½±‘}Í•É¥…±}¹Õµ‰•Èˆ°€‰¹•İ}Í•É¥…±}¹Õµ‰•Èˆ°€‰ÅÕ…¹Ñ¥Ñä‰t°4(€€€€€€€É•Á½ÉÑ}Á…ÉÑÌ ‰Í•ÉÙ¥•}É•Á½ÉÑ}É•Á±…•‘}Á…ÉÑÌˆ°É•Á½ÉÑl‰¥‰t¤°4(€€€€¤4(4(€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±” ‹:Ã–rëš^Û¦^Ğˆ¤4(€€€Ñ¥µ•}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌôÈ°½±ÌôÈ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ¥µ•}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°€‹–"Ã¢úû:Ã–rëš^Û¦^Ğˆ°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ¥µ•}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÅt°€‹šï–ò:Ã–rëš^Û¦^Ğˆ°‰½±õQÉÕ”°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ¥µ•}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÁt°É•Á½ÉÑl‰…ÉÉ¥Ù…±}Ñ¥µ”‰t½È€ˆˆ°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Ñ¥µ•}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÅt°É•Á½ÉÑl‰‘•Á…ÉÑÕÉ•}Ñ¥µ”‰t½È€ˆˆ°…±¥¸õ]}1%9}AIIA ¹9QH¤4(€€€ÍÑå±•}Ñ…‰±”¡Ñ¥µ•}Ñ…‰±”°¡•…‘•É}É½İÌôÄ°½±Õµ¹}İ¥‘Ñ¡ÌõlÌ¸Ô°€Ì¸Õt¤4(4(€€€…ÑÑ…¡µ•¹Ñ}É½ÕÁÌ€ô•Ñ}É•Á½ÉÑ}…ÑÑ…¡µ•¹ÑÌ¡É•Á½ÉÑl‰¥‰t¤4(€€€Á¡½Ñ½}±…‰•±Ì€ôì4(€€€€€€€€‰…ÉÉ¥Ù…°ˆè€‹–"Ã¢úû:Ã–rëš^Û¦^ÓŸ&ˆ°4(€€€€€€€€‰‘•Á…ÉÑÕÉ”ˆè€‹šï–ò:Ã–rëš^Û¦^ÓŸ&ˆ°4(€€€€€€€€‰Í•±™}¡•¬ˆè€‹¢«šŸ&ˆ°4(€€€€€€€€‰Í¥Ñ”ˆè€‹:Ã–rëšr7–*‡Ÿ&ˆ°4(€€€ô4(€€€™½È…Ñ•½Éä°Í•Ñ¥½¹}Ñ¥Ñ±”¥¸Á¡½Ñ½}±…‰•±Ì¹¥Ñ•µÌ ¤è4(€€€€€€€Á¡½Ñ½Ì€ô…ÑÑ…¡µ•¹Ñ}É½ÕÁÌ¹•Ğ¡…Ñ•½Éä°mt¤4(€€€€€€€¥˜¹½ĞÁ¡½Ñ½Ìè4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€…‘‘}Í•Ñ¥½¹}Ñ¥Ñ±”¡Í•Ñ¥½¹}Ñ¥Ñ±”¤4(€€€€€€€Á¡½Ñ½}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌô¡±•¸¡Á¡½Ñ½Ì¤€¬€Ä¤€¼¼€È°½±ÌôÈ¤4(€€€€€€€Á¡½Ñ½}Ñ…‰±”¹…±¥¹µ•¹Ğ€ô]}Q	1}1%959P¹9QH4(€€€€€€€Á¡½Ñ½}Ñ…‰±”¹…ÕÑ½™¥Ğ€ô…±Í”4(€€€€€€€™½È¥¹‘•à°…ÑÑ…¡µ•¹Ğ¥¸•¹Õµ•É…Ñ”¡Á¡½Ñ½Ì¤è4(€€€€€€€€€€€•±°€ôÁ¡½Ñ½}Ñ…‰±”¹É½İÍm¥¹‘•à€¼¼€Ét¹•±±Ím¥¹‘•à€”€Ét4(€€€€€€€€€€€Á…Ñ €ôÉ•Á½ÉÑ}…ÑÑ…¡µ•¹Ñ}Á…Ñ ¡…ÑÑ…¡µ•¹Ğ¤4(€€€€€€€€€€€¥˜¹½Ğ½Ì¹Á…Ñ ¹•á¥ÍÑÌ¡Á…Ñ ¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€Á…É…É…Á €ô•±°¹Á…É…É…Á¡ÍlÁt4(€€€€€€€€€€€Á…É…É…Á ¹…±¥¹µ•¹Ğ€ô]}1%9}AIIA ¹9QH4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€ÉÕ¸€ôÁ…É…É…Á ¹…‘‘}ÉÕ¸ ¤4(€€€€€€€€€€€€€€€ÉÕ¸¹…‘‘}Á¥ÑÕÉ”¡‘½á}Á¡½Ñ½}ÍÑÉ•…´¡Á…Ñ ¤°İ¥‘Ñ õ%¹¡•Ì Ì¸ÄÔ¤¤4(€€€€€€€€€€€•á•ÁĞá•ÁÑ¥½¸è4(€€€€€€€€€€€€€€€™½Éµ…Ñ}ÉÕ¸¡Á…É…É…Á ¹…‘‘}ÉÕ¸ ‹–nû&š^ƒšÎW–Ö3–”ˆ¤°Í¥é”ôà¤4(4(€€€‘½Õµ•¹Ğ¹…‘‘}Á…É…É…Á  ¤4(€€€Í¥¹}Ñ…‰±”€ô‘½Õµ•¹Ğ¹…‘‘}Ñ…‰±”¡É½İÌôÈ°½±ÌôÈ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Í¥¹}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÁt°€‹š*—–F+’êë¶û–¶_¾òhˆ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Í¥¹}Ñ…‰±”¹É½İÍlÁt¹•±±ÍlÅt°€‹:Ã–rëšr7–*‡’êë–Fc¾òhˆ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Í¥¹}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÁt°˜‹š*—–F+š^Û¦^Ó¾òiíÉ•Á½ÉÑlÉ•Á½ÉÑ}‘…Ñ”t½È€œôˆ¤4(€€€Í•Ñ}•±±}Ñ•áĞ¡Í¥¹}Ñ…‰±”¹É½İÍlÅt¹•±±ÍlÅt°˜‹–³–>ã¾òií•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¥l¹…µ”uôˆ¤4(€€€ÍÑå±•}Ñ…‰±”¡Í¥¹}Ñ…‰±”°½±Õµ¹}İ¥‘Ñ¡ÌõlÌ¸Ô°€Ì¸Õt¤4(4(€€€‰Õ™™•È€ô	åÑ•Í%< ¤4(€€€‘½Õµ•¹Ğ¹Í…Ù”¡‰Õ™™•È¤4(€€€É•ÑÕÉ¸‰Õ™™•È¹•ÑÙ…±Õ” ¤4(4(4)‘•˜Í•¹‘}¥¹Ù½¥•}•µ…¥°¡¥¹Ù½¥•}¥¤è4(€€€¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ€ô±½…‘}¥¹Ù½¥”¡¥¹Ù½¥•}¥¤4(€€€¥˜¹½Ğ±¥•¹Ñl‰•µ…¥°‰tè4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‹–º‹š"ßšÊ‡šr'¦
»ºÇ¾ò3š^ƒšÎW–>G¦ˆ¤4(€€€µ…¥±}…ÑÑ…¡µ•¹ÑÌ€ô¥¹Ù½¥•}•µ…¥±}…ÑÑ…¡µ•¹ÑÌ¡¥¹Ù½¥”°±¥•¹Ğ°¥Ñ•µÌ¤4(€€€¡Ñµ°€ôÉ•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€‰•µ…¥±}¥¹Ù½¥”¹¡Ñµ°ˆ°4(€€€€€€€¥¹Ù½¥”õ¥¹Ù½¥”°4(€€€€€€€±¥•¹Ğõ±¥•¹Ğ°4(€€€€€€€Í•ÉÙ¥•}½É‘•Èõ¥¹Ù½¥•}Í•ÉÙ¥•}½É‘•È¡¥¹Ù½¥”¤°4(€€€€€€€¥Ñ•µÌõ¥Ñ•µÌ°4(€€€€€€€Ñ½Ñ…±Ìõ¥¹Ù½¥•}Ñ½Ñ…±Ì¡¥¹Ù½¥•}¥¤°4(€€€€€€€½µÁ…¹äõ•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¤°4(€€€€€€€Ñ•ÉµÌõ•Ñ}¥¹Ù½¥•}Ñ•ÉµÌ ¤°4(€€€€€€€Á…åµ•¹Ğõ•Ñ}Á…åµ•¹Ñ}¥¹ÍÑÉÕÑ¥½¹Ì ¤°4(€€€€¤4(€€€ÍÕ‰©•Ğ€ô˜‰%¹Ù½¥”í¥¹Ù½¥•l¥¹Ù½¥•}¹Õµ‰•Èuô™É½´í•Ñ}½µÁ…¹å}ÁÉ½™¥±” ¥l¹…µ”uôˆ4(€€€Í•¹‘}•µ…¥° 4(€€€€€€€Ñ¼õ±¥•¹Ñl‰•µ…¥°‰t°4(€€€€€€€ÍÕ‰©•ĞõÍÕ‰©•Ğ°4(€€€€€€€¡Ñµ°õ¡Ñµ°°4(€€€€€€€…ÑÑ…¡µ•¹ÑÌõµ…¥±}…ÑÑ…¡µ•¹ÑÌ°4(€€€€¤4(€€€É•½É‘}•µ…¥±}‘•±¥Ù•Éä ‰¥¹Ù½¥”ˆ°¥¹Ù½¥•}¥°±¥•¹Ñl‰•µ…¥°‰t°ÍÕ‰©•Ğ¤4(€€€±½}…Ñ¥½¸ 4(€€€€€€€€‰Í•¹ˆ°4(€€€€€€€€‰¥¹Ù½¥”ˆ°4(€€€€€€€¥¹Ù½¥•}¥°4(€€€€€€€¥¹Ù½¥•l‰¥¹Ù½¥•}¹Õµ‰•È‰t°4(€€€€€€€˜‹–>G¦¢Ï¾òií±¥•¹Ñl•µ…¥°u÷¾òo¦f’îÛ¾òií±•¸¡µ…¥±}…ÑÑ…¡µ•¹ÑÌ¥ôƒ’â¨ˆ°4(€€€€¤4(€€€‘ˆ ¤¹•á•ÕÑ” ‰ÕÁ‘…Ñ”¥¹Ù½¥•ÌÍ•ĞÍ•¹Ñ}…Ğ€ô€üİ¡•É”¥€ô€üˆ°€¡¹½Ü ¤°¥¹Ù½¥•}¥¤¤4(€€€‘ˆ ¤¹½µµ¥Ğ ¤4(4(4)‘•˜Í•¹‘}•µ…¥°¡Ñ¼°ÍÕ‰©•Ğ°¡Ñµ°°…ÑÑ…¡µ•¹ÑÌõ9½¹”¤è4(€€€ÍµÑÁ}Í•ÑÑ¥¹Ì€ô•Ñ}ÍµÑÁ}Í•ÑÑ¥¹Ì ¤4(€€€¡½ÍĞ€ôÍµÑÁ}Í•ÑÑ¥¹Íl‰¡½ÍĞ‰t¹ÍÑÉ¥À ¤4(€€€¥˜¹½Ğ¡½ÍĞè4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‹–³–>ã¢ºûö»’â·jM5Q@!½ÍĞƒšr«¦7ö»ˆ¤4(€€€ÕÍ•È€ôÍµÑÁ}Í•ÑÑ¥¹Íl‰ÕÍ•È‰t¹ÍÑÉ¥À ¤4(€€€Á…ÍÍİ½É€ôÍµÑÁ}Í•ÑÑ¥¹Íl‰Á…ÍÍİ½É‰t4(€€€¥˜ÕÍ•È…¹¹½ĞÁ…ÍÍİ½Éè4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‹–³–>ã¢ºûö»’â·jM5Q@A…ÍÍİ½Éƒšr«¦7ö»	µ…¥°ƒ¦r¢š’öÿR£–êSR£’âOR£–¾‚¾ò3’â7šb¿šf»¦kfï–öW–¾‚ˆ¤4(€€€µ•ÍÍ…”€ôµ…¥±5•ÍÍ…” ¤4(€€€µ•ÍÍ…•l‰É½´‰t€ôÍµÑÁ}Í•ÑÑ¥¹Íl‰™É½´‰t½ÈÕÍ•È½È€‰‰¥±±¥¹•á…µÁ±”¹½´ˆ4(€€€µ•ÍÍ…•l‰Q¼‰t€ôÑ¼4(€€€µ•ÍÍ…•l‰MÕ‰©•Ğ‰t€ôÍÕ‰©•Ğ4(€€€µ•ÍÍ…”¹Í•Ñ}½¹Ñ•¹Ğ ‰A±•…Í”Í•”Ñ¡”…ÑÑ…¡•‘½Õµ•¹ÑÌ¸ˆ¤4(€€€µ•ÍÍ…”¹…‘‘}…±Ñ•É¹…Ñ¥Ù”¡¡Ñµ°°ÍÕ‰ÑåÁ”ô‰¡Ñµ°ˆ¤4(€€€™½È…ÑÑ…¡µ•¹Ğ¥¸…ÑÑ…¡µ•¹ÑÌ½Èmtè4(€€€€€€€µ•ÍÍ…”¹…‘‘}…ÑÑ…¡µ•¹Ğ 4(€€€€€€€€€€€…ÑÑ…¡µ•¹Ñl‰½¹Ñ•¹Ğ‰t°4(€€€€€€€€€€€µ…¥¹ÑåÁ”õ…ÑÑ…¡µ•¹Ñl‰µ…¥¹ÑåÁ”‰t°4(€€€€€€€€€€€ÍÕ‰ÑåÁ”õ…ÑÑ…¡µ•¹Ñl‰ÍÕ‰ÑåÁ”‰t°4(€€€€€€€€€€€™¥±•¹…µ”õ…ÑÑ…¡µ•¹Ñl‰™¥±•¹…µ”‰t°4(€€€€€€€€¤4(€€€Á½ÉĞ€ô¥¹Ğ¡ÍµÑÁ}Í•ÑÑ¥¹Íl‰Á½ÉĞ‰t½È€ˆÔàÜˆ¤4(€€€ÕÍ•}Ñ±Ì€ôÍµÑÁ}Í•ÑÑ¥¹Íl‰Ñ±Ì‰t¹±½İ•È ¤€ôô€‰ÑÉÕ”ˆ4(€€€ÑÉäè4(€€€€€€€İ¥Ñ ÍµÑÁ±¥ˆ¹M5Q@¡¡½ÍĞ°Á½ÉĞ°Ñ¥µ•½ÕĞôÌÀ¤…ÌÍµÑÀè4(€€€€€€€€€€€¥˜ÕÍ•}Ñ±Ìè4(€€€€€€€€€€€€€€€ÍµÑÀ¹ÍÑ…ÉÑÑ±Ì ¤4(€€€€€€€€€€€¥˜ÕÍ•Èè4(€€€€€€€€€€€€€€€ÍµÑÀ¹±½¥¸¡ÕÍ•È°Á…ÍÍİ½É¤4(€€€€€€€€€€€ÍµÑÀ¹Í•¹‘}µ•ÍÍ…”¡µ•ÍÍ…”¤4(€€€•á•ÁĞÍµÑÁ±¥ˆ¹M5QAÕÑ¡•¹Ñ¥…Ñ¥½¹ÉÉ½È…Ì•ÉÉ½Èè4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰µ…¥°ƒfï–öW–’Ç¢Ò—¢¾ß†»¢º“–³–>ã¢ºûö»’â·jM5Q@A…ÍÍİ½Éƒšb¼µ…¥°ƒ–êSR£’âOR£–¾‚¾ò3–æÛ’âS¢Ò›–>ß–ŞË–ò–B¿’â“š¶—¦ª3¢¾ˆ¤™É½´•ÉÉ½È4(€€€•á•ÁĞ€¡ÍµÑÁ±¥ˆ¹M5QAá•ÁÑ¥½¸°=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•ÉÉ½Èè4(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡˜‹¦
»’îÛ–>G¦–’Ç¢Ò—¾òií•ÉÉ½Éôˆ¤™É½´•ÉÉ½È4(4(4)…ÁÀ¹•ÉÉ½É¡…¹‘±•È¡IÕ¹Ñ¥µ•ÉÉ½È¤4)‘•˜ÉÕ¹Ñ¥µ•}•ÉÉ½È¡•ÉÉ½È¤è4(€€€™±…Í ¡ÍÑÈ¡•ÉÉ½È¤°€‰•ÉÉ½Èˆ¤4(€€€É•ÑÕÉ¸É•‘¥É•Ğ¡É•ÅÕ•ÍĞ¹É•™•ÉÉ•È½ÈÕÉ±}™½È ‰‘…Í¡‰½…Éˆ¤¤4(4(4)…ÁÀ¹•ÉÉ½É¡…¹‘±•È ĞÀÌ¤4)‘•˜™½É‰¥‘‘•¸¡•ÉÉ½È¤è4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€µ•ÍÍ…”€ô€‹’öƒj–’[¦£¢Ò›–>ß–>«¢÷š~—r/’â;¢«–ŞÇš&–Æ{–º‹š"ßnã–Ïj–Ş—–6W–J3–Ş—’ösš^—š*—¾ò3¢şg–òƒ–Ş—–6W’â7–r£’öƒj–>¿¢¢2–nÓ––š¦r¢ºÿ¦^»¾ò3¢¾ß¢SÎï–¦£º‡B–Fc–ò¦kšv¦fCˆ4(€€€•±Í”è4(€€€€€€€µ•ÍÍ…”€ô€‹’öƒšÊ‡šr'šv¦fC¢ºÿ¦^»¢şg’â«¦†×¦v‹š"[¢ºÃ–öW–š¦r¢ºÿ¦^»¾ò3¢¾ß¢SÎïº‡B–Fcˆ4(€€€É•ÑÕÉ¸€ 4(€€€€€€€É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€€€€€‰•ÉÉ½È¹¡Ñµ°ˆ°4(€€€€€€€€€€€ÍÑ…ÑÕÍ}½‘”ôˆĞÀÌˆ°4(€€€€€€€€€€€Ñ¥Ñ±”ô‹šÊ‡šr'¢ºÿ¦^»šv¦f@ˆ°4(€€€€€€€€€€€µ•ÍÍ…”õµ•ÍÍ…”°4(€€€€€€€€¤°4(€€€€€€€€ĞÀÌ°4(€€€€¤4(4(4)…ÁÀ¹•ÉÉ½É¡…¹‘±•È ĞÀĞ¤4)‘•˜¹½Ñ}™½Õ¹¡•ÉÉ½È¤è4(€€€É•ÑÕÉ¸€ 4(€€€€€€€É•¹‘•É}Ñ•µÁ±…Ñ” 4(€€€€€€€€€€€€‰•ÉÉ½È¹¡Ñµ°ˆ°4(€€€€€€€€€€€ÍÑ…ÑÕÍ}½‘”ôˆĞÀĞˆ°4(€€€€€€€€€€€Ñ¥Ñ±”ô‹šÊ‡šr'š&û–"Ã¢şg’â«¦†×¦v‹š"[¢ºÃ–öTˆ°4(€€€€€€€€€€€µ•ÍÍ…”ô‹’öƒ¢úO–—j–rÃ–v–>¿¢÷’â7š¶†»¾ò3š"[¢¢şg–òƒ–>G–£¦f’îÛ–º‹š"ß¢ÖšZg–ŞËî?¢Š¯–"ƒ¦f“ˆ°4(€€€€€€€€¤°4(€€€€€€€€ĞÀĞ°4(€€€€¤4(4(4)‘•˜•Ñ}µ•ÑÉ¥Ì ¤è4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜‰Í•±•Ğ¥°ÕÉÉ•¹ä°Á…¥‘}…Ğ°Á…åµ•¹Ñ}…µ½Õ¹Ğ™É½´¥¹Ù½¥•Ìİ¡•É”ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹í…•ÍÍ}±…ÕÍ•ôˆ°4(€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€½µÁ±•Ñ•€ôÁ…¥€ôÕ¹Á…¥€ô¥¹Ù½¥•}½Õ¹Ğ€ô€À4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€Ñ½Ñ…°€ô¥¹Ù½¥•}Ñ½Ñ…±Ì¡É½İl‰¥‰t¥l‰Ñ½Ñ…°‰t4(€€€€€€€¥¹Ù½¥•}½Õ¹Ğ€¬ô€Ä4(€€€€€€€½µÁ±•Ñ•€¬ôÑ½Ñ…°4(€€€€€€€¥˜É½İl‰Á…¥‘}…Ğ‰tè4(€€€€€€€€€€€Á…¥€¬ô™±½…Ğ¡É½İl‰Á…åµ•¹Ñ}…µ½Õ¹Ğ‰t½ÈÑ½Ñ…°¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€Õ¹Á…¥€¬ôÑ½Ñ…°4(€€€•áÁ•¹Í•}É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•ĞÍÑ…ÑÕÌ°…µ½Õ¹Ğ4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€İ¡•É”ÍÑ…ÑÕÌ¥¸€ …ÁÁÉ½Ù•œ°€ÍÕ‰µ¥ÑÑ•œ°€É•ÑÕÉ¹•œ¤4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤4(€€€Á•¹‘¥¹}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ½…±•Í”¡ÍÕ´¡…µ½Õ¹Ğ¤°€À¤…ÌÑ½Ñ…°4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€İ¡•É”ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ…¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€„ô€Á…¥œ4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡½¹” ¥l‰Ñ½Ñ…°‰t4(€€€É•¥µ‰ÕÉÍ•‘}•áÁ•¹Í•Ì€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ½…±•Í”¡ÍÕ´¡…µ½Õ¹Ğ¤°€À¤…ÌÑ½Ñ…°4(€€€€€€€™É½´•áÁ•¹Í•Ì4(€€€€€€€İ¡•É”ÍÑ…ÑÕÌ€ô€…ÁÁÉ½Ù•œ…¹Á…å½ÕÑ}ÍÑ…ÑÕÌ€ô€Á…¥œ4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡½¹” ¥l‰Ñ½Ñ…°‰t4(€€€Á•¹‘¥¹}•áÁ•¹Í•Ì€ôÍÕ´¡™±½…Ğ¡É½İl‰…µ½Õ¹Ğ‰t½È€À¤™½ÈÉ½Ü¥¸•áÁ•¹Í•}É½İÌ¥˜É½İl‰ÍÑ…ÑÕÌ‰t€„ô€‰…ÁÁÉ½Ù•ˆ¤4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€Á•¹‘¥¹}É•¥µ‰ÕÉÍ•µ•¹Ğ€ô€À4(€€€€€€€É•¥µ‰ÕÉÍ•‘}•áÁ•¹Í•Ì€ô€À4(€€€€€€€Á•¹‘¥¹}•áÁ•¹Í•Ì€ô€À4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰¥¹Ù½¥•}½Õ¹Ğˆè¥¹Ù½¥•}½Õ¹Ğ°4(€€€€€€€€‰½µÁ±•Ñ•ˆè½µÁ±•Ñ•°4(€€€€€€€€‰Á…¥ˆèÁ…¥°4(€€€€€€€€‰Õ¹Á…¥ˆèÕ¹Á…¥°4(€€€€€€€€‰Ñ½Ñ…°ˆè½µÁ±•Ñ•°4(€€€€€€€€‰Á•¹‘¥¹}É•¥µ‰ÕÉÍ•µ•¹Ğˆè™±½…Ğ¡Á•¹‘¥¹}É•¥µ‰ÕÉÍ•µ•¹Ğ½È€À¤°4(€€€€€€€€‰É•¥µ‰ÕÉÍ•‘}•áÁ•¹Í•Ìˆè™±½…Ğ¡É•¥µ‰ÕÉÍ•‘}•áÁ•¹Í•Ì½È€À¤°4(€€€€€€€€‰Á•¹‘¥¹}•áÁ•¹Í•ÌˆèÁ•¹‘¥¹}•áÁ•¹Í•Ì°4(€€€ô4(4(4)‘•˜µ½¹Ñ¡±å}¡…ÉĞ ¤è4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜‰Í•±•Ğ¥°¥ÍÍÕ•}‘…Ñ”™É½´¥¹Ù½¥•Ìİ¡•É”ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹í…•ÍÍ}±…ÕÍ•ô½É‘•È‰ä¥ÍÍÕ•}‘…Ñ”…ÍŒˆ°4(€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€‰Õ­•ÑÌ€ôíô4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€µ½¹Ñ €ôÉ½İl‰¥ÍÍÕ•}‘…Ñ”‰ulèİt4(€€€€€€€‰Õ­•ÑÍmµ½¹Ñ¡t€ô‰Õ­•ÑÌ¹•Ğ¡µ½¹Ñ °€À¤€¬¥¹Ù½¥•}Ñ½Ñ…±Ì¡É½İl‰¥‰t¥l‰Ñ½Ñ…°‰t4(€€€¥˜¹½Ğ‰Õ­•ÑÌè4(€€€€€€€É•ÑÕÉ¸mt4(€€€µ…á}Ù…±Õ”€ôµ…à¡‰Õ­•ÑÌ¹Ù…±Õ•Ì ¤¤½È€Ä4(€€€É•ÑÕÉ¸mì‰µ½¹Ñ ˆèµ½¹Ñ °€‰Ù…±Õ”ˆèÙ…±Õ”°€‰¡•¥¡ĞˆèÉ½Õ¹¡Ù…±Õ”€¼µ…á}Ù…±Õ”€¨€ÄÀÀ¥ô™½Èµ½¹Ñ °Ù…±Õ”¥¸‰Õ­•ÑÌ¹¥Ñ•µÌ ¥t4(4(4)‘•˜…Ù…¥±…‰±•}ÁÉ½©•ÑÍ}™½É}…•ÍÌ ¤è4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•Ğ‘¥ÍÑ¥¹ĞÁÉ½©•ÑÌ¹¥°ÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€™É½´ÁÉ½©•ÑÌ4(€€€€€€€©½¥¸¥¹Ù½¥•}¥Ñ•µÌ½¸¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥€ôÁÉ½©•ÑÌ¹¥4(€€€€€€€©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥4(€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹í…•ÍÍ}±…ÕÍ•ô4(€€€€€€€½É‘•È‰äÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€€ˆˆˆ°4(€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€¥˜É½İÌè4(€€€€€€€É•ÑÕÉ¸É½İÌ4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€‰Í•±•Ğ¥°¹…µ”™É½´ÁÉ½©•ÑÌİ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€¥¹Ù½¥”œ…¹¥Í}…Ñ¥Ù”€ô€Ä½É‘•È‰ä¹…µ”ˆ4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)‘•˜‘…Í¡‰½…É‘}ÁÉ½©•Ñ}½ÁÑ¥½¹Ì ¤è4(€€€É•ÑÕÉ¸l4(€€€€€€€ì‰¥ˆèÉ½İl‰¥‰t°€‰¹…µ”ˆèÉ½İl‰¹…µ”‰t°€‰½±½ÈˆèÁÉ½©•Ñ}½±½È¡É½İl‰¥‰t¥ô4(€€€€€€€™½ÈÉ½Ü¥¸…Ù…¥±…‰±•}ÁÉ½©•ÑÍ}™½É}…•ÍÌ ¤4(€€€t4(4(4)‘•˜É•Á½ÉÑ}ÁÉ½©•Ñ}½ÁÑ¥½¹Ì ¤è4(€€€¥˜¥Í}•áÑ•É¹…±}ÕÍ•È ¤è4(€€€€€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€€€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€€€€˜ˆˆˆ4(€€€€€€€€€€€Í•±•Ğ‘¥ÍÑ¥¹ĞÁÉ½©•ÑÌ¹¥°ÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€€€€€™É½´ÁÉ½©•ÑÌ4(€€€€€€€€€€€©½¥¸¥¹Ù½¥•}¥Ñ•µÌ½¸¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥€ôÁÉ½©•ÑÌ¹¥4(€€€€€€€€€€€©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥4(€€€€€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ…¹í…•ÍÍ}±…ÕÍ•ô4(€€€€€€€€€€€½É‘•È‰äÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€€€€€€ˆˆˆ°4(€€€€€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€€€€€¤¹™•Ñ¡…±° ¤4(€€€É•ÑÕÉ¸‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€€ˆˆˆ4(€€€€€€€Í•±•Ğ¥°¹…µ”™É½´ÁÉ½©•ÑÌİ¡•É”ÁÉ½©•Ñ}ÑåÁ”€ô€¥¹Ù½¥”œ…¹¥Í}…Ñ¥Ù”€ô€Ä4(€€€€€€€Õ¹¥½¸4(€€€€€€€Í•±•Ğ‘¥ÍÑ¥¹ĞÁÉ½©•ÑÌ¹¥°ÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€™É½´ÁÉ½©•ÑÌ4(€€€€€€€©½¥¸¥¹Ù½¥•}¥Ñ•µÌ½¸¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥€ôÁÉ½©•ÑÌ¹¥4(€€€€€€€©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥4(€€€€€€€İ¡•É”¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€„ô€Ù½¥œ4(€€€€€€€½É‘•È‰ä¹…µ”4(€€€€€€€€ˆˆˆ4(€€€€¤¹™•Ñ¡…±° ¤4(4(4)‘•˜µ½¹Ñ¡±å}ÁÉ½©•Ñ}¡…ÉĞ¡Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ì¤è4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€±…ÕÍ•Ì€ôl‰¥¹Ù½¥•Ì¹ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œˆ°…•ÍÍ}±…ÕÍ•t4(€€€Á…É…µÌ€ô±¥ÍĞ¡…•ÍÍ}Á…É…µÌ¤4(€€€¥˜Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ìè4(€€€€€€€Á±…•¡½±‘•ÉÌ€ô€ˆ°ˆ¹©½¥¸ ˆüˆ™½È|¥¸Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ì¤4(€€€€€€€±…ÕÍ•Ì¹…ÁÁ•¹¡˜‰¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥¥¸€¡íÁ±…•¡½±‘•ÉÍô¤ˆ¤4(€€€€€€€Á…É…µÌ¹•áÑ•¹¡Í•±•Ñ•‘}ÁÉ½©•Ñ}¥‘Ì¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜ˆˆˆ4(€€€€€€€Í•±•ĞÍÕ‰ÍÑÈ¡¥¹Ù½¥•Ì¹¥ÍÍÕ•}‘…Ñ”°€Ä°€Ü¤…Ìµ½¹Ñ °ÁÉ½©•ÑÌ¹¥…ÌÁÉ½©•Ñ}¥°4(€€€€€€€€€€€€€€ÁÉ½©•ÑÌ¹¹…µ”…ÌÁÉ½©•Ñ}¹…µ”°ÍÕ´¡¥¹Ù½¥•}¥Ñ•µÌ¹…µ½Õ¹Ğ€¬¥¹Ù½¥•}¥Ñ•µÌ¹…µ½Õ¹Ğ€¨¥¹Ù½¥•}¥Ñ•µÌ¹Ñ…á}É…Ñ”€¼€ÄÀÀ¤…ÌÑ½Ñ…°4(€€€€€€€™É½´¥¹Ù½¥•}¥Ñ•µÌ4(€€€€€€€©½¥¸¥¹Ù½¥•Ì½¸¥¹Ù½¥•Ì¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹¥¹Ù½¥•}¥4(€€€€€€€©½¥¸ÁÉ½©•ÑÌ½¸ÁÉ½©•ÑÌ¹¥€ô¥¹Ù½¥•}¥Ñ•µÌ¹ÁÉ½©•Ñ}¥4(€€€€€€€İ¡•É”ìˆ…¹€ˆ¹©½¥¸¡±…ÕÍ•Ì¥ô4(€€€€€€€É½ÕÀ‰äµ½¹Ñ °ÁÉ½©•ÑÌ¹¥°ÁÉ½©•ÑÌ¹¹…µ”4(€€€€€€€½É‘•È‰äµ½¹Ñ …ÍŒ°ÁÉ½©•ÑÌ¹¹…µ”…ÍŒ4(€€€€€€€€ˆˆˆ°4(€€€€€€€Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€‰Õ­•ÑÌ€ôíô4(€€€ÁÉ½©•Ñ}¹…µ•Ì€ôíô4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€µ½¹Ñ €ôÉ½İl‰µ½¹Ñ ‰t4(€€€€€€€…µ½Õ¹Ğ€ô™±½…Ğ¡É½İl‰Ñ½Ñ…°‰t½È€À¤4(€€€€€€€ÁÉ½©•Ñ}¥€ôÉ½İl‰ÁÉ½©•Ñ}¥‰t4(€€€€€€€ÁÉ½©•Ñ}¹…µ•ÍmÁÉ½©•Ñ}¥‘t€ôÉ½İl‰ÁÉ½©•Ñ}¹…µ”‰t4(€€€€€€€‰Õ­•ÑÌ¹Í•Ñ‘•™…Õ±Ğ¡µ½¹Ñ °ì‰µ½¹Ñ ˆèµ½¹Ñ °€‰Ñ½Ñ…°ˆè€À°€‰Í•µ•¹ÑÌˆèmuô¤4(€€€€€€€‰Õ­•ÑÍmµ½¹Ñ¡ul‰Ñ½Ñ…°‰t€¬ô…µ½Õ¹Ğ4(€€€€€€€‰Õ­•ÑÍmµ½¹Ñ¡ul‰Í•µ•¹ÑÌ‰t¹…ÁÁ•¹ 4(€€€€€€€€€€€ì‰ÁÉ½©•Ñ}¥ˆèÁÉ½©•Ñ}¥°€‰¹…µ”ˆèÉ½İl‰ÁÉ½©•Ñ}¹…µ”‰t°€‰Ù…±Õ”ˆè…µ½Õ¹Ğ°€‰½±½ÈˆèÁÉ½©•Ñ}½±½È¡ÁÉ½©•Ñ}¥¥ô4(€€€€€€€€¤4(€€€¥˜¹½Ğ‰Õ­•ÑÌè4(€€€€€€€É•ÑÕÉ¸mt4(€€€µ…á}Ñ½Ñ…°€ôµ…à¡‰Õ­•Ñl‰Ñ½Ñ…°‰t™½È‰Õ­•Ğ¥¸‰Õ­•ÑÌ¹Ù…±Õ•Ì ¤¤½È€Ä4(€€€™½È‰Õ­•Ğ¥¸‰Õ­•ÑÌ¹Ù…±Õ•Ì ¤è4(€€€€€€€‰Õ­•Ñl‰¡•¥¡Ğ‰t€ôÉ½Õ¹¡‰Õ­•Ñl‰Ñ½Ñ…°‰t€¼µ…á}Ñ½Ñ…°€¨€ÄÀÀ¤4(€€€€€€€™½ÈÍ•µ•¹Ğ¥¸‰Õ­•Ñl‰Í•µ•¹ÑÌ‰tè4(€€€€€€€€€€€Í•µ•¹Ñl‰¡•¥¡Ğ‰t€ôÉ½Õ¹¡Í•µ•¹Ñl‰Ù…±Õ”‰t€¼‰Õ­•Ñl‰Ñ½Ñ…°‰t€¨€ÄÀÀ¤¥˜‰Õ­•Ñl‰Ñ½Ñ…°‰t•±Í”€À4(€€€É•ÑÕÉ¸±¥ÍĞ¡‰Õ­•ÑÌ¹Ù…±Õ•Ì ¤¤4(4(4)‘•˜µ½¹Ñ¡±å}Á…¥‘}¡…ÉĞ ¤è4(€€€…•ÍÍ}±…ÕÍ”°…•ÍÍ}Á…É…µÌ€ô±¥•¹Ñ}™¥±Ñ•É}±…ÕÍ” ‰¥¹Ù½¥•Ìˆ¤4(€€€É½İÌ€ô‘ˆ ¤¹•á•ÕÑ” 4(€€€€€€€˜‰Í•±•Ğ¥°Á…¥‘}…Ğ°Á…åµ•¹Ñ}…µ½Õ¹Ğ™É½´¥¹Ù½¥•Ìİ¡•É”ÍÑ…ÑÕÌ€ô€½µÁ±•Ñ•œ…¹Á…¥‘}…Ğ¥Ì¹½Ğ¹Õ±°…¹í…•ÍÍ}±…ÕÍ•ô½É‘•È‰äÁ…¥‘}…Ğ…ÍŒˆ°4(€€€€€€€…•ÍÍ}Á…É…µÌ°4(€€€€¤¹™•Ñ¡…±° ¤4(€€€‰Õ­•ÑÌ€ôíô4(€€€™½ÈÉ½Ü¥¸É½İÌè4(€€€€€€€µ½¹Ñ €ôÉ½İl‰Á…¥‘}…Ğ‰ulèİt4(€€€€€€€…µ½Õ¹Ğ€ô™±½…Ğ¡É½İl‰Á…åµ•¹Ñ}…µ½Õ¹Ğ‰t½È¥¹Ù½¥•}Ñ½Ñ…±Ì¡É½İl‰¥‰t¥l‰Ñ½Ñ…°‰t¤4(€€€€€€€‰Õ­•ÑÍmµ½¹Ñ¡t€ô‰Õ­•ÑÌ¹•Ğ¡µ½¹Ñ °€À¤€¬…µ½Õ¹Ğ4(€€€¥˜¹½Ğ‰Õ­•ÑÌè4(€€€€€€€É•ÑÕÉ¸mt4(€€€µ…á}Ù…±Õ”€ôµ…à¡‰Õ­•ÑÌ¹Ù…±Õ•Ì ¤¤½È€Ä4(€€€É•ÑÕÉ¸mì‰µ½¹Ñ ˆèµ½¹Ñ °€‰Ù…±Õ”ˆèÙ…±Õ”°€‰¡•¥¡ĞˆèÉ½Õ¹¡Ù…±Õ”€¼µ…á}Ù…±Õ”€¨€ÄÀÀ¥ô™½Èµ½¹Ñ °Ù…±Õ”¥¸‰Õ­•ÑÌ¹¥Ñ•µÌ ¥t4(4(4)É•¥ÍÑ•É}™¥•±‘}É½ÕÑ•Ì¡…ÁÀ°±½‰…±Ì ¤¤4)É•¥ÍÑ•É}ÍÑ…™™}É•Á½ÉÑÌ¡…ÁÀ°±½‰…±Ì ¤¤4)É•¥ÍÑ•É}É…Ñ•}É½ÕÑ•Ì¡…ÁÀ°±½‰…±Ì ¤¤4)É•¥ÍÑ•É}Í•ÑÑ±•µ•¹Ñ}É•Ù¥•İ}É½ÕÑ•Ì¡…ÁÀ°±½‰…±Ì ¤¤4)É•¥ÍÑ•É}ÁÉ½™¥Ñ…‰¥±¥Ñå}É½ÕÑ•Ì¡…ÁÀ°±½‰…±Ì ¤¤4(4(4)¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè4(€€€Ù•É¥™å}‘…Ñ…}‘¥É•Ñ½Éå}¥‘•¹Ñ¥Ñä ¤4(€€€¥¹¥Ñ}‘ˆ ¤4(€€€…ÁÀ¹ÉÕ¸¡¡½ÍĞôˆÀ¸À¸À¸Àˆ°Á½ÉĞôàÀÀÀ°‘•‰ÕœõQÉÕ”¤4)•±Í”è4(€€€Ù•É¥™å}‘…Ñ…}‘¥É•Ñ½Éå}¥‘•¹Ñ¥Ñä ¤4(€€€¥¹¥Ñ}‘ˆ ¤(