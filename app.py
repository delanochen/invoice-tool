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
from database import (
    DatabaseError,
    IntegrityError,
    PostgreSQLConnection,
    verify_postgres_schema,
    lock_number_allocation,
)
import tempfile
import threading
import time
import zipfile
import calendar
from invoice_tool.service_orders import calendar_map_services as _calendar_map_services
from invoice_tool.service_orders.calendar_map_routes import register_calendar_map_routes
from invoice_tool.service_reports import report_read_services as _report_read_services
from invoice_tool.service_reports.report_read_routes import register_report_read_routes
from invoice_tool.service_reports import storage as _report_storage
from invoice_tool.service_reports import forms as _report_forms
from invoice_tool.service_reports import calculations as _report_calculations
from invoice_tool.employee_expenses import expense_write_services as _expense_write_services
from invoice_tool.employee_expenses.expense_write_routes import register_expense_write_routes
from invoice_tool.employee_expenses.expense_review_routes import register_expense_review_routes
from invoice_tool.employee_expenses.expense_attachment_routes import register_expense_attachment_routes
from invoice_tool.employee_expenses.expense_attachment_delete_routes import register_expense_attachment_delete_routes
from invoice_tool.employee_expenses.expense_ai_review_routes import register_expense_ai_review_routes
from invoice_tool.employee_expenses.expense_transfer_routes import register_expense_transfer_routes
from invoice_tool.employee_expenses import expense_approval_services as _expense_approval_services
from invoice_tool.employee_expenses.expense_approval_routes import register_expense_approval_routes
from invoice_tool.employee_expenses import expense_cancel_services as _expense_cancel_services
from invoice_tool.employee_expenses.expense_cancel_routes import register_expense_cancel_routes
from invoice_tool.employee_expenses.expense_delete_routes import register_expense_delete_routes
from invoice_tool.employee_expenses.expense_query_routes import register_expense_query_routes
from invoice_tool.employee_expenses.expense_reset_routes import register_expense_reset_routes
from invoice_tool.invoices import forms as _invoice_forms
from invoice_tool.invoices import tokens as _invoice_tokens
from invoice_tool.invoices import numbering as _invoice_numbering
from invoice_tool.invoices import storage as _invoice_storage
from invoice_tool.invoices import attachment_names as _invoice_attachment_names
from invoice_tool.invoices import copying as _invoice_copying
from invoice_tool.invoices import exports as _invoice_exports
from invoice_tool.invoices import email_attachments as _invoice_email_attachments
from invoice_tool.invoices import pdf as _invoice_pdf
from invoice_tool.invoices.attachment_read_routes import register_invoice_attachment_read_routes
from invoice_tool.invoices.attachment_write_routes import register_invoice_attachment_write_routes
from invoice_tool.invoices.export_routes import register_invoice_export_routes
from invoice_tool.invoices.detail_routes import register_invoice_detail_routes
from invoice_tool.invoices.send_routes import register_invoice_send_routes
from invoice_tool.invoices.admin_status_routes import register_invoice_admin_status_routes
from invoice_tool.invoices.payment_routes import register_invoice_payment_routes
from invoice_tool.invoices.void_routes import register_invoice_void_routes
from invoice_tool.invoices.delete_routes import register_invoice_delete_routes
from invoice_tool.invoices import access as _split_invoice_access
from invoice_tool.invoices import totals as _split_invoice_totals
from invoice_tool.invoices import recognition as _split_invoice_recognition
from invoice_tool.invoices import uploads as _split_invoice_uploads
from invoice_tool.invoices import loading as _split_invoice_loading
from invoice_tool.invoices import create as _split_invoice_create
from invoice_tool.invoices import update as _split_invoice_update
from invoice_tool.invoices import mail as _split_invoice_mail
from invoice_tool.shared_attachments import named as _split_named_attachment
from invoice_tool.shared_attachments import company as _split_company_attachments
from invoice_tool.email_delivery import record as _split_delivery_record
from invoice_tool.email_delivery import summary as _split_delivery_summary
from invoice_tool.shared_attachments import images as _split_image_detection
from invoice_tool.report_exports import columns as _split_xlsx_columns
from invoice_tool.report_exports import single_sheet as _split_xlsx_single
from invoice_tool.report_exports import multi_sheet as _split_xlsx_multi
from invoice_tool.report_exports import dates as _split_iso_date
from invoice_tool.report_exports import labor as _split_labor_entries
from invoice_tool.email_delivery import transport as _split_smtp_transport
from invoice_tool.dashboard import metrics as _split_dashboard_metrics
from invoice_tool.dashboard import charts as _split_dashboard_monthly
from invoice_tool.dashboard import projects as _split_dashboard_projects
from invoice_tool.app_settings import storage as _split_setting_write
from invoice_tool.app_settings import numbers as _split_setting_float
from invoice_tool.app_settings import payroll as _split_subsidy_settings
from invoice_tool.app_settings import timezone as _split_timezone
from invoice_tool.app_settings import geography as _split_headquarters
from invoice_tool.user_preferences import forms as _split_communication
from invoice_tool.user_preferences import phone as _split_phone
from invoice_tool.report_exports import location as _split_report_location
from invoice_tool.customer_reimbursements import access as _split_settlement_access
from invoice_tool.customer_reimbursements import rates as _split_contract_rates
from invoice_tool.customer_reimbursements import holidays as _split_holidays
from invoice_tool.customer_reimbursements import calculations as _split_row_field
from invoice_tool.customer_reimbursements import project_mapping as _split_project_mapping
from invoice_tool.customer_reimbursements import dates as _split_settlement_date
from invoice_tool.customer_reimbursements import totals as _split_mro_total
from invoice_tool.customer_reimbursements import item_queries as _split_settlement_items
from invoice_tool.mobile_distribution import routes as _split_mobile_install
from invoice_tool.user_preferences import routes as _split_language_session
from invoice_tool.messages import routes as _split_messages_list
from invoice_tool.mobile_distribution import pwa_routes as _split_pwa_manifest
from invoice_tool.dashboard import page_routes as _split_workspace_pages
from invoice_tool.company_profile import routes as _split_company_page
from invoice_tool.query_pages import invoices as _split_invoice_list
from invoice_tool.query_pages import orders as _split_order_query_page
from invoice_tool.query_pages import reports as _split_report_query_page
from invoice_tool.ai_daily_report_web import draft_routes as _split_draft_get
from invoice_tool.ai_daily_report_web import photo_routes as _split_photos_discover
from invoice_tool.ai_daily_report_web import lifecycle_routes as _split_draft_cancel
from invoice_tool.ai_daily_report_web import access_routes as _split_draft_csrf
from invoice_tool.ai_daily_report_web import list_routes as _split_draft_list
from invoice_tool.customer_reimbursements import management_access as _split_access
from invoice_tool.customer_reimbursements import quotation_rows as _split_quotation_rows
from invoice_tool.customer_reimbursements import source_queries as _split_source_queries
from invoice_tool.customer_reimbursements import source_merge as _split_source_merge
from invoice_tool.customer_reimbursements import workspace as _split_workspace
from invoice_tool.customer_reimbursements import attachment_files as _split_attachment_files
from invoice_tool.customer_reimbursements import item_forms as _split_item_forms
from invoice_tool.customer_reimbursements import invoice_items as _split_invoice_items
from invoice_tool.customer_reimbursements import updates as _split_updates
from invoice_tool.customer_reimbursements import documents as _split_documents
from invoice_tool.authentication import routes as _split_authentication
from invoice_tool.app_settings import admin_routes as _split_system_settings
from invoice_tool.ai_assistant import web_routes as _split_assistant
from invoice_tool.ai_daily_report_web import confirmation_routes as _split_ai_confirmation
from invoice_tool.ai_daily_report_web import validation_routes as _split_ai_validation
from invoice_tool.ai_daily_report_web import evidence_routes as _split_ai_evidence
from invoice_tool.ai_daily_report_web import worker_routes as _split_ai_workers
from invoice_tool.ai_daily_report_web import work_routes as _split_ai_work
from invoice_tool.query_pages import settlement_routes as _split_report_queries
from invoice_tool.service_orders import attachment_routes as _split_order_attachments
from invoice_tool.customer_reimbursements import form_routes as _split_settlement_form
from invoice_tool.customer_reimbursements import action_routes as _split_settlement_actions
from invoice_tool.customer_reimbursements import attachment_routes as _split_settlement_attachments
from invoice_tool.employee_expenses import forms as _expense_forms
from invoice_tool.employee_expenses import storage as _expense_storage
from invoice_tool.employee_expenses import duplicates as _expense_duplicates
from invoice_tool.employee_expenses import access as _expense_access
from invoice_tool.service_reports import documents as _report_documents
from invoice_tool.service_reports import report_write_services as _report_write_services
from invoice_tool.service_reports.report_write_routes import register_report_write_routes
from invoice_tool.service_reports.assist_routes import register_report_assist_routes
from invoice_tool.photos import services as _photo_capture_services
from invoice_tool.photos.routes import register_photo_capture_routes
from invoice_tool.service_orders import services as _service_order_services
from invoice_tool.service_orders.routes import register_service_order_routes
from invoice_tool.contracts import services as _contract_services
from invoice_tool.contracts.routes import register_contract_routes
from invoice_tool.payment_terms import calculations as _payment_term_calculations
from invoice_tool.payment_terms.services import build_payment_term_services
from invoice_tool.payment_terms.routes import register_payment_term_routes
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
from image_processing import compress_image
from invoice_tool.knowledge import KnowledgeService, register_knowledge_routes
from invoice_tool.catalog import (
    MRO_CANONICAL_PROJECT_NAME,
    build_catalog_services,
    is_mro_alias_project_name,
    is_mro_project_name,
    normalized_project_name,
    project_name_key,
    register_catalog_routes,
)
from invoice_tool.customers import build_customer_services, register_customer_routes
from invoice_tool.customers.services import localized_state_name, state_names_for_language
from invoice_tool.employees import (
    build_user_services,
    register_employee_grade_routes,
    register_employee_user_routes,
)
from invoice_tool.payroll import (
    build_payroll_correction_services,
    build_payroll_services,
    register_payroll_routes,
)
from invoice_tool.quotations.routes import register_quotation_routes
from invoice_tool.order_employee_settlements import (
    employee_expense_block_reason,
    order_employee_settlement_rows,
    register_order_employee_settlement_routes,
)
from invoice_tool.commercial_documents import (
    document_config,
    document_rates,
    ensure_commercial_document,
    sync_order_commercial_document,
)
from invoice_tool.commercial_billing import (
    canonical_commercial_rate_key,
    driving_rate_missing,
    driving_transport_amount,
    normalize_driving_mode,
    normalize_settlement_mode,
    quote_amounts,
    resolve_quote_settlement,
)
from invoice_tool.accounting import (
    InvoiceRecognitionService,
    InvoiceVoidError,
    PostingError,
)
from invoice_tool.accounting.routes import register_accounting_routes
from field_work import register_field_routes
from staff_reports import register_staff_reports
from customer_report_logic import (
    migrate_historical_customer_reports,
    normalize_customer_report_choice,
)
from rate_engine import (
    contract_rate,
    employee_grade_rate_snapshot,
    employee_rate,
    register_rate_routes,
    EMPLOYEE_RATE_LABELS,
    EMPLOYEE_RATE_TYPES,
)
from settlement_review import (
    linked_approved_expense_rows,
    register_settlement_review_routes,
    settlement_expense_candidates,
    selected_expense_count,
    selected_expense_item_ids,
    selected_expense_total,
)
from profitability import register_profitability_routes
from order_data_transfer import register_order_data_transfer_routes
from employee_finance import cancel_expense_payment_order, ensure_expense_payment_order, register_employee_finance_routes
from expense_smart_fill import register_expense_smart_fill_routes
from trip_policy import DEFAULT_TRIP_TYPE, ROUND_TRIP, ONE_WAY, TRIP_TYPES, normalize_trip_type, trip_label, trip_multiplier
from service_report_assist import ServiceReportAssistService, ServiceReportEvidenceService
import llm_config
from ai_interpretation import (
    MODEL_OPTIONS,
    SETTINGS_DEFAULTS as AI_INTERPRET_DEFAULTS,
    effective_settings as ai_interpret_effective_settings,
    interpret_attachment as run_expense_attachment_interpretation,
)
from ai_review import run_expense_ai_review as run_expense_ai_review_task
from ai_daily_report import (
    AIIntentService,
    DailyReportService,
    WorkOrderContextService,
    EmployeeResolutionService,
    TravelService,
    reconcile_travel_verification_fields,
    GoogleRoutesService,
    GoogleStaticMapsService,
    MileageService,
    MileageEvidenceService,
    PhotoDiscoveryService,
    PhotoMetadataService,
    generate_action_id,
    is_phase1_implemented,
    ACTION_VERSION,
    DraftVersionConflict,
    DraftStateError,
)
from ai_daily_report.schemas import WorkerTravel
from airbnb_receipt_tool import register_airbnb_receipt_routes
from travel_tools import register_travel_tools_routes
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
DATA_DIR = os.environ.get("INVOICE_DATA_DIR", os.path.join(BASE_DIR, "data"))
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


def _new_formal_save_service(user_id):
    """构造 FormalSaveService（AI 日报正式保存）。

    照片原图在 shared-photos、草稿里程佐证在 DATA_DIR/ai-daily-report-drafts，
    两个根目录都要传进去 —— 缺了它们，「不完整也传到工单」时就找不到该带过去的附件。
    这里在调用时才拼路径（DATA_DIR 可被测试/运行期改掉），不要固化成模块级常量。
    """
    return FormalSaveService(
        db(),
        DATA_DIR,
        REPORT_ATTACHMENTS_DIR,
        user_id,
        shared_photos_dir=SHARED_PHOTOS_DIR,
        draft_evidence_root=os.path.join(DATA_DIR, "ai-daily-report-drafts"),
    )

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
    "arrival": "现场到达时间照片",
    "departure": "离开现场时间照片",
    "self_check": "自检照片",
    "site": "现场服务照片",
    "mileage_proof": "里程佐证",
}

register_heif_opener()
ALLOWED_ATTACHMENT_LABEL = "Word、Excel、PDF、PNG、JPG、JPEG、WEBP、GIF"

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
GOOGLE_PLACES_API_KEY_ENV = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
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
    "draft": "保存未提交",
    "submitted": "待经理审核",
    "returned": "已退回",
    "completed": "已完成",
    "void": "作废",
}

EXPENSE_STATUS_LABELS = {
    "draft": "保存未提交",
    "submitted": "待经理审核",
    "returned": "已退回",
    "approved": "已通过",
    "cancelled": "已作废",
}

EXPENSE_PAYOUT_LABELS = {
    "pending": "待付款",
    "paid": "已付款",
}

CUSTOMER_REIMBURSEMENT_STATUS_LABELS = {
    "draft": "保存未提交",
    "submitted": "待经理审核",
    "returned": "已退回",
    "approved": "已通过",
}

ROLE_OPTIONS = {
    "admin": "管理员",
    "manager": "经理",
    "finance": "财务",
    "employee": "员工",
    "external_manager": "外部管理员",
    "external_employee": "外部员工",
}

MENU_PERMISSION_GROUPS = [
    {
        "label": "主菜单",
        "items": [
            {"key": "dashboard", "label": "概览", "roles": {"admin", "manager", "finance"}},
            {"key": "contracts", "label": "合同", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "quotations", "label": "报价单", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "service_orders", "label": "工单", "roles": set(ROLE_OPTIONS)},
            {"key": "field_work", "label": "现场工作", "roles": set(ROLE_OPTIONS)},
            {"key": "service_order_map", "label": "站点地图", "roles": {"admin", "manager", "finance", "employee", "external_manager"}},
            {"key": "service_order_calendar", "label": "工单日历", "roles": set(ROLE_OPTIONS)},
            {"key": "expense_processing", "label": "报销处理", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "new_invoice", "label": "新建发票", "roles": {"manager", "finance"}},
        ],
    },
    {
        "label": "报表",
        "items": [
            {"key": "invoices", "label": "发票查询", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "invoice_query", "label": "发票明细查询", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "customer_reimbursement_query", "label": "工单结算报表", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "profitability", "label": "项目利润", "roles": {"admin", "manager", "finance"}},
            {"key": "buyer_query", "label": "站点查询", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "service_order_query", "label": "工单查询", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "service_report_query", "label": "日报查询", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "expense_query", "label": "报销明细查询", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "user_certificate_query", "label": "员工证书查询", "roles": set(ROLE_OPTIONS)},
            {"key": "audit_log_report", "label": "操作日志", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "薪酬管理",
        "items": [
            {"key": "labor_hours_report", "label": "工时统计", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "payroll_report", "label": "薪酬统计", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "payroll_calendar", "label": "薪酬日历", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "employee_grades", "label": "员工等级", "roles": {"admin", "manager", "finance"}},
            {"key": "payroll_subsidies", "label": "补贴参数", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "财务管理",
        "items": [
            {"key": "finance_overview", "label": "财务总览", "roles": {"admin", "manager", "finance"}},
            {"key": "employee_payments", "label": "员工付款中心", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "bank_accounts", "label": "银行账户", "roles": {"admin", "finance"}},
            {"key": "bank_transactions", "label": "银行流水", "roles": {"admin", "finance"}},
            {"key": "employee_advances", "label": "员工借款", "roles": {"admin", "manager", "finance"}},
            {"key": "employee_ledger", "label": "员工往来账", "roles": {"admin", "manager", "finance"}},
            {"key": "payment_batches", "label": "付款批次", "roles": {"admin", "manager", "finance"}},
            {"key": "tax_review", "label": "税务复核", "roles": {"admin", "manager", "finance"}},
            {"key": "annual_tax_summary", "label": "年度税务汇总", "roles": {"admin", "manager", "finance"}},
            {"key": "bank_reconciliation", "label": "银行对账", "roles": {"admin", "finance"}},
            {"key": "accounting_vouchers", "label": "会计凭证", "roles": {"admin", "manager", "finance"}},
            {"key": "accounting_receipts", "label": "客户收款", "roles": {"admin", "manager", "finance"}},
            {"key": "accounting_periods", "label": "会计期间", "roles": {"admin", "finance"}},
            {"key": "accounting_opening", "label": "期初建账", "roles": {"admin", "finance"}},
            {"key": "accounting_reports", "label": "会计报表", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "资产管理",
        "items": [
            {"key": "assets", "label": "资产档案", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "基础数据",
        "items": [
            {"key": "clients", "label": "客户", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "owners", "label": "业主", "roles": {"admin", "manager", "finance"}},
            {"key": "manufacturers", "label": "厂家", "roles": {"admin", "manager", "finance"}},
            {"key": "buyers", "label": "站点", "roles": {"admin", "manager", "finance", "external_manager"}},
            {"key": "work_order_types", "label": "工单类型", "roles": {"admin", "manager"}},
            {"key": "projects", "label": "项目", "roles": {"admin", "manager"}},
            {"key": "countries", "label": "国家", "roles": {"admin", "manager"}},
            {"key": "company_info", "label": "公司信息", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "users", "label": "用户/我的资料", "roles": set(ROLE_OPTIONS)},
        ],
    },
    {
        "label": "实用工具",
        "items": [
            {"key": "ai_daily_report", "label": "AI 日报", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "travel_tools", "label": "出行工具", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "knowledge_base", "label": "知识库", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "ai_assistant", "label": "智能助手", "roles": {"admin", "manager", "finance", "employee"}},
            {"key": "database_console", "label": "数据库工具", "roles": {"admin"}},
            {"key": "order_data_transfer", "label": "工单数据转移", "roles": {"admin"}},
            {"key": "airbnb_receipt", "label": "Airbnb 收据", "roles": {"admin", "manager", "finance"}},
        ],
    },
    {
        "label": "系统配置",
        "items": [
            {"key": "payment_terms", "label": "账期管理", "roles": {"admin", "finance"}},
            {"key": "system_settings", "label": "系统设置", "roles": {"admin"}},
            # 消息在 UI 上已从「主菜单」下拉剥离、成为菜单栏上紧跟系统配置的一级入口，
            # 这里的分组只是权限配置页的分类，跟着 UI 位置走，免得两边对不上。
            # 它的默认角色仍是全部角色（不是管理项，只是位置在系统配置后面）。
            {"key": "messages", "label": "消息", "roles": set(ROLE_OPTIONS)},
        ],
    },
]

DEFAULT_MENU_ROLES = {
    item["key"]: set(item["roles"])
    for group in MENU_PERMISSION_GROUPS
    for item in group["items"]
}
# 经理是财务权限的超集：今后新增任何默认财务菜单时，无需再手工重复补经理。
for _roles in DEFAULT_MENU_ROLES.values():
    if "finance" in _roles:
        _roles.add("manager")

ACTION_LABELS = {
    "view": "查看",
    "create": "新增",
    "edit": "编辑",
    "delete": "删除",
    "approve": "审核",
    "reset": "修改状态",
    "export": "导出",
    "send": "发送",
    "pay": "核销",
    "execute": "执行",
    "post": "过账",
    "ai_review": "AI 智能审核",
    "reconcile": "对账",
}

ROLE_ACTION_PERMISSION_GROUPS = [
    {
        "label": "主业务",
        "items": [
            {"key": "contracts", "label": "合同", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "quotations", "label": "报价单", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}, "export": {"admin", "manager", "finance", "external_manager"}}},
            {"key": "service_orders", "label": "工单", "actions": {"view": set(ROLE_OPTIONS), "create": {"manager", "finance", "employee"}, "edit": {"admin", "manager", "finance", "employee", "external_manager", "external_employee"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "service_order_calendar", "label": "工单日历", "actions": {"view": set(ROLE_OPTIONS)}},
            {"key": "service_reports", "label": "工作日报", "actions": {"view": set(ROLE_OPTIONS), "create": {"admin", "manager", "finance", "employee", "external_employee"}, "edit": {"admin", "manager", "finance", "employee", "external_employee"}, "delete": {"admin", "manager"}, "export": {"admin", "manager", "finance", "employee", "external_employee", "external_manager"}}},
            {"key": "invoices", "label": "发票", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}, "export": {"admin", "manager", "finance", "external_manager"}, "send": {"manager", "finance"}, "pay": {"admin", "manager", "finance"}}},
            {"key": "expenses", "label": "员工报销", "actions": {"view": {"admin", "manager", "finance", "employee"}, "create": {"manager", "finance", "employee"}, "edit": {"admin", "manager", "finance", "employee"}, "delete": {"admin", "manager", "finance", "employee"}, "approve": {"admin", "manager", "finance"}, "ai_review": {"admin", "manager", "finance"}}},
            {"key": "customer_reimbursements", "label": "工单结算", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}, "approve": {"admin", "manager"}, "reset": {"admin", "manager", "finance"}, "export": {"admin", "manager", "finance", "external_manager"}, "send": {"manager", "finance"}}},
            {"key": "profitability", "label": "项目利润", "actions": {"view": {"admin", "manager", "finance"}}},
            {"key": "knowledge_base", "label": "知识库", "actions": {"view": {"admin", "manager", "finance", "employee"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager"}}},
            {"key": "ai_assistant", "label": "智能助手", "actions": {"view": {"admin", "manager", "finance", "employee"}}},
        ],
    },
    {
        "label": "基础数据",
        "items": [
            {"key": "clients", "label": "客户", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "owners", "label": "业主", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "manufacturers", "label": "厂家", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "buyers", "label": "站点", "actions": {"view": {"admin", "manager", "finance", "external_manager"}, "create": {"admin", "manager", "finance", "external_manager"}, "edit": {"admin", "manager", "finance", "external_manager"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "work_order_types", "label": "工单类型", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}, "delete": {"admin", "manager"}}},
            {"key": "projects", "label": "项目", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}, "delete": {"admin", "manager"}}},
            {"key": "countries", "label": "国家", "actions": {"view": {"admin", "manager"}, "create": {"admin", "manager"}, "edit": {"admin", "manager"}}},
            {"key": "users", "label": "用户", "actions": {"view": set(ROLE_OPTIONS), "create": {"admin", "external_manager"}, "edit": {"admin", "external_manager"}, "delete": {"admin"}, "approve": {"admin", "manager"}}},
        ],
    },
    {
        "label": "薪酬与系统",
        "items": [
            {"key": "labor_hours_report", "label": "工时统计", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "payroll_report", "label": "薪酬统计", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "payroll_calendar", "label": "薪酬日历", "actions": {"view": {"admin", "manager", "finance", "employee"}, "export": {"admin", "manager", "finance"}}},
            {"key": "employee_grades", "label": "员工等级", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin", "manager", "finance"}}},
            {"key": "payroll_subsidies", "label": "补贴参数", "actions": {"view": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}}},
            {"key": "company_info", "label": "公司信息", "actions": {"view": {"admin", "manager", "finance", "employee"}, "edit": {"admin"}}},
            {"key": "system_settings", "label": "系统设置", "actions": {"view": {"admin"}, "edit": {"admin"}}},
            {"key": "database_console", "label": "数据库工具", "actions": {"view": {"admin"}, "execute": {"admin"}}},
            {"key": "order_data_transfer", "label": "工单数据转移", "actions": {"view": {"admin"}, "execute": {"admin"}}},
            {"key": "payment_terms", "label": "账期管理", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "audit_logs", "label": "操作日志", "actions": {"view": {"admin", "manager", "finance"}}},
        ],
    },
    {
        "label": "财务与资产",
        "items": [
            {"key": "finance_overview", "label": "财务总览", "actions": {"view": {"admin", "manager", "finance"}}},
            {"key": "employee_payments", "label": "员工付款单", "actions": {"view": {"admin", "manager", "finance", "employee"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "approve": {"admin", "manager", "finance"}, "pay": {"admin", "finance"}, "reconcile": {"admin", "finance"}, "email_statement": {"admin", "finance", "manager"}}},
            {"key": "employee_advances", "label": "员工借款", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "bank_accounts", "label": "银行账户", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "bank_transactions", "label": "银行流水", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "employee_ledger", "label": "员工往来账", "actions": {"view": {"admin", "manager", "finance"}}},
            # Phase 4A：税务复核。「看」给财务/经理/管理员，「改分类」只给财务与管理员
            # —— 分类调整会改变年度汇总口径，不能让只读角色误点。
            {"key": "tax_review", "label": "税务复核", "actions": {"view": {"admin", "manager", "finance"}, "review": {"admin", "finance"}}},
            # Phase 4B：年度税务汇总。看 = 财务 / 经理 / 管理员；导出只给财务与管理员。
            # 普通员工一律不给（要看自己的年度汇总以后单独做 self-only 权限）。
            {"key": "annual_tax_summary", "label": "年度税务汇总", "actions": {"view": {"admin", "manager", "finance"}, "export": {"admin", "finance"}}},
            {"key": "bank_reconciliation", "label": "银行对账", "actions": {"view": {"admin", "finance"}, "reconcile": {"admin", "finance"}}},
            {"key": "accounting_vouchers", "label": "会计凭证", "actions": {"view": {"admin", "manager", "finance"}}},
            {"key": "accounting_receipts", "label": "客户收款", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "finance"}, "edit": {"admin", "finance"}}},
            {"key": "accounting_corrections", "label": "发票会计更正", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}}},
            {"key": "accounting_periods", "label": "会计期间", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "close": {"admin", "finance"}, "reopen": {"admin"}}},
            {"key": "accounting_opening", "label": "期初建账", "actions": {"view": {"admin", "finance"}, "create": {"admin", "finance"}, "post": {"admin", "finance"}}},
            {"key": "accounting_reports", "label": "会计报表", "actions": {"view": {"admin", "manager", "finance"}, "export": {"admin", "finance"}}},
            {"key": "assets", "label": "资产档案", "actions": {"view": {"admin", "manager", "finance"}, "create": {"admin", "manager", "finance"}, "edit": {"admin", "manager", "finance"}, "delete": {"admin"}}},
        ],
    },
]

DEFAULT_ACTION_ROLES = {
    (item["key"], action): set(roles)
    for group in ROLE_ACTION_PERMISSION_GROUPS
    for item in group["items"]
    for action, roles in item["actions"].items()
}
MANAGER_FINANCE_INHERITANCE_EXCEPTIONS = {
    # Tax workspaces are intentionally manager-read-only. Review and export
    # remain restricted to finance/admin even though managers generally inherit
    # finance permissions elsewhere.
    ("tax_review", "review"),
    ("annual_tax_summary", "export"),
}
# 操作权限同样保持 manager >= finance，避免新增财务动作时遗漏经理。
for _permission_key, _roles in DEFAULT_ACTION_ROLES.items():
    if "finance" in _roles and _permission_key not in MANAGER_FINANCE_INHERITANCE_EXCEPTIONS:
        _roles.add("manager")

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
        groups.insert(0, {"label": "菜单入口", "items": menu_only_items})
    return groups

SUPPORTED_LANGUAGES = {
    "zh-CN": {"label": "简体中文", "flag": "🇨🇳"},
    "en": {"label": "English", "flag": "🇺🇸"},
    "nl": {"label": "Nederlands", "flag": "🇳🇱"},
    "de": {"label": "Deutsch", "flag": "🇩🇪"},
    "es": {"label": "Español", "flag": "🇪🇸"},
}
DEFAULT_LANGUAGE = "zh-CN"

PROJECT_TYPE_LABELS = {
    "invoice": "发票项目",
    "expense": "员工报销",
    "customer_expense": "工单结算",
}

CONTRACT_TYPE_LABELS = {
    "framework": "框架合同",
    "project": "项目合同",
}

CONTRACT_STATUS_LABELS = {
    "draft": "草稿",
    "review": "审核中",
    "signed": "已签署",
    "active": "执行中",
    "completed": "已完成",
    "terminated": "已终止",
}

# 客户费率一律以数据库（合同费率版本）为准；这里只保留项目名称清单用于
# 初始化 projects 目录，不再携带任何费率数值，也不会覆盖已有 unit_price。
CUSTOMER_REIMBURSEMENT_PROJECTS = (
    "标准工时",
    "交通工时",
    "加班工时",
    "节假日工时",
    "里程费",
)

# 工单结算行字段 -> 合同费率版本 rate_type -> 展示名
CUSTOMER_REIMBURSEMENT_RATE_FIELDS = (
    ("standard_rate", "regular_hours", "标准工时"),
    ("transport_rate", "travel_hours", "交通工时"),
    ("public_transport_rate", "public_transport_hours", "公共交通工时"),
    ("overtime_rate", "overtime_hours", "加班工时"),
    ("holiday_rate", "holiday_hours", "节假日工时"),
    ("mileage_rate", "mileage", "里程费"),
)

# 发票行中无“来源项目”概念的三类，仍按结算单 totals 开票；
# “其他”桶按结算明细 auto_expense_sources 的来源项目经 expense_settlement_invoice_map 拆分。
CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS = {
    "Technical Services": "labor_total",
    "Travel Expenses Reimbursement": "travel_total",
    "Mileage Reimbursement": "mileage_total",
}

# 工单结算里映射可指向的金额字段（expense_settlement_invoice_map.settlement_field 值域）
SETTLEMENT_EXPENSE_FIELD_LABELS = {
    "lodging": "住宿费",
    "airfare": "机票费",
    "baggage": "行李费",
    "rental_car": "租车费用",
    "fuel": "燃油费",
    "parking": "停车费",
    "taxi": "打车费",
    "other": "其他",
}

DEFAULT_OWNER_NAMES = [
    "未知",
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
    "阳光",
    "LG",
    "比亚迪",
]

DEFAULT_SITE_OWNER_NAMES = {
    "edinburg": "Stella",
    "revolution": "spearmint",
    "ebony": "plus power",
    "anemoi": "plus power",
}






app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["DEPLOYMENT_ENV"] = os.environ.get("DEPLOYMENT_ENV", "production").strip().lower()
# Persistent login session for mobile PWA: without session.permanent the
# cookie is a browser-session cookie and iOS Safari discards it every time
# the PWA is closed, forcing a re-login.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

from settlement_request_limits import register_settlement_request_limits

register_settlement_request_limits(app)


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
        g.db = PostgreSQLConnection()
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
    identity_db = PostgreSQLConnection()
    try:
        identity_row = identity_db.execute(
            "select value from settings where key = 'production_database_identity'"
        ).fetchone()
    finally:
        identity_db.close()
    if not identity_row or identity_row[0] != DATA_DIRECTORY_IDENTITY:
        raise RuntimeError("Protected PostgreSQL database identity mismatch.")


# MRO 项目合并后的规范全名：裸名（MRO Supplies / MroSupplies）一律归到它名下。












@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def reset_ai_drafts_for_deleted_service_report(connection, report_id):
    """Make AI drafts reusable after their generated formal report is deleted."""
    rows = connection.execute(
        """
        select id from ai_daily_report_drafts where saved_report_id = ?
        union
        select draft_id from ai_daily_report_formal_commits where service_report_id = ?
        """,
        (report_id, report_id),
    ).fetchall()
    draft_ids = [int(row[0]) for row in rows]
    if not draft_ids:
        return 0
    placeholders = ",".join("?" for _ in draft_ids)
    connection.execute(
        f"delete from ai_daily_report_formal_commits where draft_id in ({placeholders})",
        draft_ids,
    )
    connection.execute(
        f"""
        update ai_daily_report_drafts
        set status = 'draft', saved_report_id = null,
            draft_version = draft_version + 1, updated_at = ?
        where id in ({placeholders})
        """,
        [now(), *draft_ids],
    )
    return len(draft_ids)


def ensure_postgres_admin():
    """PostgreSQL 模式下沿用 init_db 的管理员引导语义（fail-closed）。

    库里已有管理员时是空操作（每次启动一次 SELECT）；全新 PG 部署或灾备
    恢复后没有管理员时，按 ADMIN_EMAIL / ADMIN_PASSWORD 引导，凭据缺失、
    为空、为默认密码、或邮箱已属于普通用户时拒绝启动。
    """
    with app.app_context():
        connection = db()
        if connection.execute(
            "select id from users where role = 'admin' limit 1"
        ).fetchone():
            return
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
        try:
            connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values (?, ?, ?, 'admin', ?)
                """,
                ("Admin", admin_email, generate_password_hash(admin_password), now()),
            )
            connection.commit()
        except IntegrityError:
            # 多 worker 并发启动：另一个进程已插入管理员，视为成功
            if connection.execute(
                "select id from users where role = 'admin' limit 1"
            ).fetchone():
                return
            raise


def init_db():
    """Validate the migrated PostgreSQL schema and bootstrap its first admin."""
    verify_postgres_schema()
    ensure_postgres_admin()
    return


def current_language():
    language = session.get("language", DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_from_form(default=DEFAULT_LANGUAGE):
    language = request.form.get("default_language", default or DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


COMMUNICATION_LANGUAGES = {
    "zh-CN": "中文",
    "en": "English",
    "es": "Español",
}


def communication_languages_from_form(default="zh-CN"):
    return _split_communication.communication_languages_from_form(default, COMMUNICATION_LANGUAGES=COMMUNICATION_LANGUAGES, request=request)


def normalize_phone(value, country_code="US"):
    return _split_phone.normalize_phone(value, country_code, re=re)


def clear_session_preserving_language():
    language = current_language()
    session.clear()
    session["language"] = language










def report_location_filters(table_alias="service_orders"):
    return _split_report_location.report_location_filters(table_alias, country_rows=country_rows, request=request)


def get_setting(key, default=""):
    return _split_setting_write.get_setting(key, default, db=db)


def set_setting(key, value):
    return _split_setting_write.set_setting(key, value, db=db)


def setting_float(key, default=0):
    return _split_setting_float.setting_float(key, default, get_setting=get_setting)


def setting_int(key, default=0):
    return _split_setting_float.setting_int(key, default, get_setting=get_setting)


def inspection_cycle_days():
    return max(1, setting_int("inspection_cycle_days", 180))


def inspection_warning_days():
    return max(1, setting_int("inspection_warning_days", 150))


def positive_int(value, default=1):
    return _split_setting_float.positive_int(value, default)


def payroll_subsidy_settings():
    return _split_subsidy_settings.payroll_subsidy_settings(get_setting=get_setting, setting_float=setting_float)


def lodging_reimbursement_limit():
    return payroll_subsidy_settings()["lodging_limit"]


def is_lodging_project_name(name):
    normalized = (name or "").strip().casefold()
    return "住宿" in normalized or "lodging" in normalized or "hotel" in normalized


def is_fuel_project_name(name):
    normalized = (name or "").strip().casefold()
    return "油费" in normalized or "加油" in normalized or "fuel" in normalized or "gas" in normalized


def validate_lodging_reimbursement(amount, label="住宿报销"):
    return _split_subsidy_settings.validate_lodging_reimbursement(amount, label, lodging_reimbursement_limit=lodging_reimbursement_limit)


def validate_fuel_reimbursement_allowed(amount, label="油费报销"):
    if amount > 0 and payroll_subsidy_settings()["car_allowance_method"] == "mileage":
        raise ValueError(f"当前车补按里程计费，{label}不可报销。")


def get_timezone_name():
    return _split_timezone.get_timezone_name(DEFAULT_TIMEZONE=DEFAULT_TIMEZONE, get_setting=get_setting)


def app_timezone():
    return _split_timezone.app_timezone(DEFAULT_TIMEZONE=DEFAULT_TIMEZONE, ZoneInfo=ZoneInfo, ZoneInfoNotFoundError=ZoneInfoNotFoundError, get_timezone_name=get_timezone_name)


def get_company_profile():
    return {key: get_setting(f"company_{key}", value) for key, value in DEFAULT_COMPANY_PROFILE.items()}


def get_payment_instructions():
    return {key: get_setting(f"payment_{key}", value) for key, value in DEFAULT_PAYMENT_INSTRUCTIONS.items()}


def get_invoice_terms():
    return get_setting("invoice_terms", DEFAULT_INVOICE_TERMS)


def get_smtp_settings():
    return {key: get_setting(f"smtp_{key}", value) for key, value in DEFAULT_SMTP_SETTINGS.items()}


def headquarters_coordinates():
    return _split_headquarters.headquarters_coordinates(HEADQUARTERS_LATITUDE=HEADQUARTERS_LATITUDE, HEADQUARTERS_LONGITUDE=HEADQUARTERS_LONGITUDE, math=math)


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


def get_google_places_api_key():
    return _split_headquarters.get_google_places_api_key(GOOGLE_PLACES_API_KEY_ENV=GOOGLE_PLACES_API_KEY_ENV, get_google_geocoding_api_key=get_google_geocoding_api_key, get_setting=get_setting)


def save_named_attachment(uploaded, directory, table, owner_column=None, owner_id=None):
    return _split_named_attachment.save_named_attachment(uploaded, directory, table, owner_column, owner_id, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, db=db, g=g, now=now, os=os, secrets=secrets)


def get_company_attachments():
    return _split_company_attachments.get_company_attachments(db=db)








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
    if request.method == "POST":
        app.logger.warning("POST diag: path=%s content_length=%s max_cl=%s max_fms=%s terminated=%s",
            request.path, request.content_length,
            getattr(request, 'max_content_length', 'N/A'),
            getattr(request, 'max_form_memory_size', 'N/A'),
            request.environ.get('wsgi.input_terminated'))
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


# ─── Phase 6: AI Daily Report CSRF Protection ──────────────────────────────

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


# ─── Phase 6: AI Daily Report Authorization Helpers ────────────────────────

def can_view_ai_daily_report_draft(user, draft_row):
    """Check if user can view an AI Daily Report Draft.

    Rules:
    - admin/manager/finance: can view all drafts
    - employee: can view drafts where they are a worker, or drafts they created
    - external users: cannot view

    Args:
        user: current database row or dict
        draft_row: draft database row (must include created_by, draft_data)

    Returns:
        bool: True if user can view
    """
    if not user or not draft_row:
        return False

    # Convert database row to dict if needed
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
        user: current database row or dict
        draft_row: draft database row

    Returns:
        bool: True if user can edit
    """
    if not user or not draft_row:
        return False

    # Convert database row to dict if needed
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

    Worker matching uses PostgreSQL jsonb_array_elements to check
    draft_data.workers[].user_id.
    This is stable identity matching (user_id), NOT name matching, to prevent
    same-name employees from gaining unauthorized access.
    """
    if not user:
        return ["1=0"], []

    # Convert database row to dict (row objects do not support attribute access)
    if hasattr(user, "keys"):
        user = dict(user)

    role = user.get("role", "")
    user_id = user.get("id", "")

    # Admin/manager/finance see all
    if role in {"admin", "manager", "finance"}:
        return ["1=1"], []

    # Employee sees drafts they created OR where they are a worker (by stable user_id)
    if role == "employee":
        # PostgreSQL jsonb: expand workers[] and match user_id as text.
        # This prevents same-name employees from gaining access (name-based matching is unsafe)
        worker_clause = """EXISTS (
            SELECT 1 FROM jsonb_array_elements(ai_daily_report_drafts.draft_data::jsonb -> 'workers') w
            WHERE w.value ->> 'user_id' = ?::text
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
    return _split_access.can_manage_customer_reimbursement(g=g, has_action_permission=has_action_permission)


# v0.1.239: 工单结算的「修改状态」「审核」「删除」改为完全由菜单权限配置决定
# （权限管理页面可勾选），默认财务/经理/管理员均有权限。
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


def order_has_approved_reimbursement(order_id):
    """Return True if the service order already has an approved customer reimbursement.

    Once the customer reimbursement is approved, the order's daily reports are
    locked: no create / edit / delete. The reimbursement must be returned to
    draft first. This prevents phantom report hours that affect payroll without
    changing what the customer pays.
    """
    if not order_id:
        return False
    row = db().execute(
        "select 1 from customer_reimbursements where service_order_id = ? and status = 'approved' limit 1",
        (order_id,),
    ).fetchone()
    return row is not None


def report_locked_by_approved_reimbursement(order):
    """Flash a friendly lock message and return True when the order is locked."""
    if order and order_has_approved_reimbursement(order["id"]):
        flash(
            "该工单的客户结算已通过审核，日报已锁定。如需新建、修改或删除日报，"
            "请先在工单结算页面将结算退回为草稿。",
            "error",
        )
        return True
    return False


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
    if role == "manager" and (
        overrides.get(("finance", menu_key), False)
        or any(
            enabled
            for (permission_role, resource_key, _action_key), enabled
            in action_overrides.items()
            if permission_role == "finance" and resource_key == menu_key
        )
    ):
        return True
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
    if action_key != "view" and override_key in overrides:
        return overrides[override_key]
    if role == "manager" and (resource_key, action_key) not in MANAGER_FINANCE_INHERITANCE_EXCEPTIONS and (
        overrides.get(("finance", resource_key, action_key), False)
        or (
            action_key == "view"
            and menu_overrides.get(("finance", resource_key), False)
        )
    ):
        return True
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
        ("accounting_receipts", "GET"): ("accounting_receipts", "view"),
        ("accounting_receipts", "POST"): ("accounting_receipts", "create"),
        ("accounting_prepayment_apply", "GET"): ("accounting_receipts", "edit"),
        ("accounting_prepayment_apply", "POST"): ("accounting_receipts", "edit"),
        ("accounting_invoice_correct", "GET"): ("accounting_corrections", "create"),
        ("accounting_invoice_correct", "POST"): ("accounting_corrections", "create"),
        ("accounting_invoice_correction_reverse", "POST"): ("accounting_corrections", "create"),
        ("accounting_periods", "GET"): ("accounting_periods", "view"),
        ("accounting_periods", "POST"): ("accounting_periods", "view"),
        ("accounting_opening_balances", "GET"): ("accounting_opening", "view"),
        ("accounting_opening_balances", "POST"): ("accounting_opening", "view"),
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
        ("order_data_transfer", "GET"): ("order_data_transfer", "view"),
        ("order_data_transfer", "POST"): ("order_data_transfer", "execute"),
        ("company_info", "GET"): ("company_info", "view"),
        ("company_info", "POST"): ("company_info", "edit"),
        ("travel_tools_page", "GET"): ("travel_tools", "view"),
        ("travel_tools_route_map_api", "POST"): ("travel_tools", "view"),
        ("travel_tools_hotel_api", "POST"): ("travel_tools", "view"),
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
        "quotations": ("quotations", "view"),
        "new_quotation": ("quotations", "create"),
        "quotation_detail": ("quotations", "view"),
        "edit_quotation": ("quotations", "edit"),
        "delete_quotation": ("quotations", "delete"),
        "quotation_pdf": ("quotations", "export"),
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
        "update_employee_grade_members": ("employee_grades", "edit"),
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
        "service_order_map_static_image": ("service_orders", "view"),
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
        "accounting_vouchers": ("accounting_vouchers", "view"),
        "accounting_voucher_detail": ("accounting_vouchers", "view"),
        "accounting_receipts": ("accounting_receipts", "view"),
        "accounting_prepayment_apply": ("accounting_receipts", "edit"),
        "accounting_invoice_correct": ("accounting_corrections", "create"),
        "accounting_invoice_correction_reverse": ("accounting_corrections", "create"),
        "accounting_periods": ("accounting_periods", "view"),
        "accounting_opening_balances": ("accounting_opening", "view"),
        "accounting_trial_balance": ("accounting_reports", "view"),
        "accounting_income_statement": ("accounting_reports", "view"),
        "accounting_balance_sheet": ("accounting_reports", "view"),
        "accounting_receivable_aging": ("accounting_reports", "view"),
        "accounting_customer_statement": ("accounting_reports", "view"),
        "accounting_sales_tax_liability": ("accounting_reports", "view"),
        "accounting_account_ledger": ("accounting_reports", "view"),
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
        # Phase 4A：Tax Review 工作台（写动作额外要求 tax_review.review）
        "tax_review": ("tax_review", "view"),
        "tax_review_component": ("tax_review", "view"),
        "save_tax_review": ("tax_review", "review"),
        # Phase 4B：Annual Tax Summary（导出额外要求 annual_tax_summary.export）
        "annual_tax_summary": ("annual_tax_summary", "view"),
        "annual_tax_summary_components": ("annual_tax_summary", "view"),
        "annual_tax_summary_export": ("annual_tax_summary", "export"),
        # Phase 6C：CPA Policy Decision Package（权限复用 annual_tax_summary）
        "policy_decisions": ("annual_tax_summary", "view"),
        "policy_decisions_export": ("annual_tax_summary", "export"),
        # Phase 6B：CPA Workpaper（只读 reporting，权限同 annual_tax_summary；
        # 导出额外要求 export。复核动作仍走 4A 的 tax_review.review。）
        "cpa_workpaper": ("annual_tax_summary", "view"),
        "cpa_workpaper_export": ("annual_tax_summary", "export"),
    }
    return method_rules.get((endpoint, method)) or endpoint_rules.get(endpoint)


def can_access_client(client_id):
    if not g.user:
        return False
    if is_internal_user():
        return True
    return is_external_manager() and g.user["client_id"] == client_id


def require_invoice_access(invoice_id):
    return _split_invoice_access.require_invoice_access(invoice_id, abort=abort, can_access_client=can_access_client, can_view_invoices=can_view_invoices, db=db)


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
        "admin": "管理员",
        "manager": "经理",
        "finance": "财务",
        "employee": "员工",
        "user": "员工",
        "internal": "财务",
        "external": "外部管理员",
        "external_manager": "外部管理员",
        "external_employee": "外部员工",
    }
    return labels.get(role, role)


def state_label(state_code):
    """站点州简写 + 当前语言的州名，如 "TX · 得克萨斯州"（en 下为 "TX · Texas"）。"""
    code = (state_code or "").strip().upper()
    if not code:
        return ""
    name = localized_state_name(code, current_language())
    return f"{code} · {name}" if name else code


def money(value, currency="USD"):
    symbols = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "JPY": "¥"}
    amount = float(value or 0)
    if currency == "JPY":
        return f"{symbols.get(currency, currency + ' ')}{amount:,.0f}"
    return f"{symbols.get(currency, currency + ' ')}{amount:,.2f}"


def hours(value):
    """时间数量（工时 / 时长 / 小时）保留两位小数；金额与单价不要用它。

    只用于**只读展示**：表单里的 input 值必须保留原始精度（如 0.25 小时），
    否则保存时会把用户填的值改掉。

    两位小数是必须的：工时按 15 分钟量化，最小档位就是 0.25 小时，
    一位小数会把 2.25 显示成 2.2（f"{2.25:.1f}" == "2.2"），与
    2.25×时薪 算出的金额对不上，用户会以为算错。去掉末尾无意义的 0，
    使 2.0 显示成 "2"、2.25 显示成 "2.25"。
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    text = f"{number:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


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
    return _split_invoice_totals.invoice_totals(invoice_id, db=db)


def accounting_base_enabled(connection=None):
    connection = connection or db()
    row = connection.execute(
        "select value from settings where key='accounting_base_enabled'"
    ).fetchone()
    return bool(row and row[0] == "1")


def recognize_invoice_if_accounting_enabled(invoice_id, *, source_version=1):
    return _split_invoice_recognition.recognize_invoice_if_accounting_enabled(invoice_id, source_version=source_version, InvoiceRecognitionService=InvoiceRecognitionService, accounting_base_enabled=accounting_base_enabled, db=db, g=g)


def payment_label(invoice):
    if invoice["status"] == "void":
        return "不适用"
    if invoice["paid_at"]:
        return "已核销"
    if invoice["status"] == "completed":
        return "待核销"
    return "流程中"


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
    return sql_statement_kind(sql) in {"select", "with", "explain"}


def client_order_number_warning(customer_name, client_order_number):
    """Return a warning for the current Sanhe Tongfei work-order number convention."""
    normalized_customer = (customer_name or "").strip().casefold()
    if "三河同飞" not in normalized_customer and "sanhe tongfei" not in normalized_customer:
        return ""
    number = (client_order_number or "").strip()
    if not number:
        return "未填写服务订单号码，无法按三河同飞规则识别。"
    if not number.startswith("SHPG") and len(number) < 16:
        return "服务订单号码应以 SHPG 开头且总长度不少于16位，当前格式不符合三河同飞规则。"
    if not number.startswith("SHPG"):
        return "服务订单号码应以 SHPG 开头，当前格式不符合三河同飞规则。"
    if len(number) < 16:
        return f"服务订单号码总长度应不少于16位，当前为{len(number)}位。"
    return ""


def is_image_attachment(content_type, filename):
    return _split_image_detection.is_image_attachment(content_type, filename, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS)


def is_customer_reimbursement_image_attachment(attachment):
    return _split_image_detection.is_customer_reimbursement_image_attachment(attachment, customer_reimbursement_attachment_path=customer_reimbursement_attachment_path, is_image_attachment=is_image_attachment, valid_image_file=valid_image_file)


app.jinja_env.filters["money"] = money
app.jinja_env.filters["hours"] = hours
app.jinja_env.filters["role_label"] = role_label
app.jinja_env.filters["payment_label"] = payment_label
app.jinja_env.filters["local_datetime"] = local_datetime
app.jinja_env.globals["state_label"] = state_label
app.jinja_env.globals["can_view_invoices"] = can_view_invoices
app.jinja_env.globals["can_create_invoice"] = can_create_invoice
app.jinja_env.globals["can_view_contracts"] = can_view_contracts
app.jinja_env.globals["can_manage_contracts"] = can_manage_contracts
app.jinja_env.globals["can_create_service_order"] = can_create_service_order
app.jinja_env.globals["can_delete_service_order"] = can_delete_service_order
app.jinja_env.globals["can_create_expense"] = can_create_expense
app.jinja_env.globals["can_manage_customer_reimbursement"] = can_manage_customer_reimbursement
app.jinja_env.globals["is_external_user"] = is_external_user
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


































def next_invoice_number():
    return _invoice_numbering.next_invoice_number(date=date, db=db, lock_number_allocation=lock_number_allocation)


def next_contract_number():
    return _contract_services.next_contract_number(date=date, db=db, lock_number_allocation=lock_number_allocation, secrets=secrets)


def contract_attachment_dir(contract_id):
    return _contract_services.contract_attachment_dir(contract_id, CONTRACT_ATTACHMENTS_DIR=CONTRACT_ATTACHMENTS_DIR, os=os)


def contract_attachment_path(attachment):
    return _contract_services.contract_attachment_path(attachment, CONTRACT_ATTACHMENTS_DIR=CONTRACT_ATTACHMENTS_DIR, os=os)


def save_contract_uploads(contract_id):
    return _contract_services.save_contract_uploads(contract_id, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, contract_attachment_dir=contract_attachment_dir, db=db, g=g, now=now, os=os, request=request, secrets=secrets)


def contract_form_values(contract=None):
    return _contract_services.contract_form_values(contract, CONTRACT_STATUS_LABELS=CONTRACT_STATUS_LABELS, CONTRACT_TYPE_LABELS=CONTRACT_TYPE_LABELS, can_access_client=can_access_client, db=db, next_contract_number=next_contract_number, request=request, to_float=to_float)


def next_service_order_number():
    return _service_order_services.next_service_order_number(date=date, db=db, lock_number_allocation=lock_number_allocation)


def next_expense_number():
    lock_number_allocation(db())
    prefix = f"EX{date.today():%y%m}"
    # 现存最大号；删除报销会让它下降，不能单独作为下一号依据，否则被删的号会被复用。
    row = db().execute(
        """
        select expense_number from expenses
        where expense_number like ?
        order by expense_number desc limit 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    current_max = 0
    if row:
        try:
            current_max = int(row["expense_number"][len(prefix):])
        except (TypeError, ValueError):
            current_max = 0
    # 持久化水位：只增不减。已发过的号（包括已被删除报销曾经占用的号）
    # 永远不再分配给新报销，避免历史号段撞单。
    hw_key = f"expense_high_water_{prefix}"
    high_water = setting_int(hw_key, 0)
    n = max(current_max, high_water) + 1
    # 防御：理论上不会撞号，历史数据/并发下跳过任何已占用号。
    while db().execute(
        "select id from expenses where expense_number = ?",
        (f"{prefix}{n:03d}",),
    ).fetchone():
        n += 1
    set_setting(hw_key, str(n))
    return f"{prefix}{n:03d}"


def invoice_number_exists(invoice_number, exclude_invoice_id=None):
    return _invoice_numbering.invoice_number_exists(invoice_number, exclude_invoice_id, db=db)


def allowed_attachment(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def normalized_attachment_filename(filename):
    return _invoice_attachment_names.normalized_attachment_filename(filename, os=os)


def invoice_attachment_dir(invoice_id):
    return _invoice_storage.invoice_attachment_dir(invoice_id, ATTACHMENTS_DIR=ATTACHMENTS_DIR, os=os)


def attachment_file_path(attachment):
    return _invoice_storage.attachment_file_path(attachment, ATTACHMENTS_DIR=ATTACHMENTS_DIR, os=os)


def invoice_attachment_path(invoice_id):
    return _invoice_storage.invoice_attachment_path(invoice_id, ATTACHMENTS_DIR=ATTACHMENTS_DIR, os=os)


def existing_attachment_names(invoice_id):
    return _invoice_attachment_names.existing_attachment_names(invoice_id, db=db, normalized_attachment_filename=normalized_attachment_filename)


def duplicate_attachment_names(uploads, invoice_id=None):
    return _invoice_attachment_names.duplicate_attachment_names(uploads, invoice_id, existing_attachment_names=existing_attachment_names, normalized_attachment_filename=normalized_attachment_filename, os=os)


def validate_attachment_uploads(uploads, invoice_id=None):
    return _invoice_attachment_names.validate_attachment_uploads(uploads, invoice_id, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, duplicate_attachment_names=duplicate_attachment_names, flash=flash)


def save_uploaded_attachment(invoice_id, uploaded):
    return _invoice_storage.save_uploaded_attachment(invoice_id, uploaded, db=db, existing_attachment_names=existing_attachment_names, g=g, invoice_attachment_dir=invoice_attachment_dir, normalized_attachment_filename=normalized_attachment_filename, now=now, os=os, secrets=secrets)


def unique_invoice_attachment_filename(invoice_id, original_filename):
    return _invoice_attachment_names.unique_invoice_attachment_filename(invoice_id, original_filename, existing_attachment_names=existing_attachment_names, normalized_attachment_filename=normalized_attachment_filename, os=os)


def copy_file_to_invoice_attachment(invoice_id, source_path, original_filename, content_type=None, uploaded_by=None):
    return _invoice_copying.copy_file_to_invoice_attachment(invoice_id, source_path, original_filename, content_type, uploaded_by, db=db, g=g, invoice_attachment_dir=invoice_attachment_dir, now=now, os=os, secrets=secrets, shutil=shutil, unique_invoice_attachment_filename=unique_invoice_attachment_filename)


def copy_customer_reimbursement_attachments_to_invoice(invoice_id, reimbursement_id):
    return _invoice_copying.copy_customer_reimbursement_attachments_to_invoice(invoice_id, reimbursement_id, copy_file_to_invoice_attachment=copy_file_to_invoice_attachment, customer_reimbursement_attachment_path=customer_reimbursement_attachment_path, get_customer_reimbursement_attachments=get_customer_reimbursement_attachments)


def copy_mileage_proofs_to_invoice(invoice_id, service_order_id):
    return _invoice_copying.copy_mileage_proofs_to_invoice(invoice_id, service_order_id, copy_file_to_invoice_attachment=copy_file_to_invoice_attachment, db=db, existing_attachment_names=existing_attachment_names, normalized_attachment_filename=normalized_attachment_filename, report_attachment_path=report_attachment_path)


def uploaded_attachments_from_request():
    return _split_invoice_uploads.uploaded_attachments_from_request(request=request)


def get_invoice_attachments(invoice_id):
    return _invoice_storage.get_invoice_attachments(invoice_id, db=db)


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
               quotations.quotation_number,
               quotations.customer as quotation_customer,
               quotations.project_name as quotation_project_name,
               quotations.status as quotation_status,
               quotations.current_revision_no as quotation_revision_no,
               quotations.total as quotation_current_total,
               quotations.currency as quotation_currency,
               coalesce(country_local.name, country_zh.name, country_en.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, country_en.region_name, service_orders.region_code) as region_name
        from service_orders
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join manufacturers as order_manufacturers on order_manufacturers.id = service_orders.manufacturer_id
        left join owners on owners.id = buyers.owner_id
        left join clients on clients.id = service_orders.client_id
        left join contracts on contracts.id = service_orders.contract_id
        left join quotations on quotations.id = service_orders.quotation_id
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
    return _service_order_services.service_order_dependency_counts(order_id, db=db)


def service_order_delete_blockers(order_id):
    return _service_order_services.service_order_delete_blockers(order_id, service_order_dependency_counts=service_order_dependency_counts)


def require_service_order_start_date(order):
    if order["start_date"]:
        return None
    flash("请先编辑工单并维护开始日期，再新增发票、工作日报或报销。", "error")
    return redirect(url_for("edit_service_order", order_id=order["id"]))


def require_service_report(report_id):
    report = db().execute("select * from service_reports where id = ?", (report_id,)).fetchone()
    if not report:
        abort(404)
    order = require_service_order(report["service_order_id"])
    return report, order


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def report_storage_context_values(order_number, report_date, report_id=None):
    return _report_storage.report_storage_context_values(order_number, report_date, report_id, datetime=datetime, secure_filename=secure_filename)


def report_storage_context(report_id):
    return _report_storage.report_storage_context(report_id, abort=abort, app=app, db=db, report_storage_context_values=report_storage_context_values, request=request)


def report_attachment_relative_path(report_id, category, stored_filename, storage_context=None):
    return _report_storage.report_attachment_relative_path(report_id, category, stored_filename, storage_context, Path=Path, REPORT_PHOTO_FOLDERS=REPORT_PHOTO_FOLDERS, os=os, report_storage_context=report_storage_context)


def report_attachment_dir(report_id, category, storage_context=None):
    return _report_storage.report_attachment_dir(report_id, category, storage_context, REPORT_ATTACHMENTS_DIR=REPORT_ATTACHMENTS_DIR, os=os, report_attachment_relative_path=report_attachment_relative_path)


def report_attachment_path(attachment):
    return _report_storage.report_attachment_path(attachment, Path=Path, REPORT_ATTACHMENTS_DIR=REPORT_ATTACHMENTS_DIR, os=os)


def prune_empty_report_folders(path):
    return _report_storage.prune_empty_report_folders(path, Path=Path, REPORT_ATTACHMENTS_DIR=REPORT_ATTACHMENTS_DIR)


def relocate_report_attachments(report_id):
    return _report_storage.relocate_report_attachments(report_id, Path=Path, REPORT_ATTACHMENTS_DIR=REPORT_ATTACHMENTS_DIR, db=db, os=os, prune_empty_report_folders=prune_empty_report_folders, report_attachment_path=report_attachment_path, report_attachment_relative_path=report_attachment_relative_path, shutil=shutil)


def uploaded_report_files(field_name):
    return _report_storage.uploaded_report_files(field_name, request=request)


def compress_report_image(source, target_path):
    return _report_storage.compress_report_image(source, target_path, compress_image=compress_image)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_report_photo_name(filename):
    return _report_storage.normalized_report_photo_name(filename, os=os, re=re)


def is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
    return _report_storage.is_duplicate_report_photo(report_id, category, original_filename, candidate_hash, db=db, file_sha256=file_sha256, normalized_report_photo_name=normalized_report_photo_name, os=os, report_attachment_path=report_attachment_path)


def save_report_attachment(report_id, uploaded, category, storage_context=None):
    return _report_storage.save_report_attachment(report_id, uploaded, category, storage_context, allowed_image=allowed_image, compress_report_image=compress_report_image, db=db, file_sha256=file_sha256, g=g, is_duplicate_report_photo=is_duplicate_report_photo, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_relative_path=report_attachment_relative_path, secrets=secrets)


def save_report_file_attachment(report_id, uploaded, category, storage_context=None):
    return _report_storage.save_report_file_attachment(report_id, uploaded, category, storage_context, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, db=db, file_sha256=file_sha256, g=g, is_duplicate_report_photo=is_duplicate_report_photo, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_relative_path=report_attachment_relative_path, secrets=secrets)


def shared_photos_root():
    return Path(SHARED_PHOTOS_DIR).resolve()


def service_order_photo_folder(order_number):
    root = shared_photos_root()
    folder = (root / secure_filename(order_number)).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("工单照片目录无效。")
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
                raise ValueError("这个 Google Photos 分享链接需要登录或没有公开访问权限，服务器无法直接导入。")
            content_type = response.headers.get_content_type()
            if content_type and "html" not in content_type:
                raise ValueError("链接返回的不是 Google Photos 分享页面。")
            return response.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ValueError("这个 Google Photos 分享链接需要登录或没有访问权限，服务器无法直接导入。") from error
        raise ValueError(f"读取 Google Photos 分享链接失败：HTTP {error.code}") from error
    except URLError as error:
        raise ValueError(f"读取 Google Photos 分享链接失败：{error.reason}") from error


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
                    raise ValueError(f"不是图片内容：{content_type or 'unknown'}")
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
                            raise ValueError("图片超过 30MB，已跳过。")
                        digest.update(chunk)
                        file.write(chunk)
                return target_path, digest.hexdigest(), total
        except ValueError as error:
            if "超过 30MB" in str(error):
                raise
            last_error = error
        except (HTTPError, URLError, OSError) as error:
            last_error = error
    raise ValueError(f"无法下载原图：{last_error}")


def import_google_photos_share_to_incoming(order, share_url):
    if not is_google_photos_share_url(share_url):
        raise ValueError("请输入 Google Photos 分享链接。")
    page_html = google_photos_page_html(share_url)
    image_urls, total_found = extract_google_photo_urls(page_html)
    if not image_urls:
        raise ValueError("没有从这个分享链接中识别到可下载照片。链接可能需要登录，或 Google 页面结构发生变化。")
    target_dir = service_order_incoming_folder(order["order_number"], "google-photos")
    imported = []
    skipped = []
    seen_hashes = set()
    for index, image_url in enumerate(image_urls, start=1):
        try:
            path, digest, size = download_google_photo_image(image_url, target_dir, index)
            if digest in seen_hashes:
                path.unlink(missing_ok=True)
                skipped.append(f"第 {index} 张：重复照片")
                continue
            seen_hashes.add(digest)
            imported.append({"path": path, "size": size})
        except Exception as error:
            skipped.append(f"第 {index} 张：{error}")
    if not imported and skipped:
        raise ValueError("照片下载失败：" + "；".join(skipped[:5]))
    truncated_count = max(0, total_found - len(image_urls))
    return imported, skipped, target_dir, total_found, truncated_count


def send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found=0, truncated_count=0, error=None):
    success_count = len(imported or [])
    skipped_count = len(skipped or [])
    if error:
        title = f"Google Photos 导入失败：{order['order_number']}"
        lines = [
            f"工单：{order['order_number']}",
            f"链接：{share_url}",
            f"失败原因：{error}",
        ]
    else:
        title = f"Google Photos 导入完成：{order['order_number']}"
        lines = [
            f"工单：{order['order_number']}",
            f"链接：{share_url}",
            f"识别到照片：{total_found} 张",
            f"尝试导入：{success_count + skipped_count} 张",
            f"导入照片：{success_count} 张",
            f"跳过/失败：{skipped_count} 张",
            f"保存位置：{target_dir}",
            "照片已放入 incoming/google-photos，photo_worker 会自动处理到 pictures。",
        ]
        if truncated_count:
            lines.append(f"注意：识别到的照片超过系统单次导入上限 {GOOGLE_PHOTOS_MAX_IMPORT} 张，仍有 {truncated_count} 张未导入。")
        if skipped:
            lines.append("前几条跳过/失败信息：")
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
    return _photo_capture_services.resolve_shared_photo(relative_path, require_file, allow_missing, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, abort=abort, shared_photos_root=shared_photos_root)


def shared_photo_relative(path):
    return _photo_capture_services.shared_photo_relative(path, shared_photos_root=shared_photos_root)


def resolve_shared_picture(relative_path):
    return _photo_capture_services.resolve_shared_picture(relative_path, abort=abort, resolve_shared_photo=resolve_shared_photo, shared_photos_root=shared_photos_root)


def count_shared_images(path, excluded_dirs=None):
    return _photo_capture_services.count_shared_images(path, excluded_dirs, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, valid_image_file=valid_image_file)


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
    return _photo_capture_services.order_photo_status(order_dir, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, count_shared_images=count_shared_images)


def save_shared_report_photo(report_id, relative_path, category, storage_context=None):
    return _report_storage.save_shared_report_photo(report_id, relative_path, category, storage_context, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, db=db, file_sha256=file_sha256, g=g, is_duplicate_report_photo=is_duplicate_report_photo, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_relative_path=report_attachment_relative_path, report_storage_context=report_storage_context, resolve_shared_photo=resolve_shared_photo, secrets=secrets, shared_photos_root=shared_photos_root, shutil=shutil)


def save_generated_report_attachment(report_id, source_path, original_filename, replace_attachment_id=None):
    return _report_storage.save_generated_report_attachment(report_id, source_path, original_filename, replace_attachment_id, db=db, g=g, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_path=report_attachment_path, report_attachment_relative_path=report_attachment_relative_path, secrets=secrets, shutil=shutil)


def save_report_uploads(report_id, storage_context=None):
    return _report_storage.save_report_uploads(report_id, storage_context, app=app, request=request, save_report_attachment=save_report_attachment, save_report_file_attachment=save_report_file_attachment, save_shared_report_photo=save_shared_report_photo, uploaded_report_files=uploaded_report_files)


def claim_report_save_token(token, report_id=None):
    return _report_write_services.claim_report_save_token(token, report_id, db=db, now=now)


def finish_report_save_token(token, report_id):
    return _report_write_services.finish_report_save_token(token, report_id, db=db)


def claim_invoice_save_token(token, invoice_id=None):
    return _invoice_tokens.claim_invoice_save_token(token, invoice_id, db=db, now=now)


def finish_invoice_save_token(token, invoice_id):
    return _invoice_tokens.finish_invoice_save_token(token, invoice_id, db=db)


def claim_expense_save_token(token, expense_id=None):
    return _expense_write_services.claim_expense_save_token(token, expense_id, db=db, now=now)


def finish_expense_save_token(token, expense_id):
    return _expense_write_services.finish_expense_save_token(token, expense_id, db=db)


def posted_report_time(prefix):
    return _report_forms.posted_report_time(prefix, request=request)


def report_worker_ids_from_form():
    return _report_forms.report_worker_ids_from_form(request=request)


REPORT_TRAVEL_MODES = {"self_drive", "flight", "following", "rental_drive"}


def report_worker_rows_from_form():
    return _report_forms.report_worker_rows_from_form(REPORT_TRAVEL_MODES=REPORT_TRAVEL_MODES, normalize_trip_type=normalize_trip_type, request=request, to_float=to_float)


def report_billing_mileage(worker_rows, method):
    return _report_calculations.report_billing_mileage(worker_rows, method)


def get_report_attachments(report_id):
    return _report_read_services.get_report_attachments(report_id, db=db)


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
    return _split_settlement_access.require_customer_reimbursement(reimbursement_id, abort=abort, can_view_customer_reimbursement=can_view_customer_reimbursement, db=db, require_service_order=require_service_order)


def resolve_contract_rates(order_id, work_date):
    return _split_contract_rates.resolve_contract_rates(order_id, work_date, CUSTOMER_REIMBURSEMENT_RATE_FIELDS=CUSTOMER_REIMBURSEMENT_RATE_FIELDS, contract_rate=contract_rate, db=db, lodging_reimbursement_limit=lodging_reimbursement_limit)


def contract_rate_missing_summary(order_id, dates):
    return _split_contract_rates.contract_rate_missing_summary(order_id, dates, resolve_contract_rates=resolve_contract_rates)


def used_customer_rate_fields(row):
    return _split_contract_rates.used_customer_rate_fields(row, to_float=to_float)


def assert_contract_rates_covered(order_id, rows):
    return _split_contract_rates.assert_contract_rates_covered(order_id, rows, CUSTOMER_REIMBURSEMENT_RATE_FIELDS=CUSTOMER_REIMBURSEMENT_RATE_FIELDS, resolve_contract_rates=resolve_contract_rates, used_customer_rate_fields=used_customer_rate_fields)




def parse_report_minutes(value):
    return _report_calculations.parse_report_minutes(value)


def rounded_report_service_hours(arrival_time, departure_time):
    return _report_calculations.rounded_report_service_hours(arrival_time, departure_time, parse_report_minutes=parse_report_minutes)


def format_report_hours(value):
    return _report_calculations.format_report_hours(value)


def calculated_report_total_time():
    return _report_forms.calculated_report_total_time(format_report_hours=format_report_hours, report_worker_rows_from_form=report_worker_rows_from_form)


def calculated_report_total_service_hours(arrival_time, departure_time):
    return _report_forms.calculated_report_total_service_hours(arrival_time, departure_time, report_worker_rows_from_form=report_worker_rows_from_form, rounded_report_service_hours=rounded_report_service_hours)


def calculated_report_driving_miles():
    return _report_forms.calculated_report_driving_miles(report_billing_mileage=report_billing_mileage, report_worker_rows_from_form=report_worker_rows_from_form, request=request)


def observed_us_holidays(year):
    return _split_holidays.observed_us_holidays(year, date=date, timedelta=timedelta)


def is_us_weekend_or_holiday(day):
    return day.weekday() >= 5 or day in observed_us_holidays(day.year)


def report_duration_hours(report, worker_count=1):
    return _report_calculations.report_duration_hours(report, worker_count, rounded_report_service_hours=rounded_report_service_hours)


def report_actual_date(report):
    return _report_calculations.report_actual_date(report)


def _parse_quotation_revision_snapshot(quotation_revision):
    import json as _json
    try:
        data = _json.loads(quotation_revision["snapshot_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def build_quotation_settlement_rows(order, quotation_revision):
    return _split_quotation_rows.build_quotation_settlement_rows(order, quotation_revision, _parse_quotation_revision_snapshot=_parse_quotation_revision_snapshot, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, canonical_commercial_rate_key=canonical_commercial_rate_key, db=db, driving_rate_missing=driving_rate_missing, driving_transport_amount=driving_transport_amount, money_decimal=money_decimal, money_float=money_float, normalize_driving_mode=normalize_driving_mode, normalize_settlement_mode=normalize_settlement_mode, report_actual_date=report_actual_date, split_report_labor_hours=split_report_labor_hours, to_float=to_float)


def customer_reimbursement_seed_rows(order_id):
    return _split_quotation_rows.customer_reimbursement_seed_rows(order_id, build_quotation_settlement_rows=build_quotation_settlement_rows, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, db=db, report_actual_date=report_actual_date, resolve_contract_rates=resolve_contract_rates, split_report_labor_hours=split_report_labor_hours)


def merge_service_reports_into_customer_reimbursement(rows, order_id):
    return _split_source_merge.merge_service_reports_into_customer_reimbursement(rows, order_id, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, customer_reimbursement_seed_rows=customer_reimbursement_seed_rows, normalized_project_name=normalized_project_name)


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


def _row_field(row, key, default=0):
    return _split_row_field._row_field(row, key, default)


def customer_reimbursement_item_expense_amount(row, field_name):
    return _split_row_field.customer_reimbursement_item_expense_amount(row, field_name, _row_field=_row_field, money_decimal=money_decimal)


def calculate_customer_reimbursement_item(row, sort_order=0):
    return _split_row_field.calculate_customer_reimbursement_item(row, sort_order, Decimal=Decimal, _row_field=_row_field, customer_reimbursement_item_expense_amount=customer_reimbursement_item_expense_amount, decimal_value=decimal_value, money_decimal=money_decimal, money_float=money_float)


def expense_settlement_invoice_mapping():
    return _split_project_mapping.expense_settlement_invoice_mapping(MRO_CANONICAL_PROJECT_NAME=MRO_CANONICAL_PROJECT_NAME, db=db, is_mro_project_name=is_mro_project_name, project_name_key=project_name_key)


def expense_settlement_invoice_field(project_name):
    return _split_project_mapping.expense_settlement_invoice_field(project_name, expense_settlement_invoice_mapping=expense_settlement_invoice_mapping, project_name_key=project_name_key)


def canonical_project_mapping_key(value, canonical_keys):
    return _split_project_mapping.canonical_project_mapping_key(value, canonical_keys, project_name_key=project_name_key)


def customer_reimbursement_expense_field(project_name, fuel_vehicle_type=None):
    return _split_project_mapping.customer_reimbursement_expense_field(project_name, fuel_vehicle_type, expense_settlement_invoice_field=expense_settlement_invoice_field)


def approved_customer_reimbursement_expense_rows(order_id, cutoff_at=None, exclude_ignored_for_reimbursement_id=None):
    return _split_source_queries.approved_customer_reimbursement_expense_rows(order_id, cutoff_at, exclude_ignored_for_reimbursement_id, db=db)


def _reimbursement_date_key(value):
    return _split_settlement_date._reimbursement_date_key(value, date=date, datetime=datetime)


def _reimbursement_date_value(value):
    """存库用的归一日期，保持 'YYYY-MM-DD' 字符串，由 DB 适配器统一处理。"""
    return _reimbursement_date_key(value)


def _fallback_row_for_worker(candidates_by_worker, worker_key):
    """报销精确（人名+日期）匹配落空时回落到同一员工的已有明细行，避免
    『同人异日拆行』导致同一来源被计入两次（翻倍根因②）。
    - 该员工只有一行 → 直接复用
    - 多行 → 取 project_date 最近的一行（'YYYY-MM-DD' 字符串比较有效）
    - 该员工无任何行 → 返回 None（由调用方新建行）
    """
    rows = candidates_by_worker.get(worker_key)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    return max(rows, key=lambda r: _reimbursement_date_key(r.get("project_date")))


def merge_approved_expenses_into_customer_reimbursement(
    rows, order_id, cutoff_at=None, reimbursement_id=None, selection_mode="legacy"
):
    return _split_source_merge.merge_approved_expenses_into_customer_reimbursement(rows, order_id, cutoff_at, reimbursement_id, selection_mode, _fallback_row_for_worker=_fallback_row_for_worker, _reimbursement_date_key=_reimbursement_date_key, _reimbursement_date_value=_reimbursement_date_value, approved_customer_reimbursement_expense_rows=approved_customer_reimbursement_expense_rows, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, customer_reimbursement_expense_field=customer_reimbursement_expense_field, linked_approved_expense_rows=linked_approved_expense_rows, money_decimal=money_decimal, money_float=money_float, normalized_project_name=normalized_project_name, resolve_contract_rates=resolve_contract_rates)


def approved_mro_supplies_total(order_id, cutoff_at=None, exclude_ignored_for_reimbursement_id=None):
    return _split_mro_total.approved_mro_supplies_total(order_id, cutoff_at, exclude_ignored_for_reimbursement_id, Decimal=Decimal, approved_customer_reimbursement_expense_rows=approved_customer_reimbursement_expense_rows, customer_reimbursement_expense_field=customer_reimbursement_expense_field, money_decimal=money_decimal, money_float=money_float, project_name_key=project_name_key)


def approved_rental_vehicle_fuel_total(order_id):
    return _split_mro_total.approved_rental_vehicle_fuel_total(order_id, db=db, money_float=money_float)


def customer_reimbursement_totals(items, mro_supplies_total=0, rental_fuel_total=0):
    return _split_mro_total.customer_reimbursement_totals(items, mro_supplies_total, rental_fuel_total, Decimal=Decimal, _row_field=_row_field, customer_reimbursement_item_expense_amount=customer_reimbursement_item_expense_amount, money_decimal=money_decimal, money_float=money_float)


def customer_reimbursement_pending_sources(reimbursement):
    return _split_source_queries.customer_reimbursement_pending_sources(reimbursement, Decimal=Decimal, _reimbursement_date_key=_reimbursement_date_key, customer_reimbursement_expense_field=customer_reimbursement_expense_field, customer_reimbursement_items=customer_reimbursement_items, db=db, json=json, money_decimal=money_decimal, money_float=money_float)


def customer_reimbursement_gate_error(reimbursement):
    return _split_workspace.customer_reimbursement_gate_error(reimbursement, customer_reimbursement_pending_sources=customer_reimbursement_pending_sources, money=money)


def customer_reimbursement_person_days(order_id):
    return _split_workspace.customer_reimbursement_person_days(order_id, db=db)


def customer_reimbursement_lodging_range(order_id):
    return _split_workspace.customer_reimbursement_lodging_range(order_id, date=date, db=db)


def customer_reimbursement_workspace_data(order_id, reimbursement_id, saved_items):
    return _split_workspace.customer_reimbursement_workspace_data(order_id, reimbursement_id, saved_items, customer_reimbursement_seed_rows=customer_reimbursement_seed_rows, date=date, db=db, settlement_expense_candidates=settlement_expense_candidates)


def customer_reimbursement_column_totals(items):
    return _split_mro_total.customer_reimbursement_column_totals(items, Decimal=Decimal, customer_reimbursement_item_expense_amount=customer_reimbursement_item_expense_amount, money_decimal=money_decimal, money_float=money_float)


def customer_reimbursement_items(reimbursement_id):
    return _split_settlement_items.customer_reimbursement_items(reimbursement_id, db=db)


def get_customer_reimbursement_attachments(reimbursement_id):
    return _split_attachment_files.get_customer_reimbursement_attachments(reimbursement_id, db=db)


def save_customer_reimbursement_attachment(reimbursement_id, uploaded):
    return _split_attachment_files.save_customer_reimbursement_attachment(reimbursement_id, uploaded, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, customer_reimbursement_attachment_dir=customer_reimbursement_attachment_dir, db=db, g=g, now=now, os=os, secrets=secrets)


def save_customer_reimbursement_uploads(reimbursement_id):
    for uploaded in uploaded_attachments_from_request():
        save_customer_reimbursement_attachment(reimbursement_id, uploaded)


def unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename):
    return _split_attachment_files.unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename, db=db, normalized_attachment_filename=normalized_attachment_filename, os=os)


def copy_file_to_customer_reimbursement_attachment(
    reimbursement_id,
    source_path,
    original_filename,
    content_type=None,
    uploaded_by=None,
    source_expense_attachment_id=None,
):
    return _split_attachment_files.copy_file_to_customer_reimbursement_attachment(reimbursement_id, source_path, original_filename, content_type, uploaded_by, source_expense_attachment_id, DatabaseError=DatabaseError, IntegrityError=IntegrityError, app=app, customer_reimbursement_attachment_dir=customer_reimbursement_attachment_dir, db=db, g=g, now=now, os=os, secrets=secrets, shutil=shutil, unique_customer_reimbursement_attachment_filename=unique_customer_reimbursement_attachment_filename)


def copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, service_order_id):
    return _split_attachment_files.copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, service_order_id, copy_file_to_customer_reimbursement_attachment=copy_file_to_customer_reimbursement_attachment, db=db, report_attachment_path=report_attachment_path)


def customer_reimbursement_items_from_form(order_id=None):
    return _split_item_forms.customer_reimbursement_items_from_form(order_id, assert_contract_rates_covered=assert_contract_rates_covered, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, money_float=money_float, request=request, resolve_contract_rates=resolve_contract_rates, to_float=to_float, validate_fuel_reimbursement_allowed=validate_fuel_reimbursement_allowed)


def save_customer_reimbursement_items(reimbursement_id, rows):
    return _split_item_forms.save_customer_reimbursement_items(reimbursement_id, rows, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, db=db, json=json, money_decimal=money_decimal)


def latest_customer_reimbursement(order_id):
    return _split_invoice_items.latest_customer_reimbursement(order_id, db=db)


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
    return _split_invoice_items.customer_reimbursement_linked_invoice(reimbursement, order_id, db=db, service_order_active_invoice=service_order_active_invoice)


def customer_reimbursement_other_invoice_breakdown(reimbursement_id):
    return _split_invoice_items.customer_reimbursement_other_invoice_breakdown(reimbursement_id, customer_reimbursement_items=customer_reimbursement_items, expense_settlement_invoice_field=expense_settlement_invoice_field, json=json, money_decimal=money_decimal, money_float=money_float, normalized_project_name=normalized_project_name)


def customer_reimbursement_invoice_items(reimbursement):
    return _split_invoice_items.customer_reimbursement_invoice_items(reimbursement, CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS=CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS, MRO_CANONICAL_PROJECT_NAME=MRO_CANONICAL_PROJECT_NAME, _parse_quotation_revision_snapshot=_parse_quotation_revision_snapshot, canonical_project_mapping_key=canonical_project_mapping_key, customer_reimbursement_other_invoice_breakdown=customer_reimbursement_other_invoice_breakdown, db=db, is_mro_project_name=is_mro_project_name, money_decimal=money_decimal, money_float=money_float, normalize_settlement_mode=normalize_settlement_mode, project_name_key=project_name_key, quote_amounts=quote_amounts)


def create_customer_reimbursement(order, expense_selection_mode="legacy"):
    return _split_updates.create_customer_reimbursement(order, expense_selection_mode, assert_contract_rates_covered=assert_contract_rates_covered, copy_mileage_proofs_to_customer_reimbursement=copy_mileage_proofs_to_customer_reimbursement, customer_reimbursement_seed_rows=customer_reimbursement_seed_rows, db=db, g=g, now=now, save_customer_reimbursement_items=save_customer_reimbursement_items, secrets=secrets, update_customer_reimbursement_totals=update_customer_reimbursement_totals)


def sync_expense_attachments_to_settlement(order_id):
    return _split_updates.sync_expense_attachments_to_settlement(order_id, copy_file_to_customer_reimbursement_attachment=copy_file_to_customer_reimbursement_attachment, customer_reimbursement_attachment_dir=customer_reimbursement_attachment_dir, db=db, expense_attachment_path=expense_attachment_path, expense_settlement_invoice_field=expense_settlement_invoice_field, latest_customer_reimbursement=latest_customer_reimbursement, normalized_project_name=normalized_project_name, os=os, selected_expense_item_ids=selected_expense_item_ids)


def _quotation_totals_with_discount(reimbursement, totals):
    return _split_updates._quotation_totals_with_discount(reimbursement, totals, _parse_quotation_revision_snapshot=_parse_quotation_revision_snapshot, db=db, money_decimal=money_decimal, money_float=money_float, normalize_settlement_mode=normalize_settlement_mode, quote_amounts=quote_amounts, resolve_quote_settlement=resolve_quote_settlement)


def update_customer_reimbursement_totals(reimbursement_id, rows=None):
    return _split_updates.update_customer_reimbursement_totals(reimbursement_id, rows, _quotation_totals_with_discount=_quotation_totals_with_discount, approved_mro_supplies_total=approved_mro_supplies_total, customer_reimbursement_items=customer_reimbursement_items, customer_reimbursement_totals=customer_reimbursement_totals, db=db, merge_approved_expenses_into_customer_reimbursement=merge_approved_expenses_into_customer_reimbursement, merge_service_reports_into_customer_reimbursement=merge_service_reports_into_customer_reimbursement, save_customer_reimbursement_items=save_customer_reimbursement_items, selected_expense_total=selected_expense_total, sync_expense_attachments_to_settlement=sync_expense_attachments_to_settlement)


def ensure_customer_reimbursement_pdf_record(reimbursement, order):
    return _split_documents.ensure_customer_reimbursement_pdf_record(reimbursement, order, db=db, secrets=secrets)


def reimbursement_number(value):
    number = float(value or 0)
    return f"{number:,.2f}" if number else ""


def reimbursement_paragraph(value, style):
    text = "" if value is None else str(value)
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), style)


def build_customer_reimbursement_pdf(reimbursement, order, rows):
    return _split_documents.build_customer_reimbursement_pdf(reimbursement, order, rows, A4=A4, Decimal=Decimal, LongTable=LongTable, Paragraph=Paragraph, ParagraphStyle=ParagraphStyle, SimpleDocTemplate=SimpleDocTemplate, Spacer=Spacer, TA_CENTER=TA_CENTER, TA_LEFT=TA_LEFT, TableStyle=TableStyle, UnicodeCIDFont=UnicodeCIDFont, colors=colors, customer_reimbursement_file_path=customer_reimbursement_file_path, customer_reimbursement_item_expense_amount=customer_reimbursement_item_expense_amount, customer_reimbursement_totals=customer_reimbursement_totals, get_company_profile=get_company_profile, landscape=landscape, mm=mm, money=money, money_decimal=money_decimal, os=os, pdfmetrics=pdfmetrics, reimbursement_number=reimbursement_number, reimbursement_paragraph=reimbursement_paragraph)


def remove_customer_reimbursement_pdf(reimbursement):
    path = customer_reimbursement_file_path(reimbursement)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def ensure_customer_reimbursement_pdf_file(reimbursement, order):
    return _split_documents.ensure_customer_reimbursement_pdf_file(reimbursement, order, build_customer_reimbursement_pdf=build_customer_reimbursement_pdf, customer_reimbursement_file_path=customer_reimbursement_file_path, customer_reimbursement_items=customer_reimbursement_items, ensure_customer_reimbursement_pdf_record=ensure_customer_reimbursement_pdf_record, os=os)


def service_report_docx_filename(order, report, used_filenames=None):
    return _report_documents.service_report_docx_filename(order, report, used_filenames, os=os, secure_filename=secure_filename)


def service_order_report_word_attachments(order):
    return _report_documents.service_order_report_word_attachments(order, build_service_report_docx=build_service_report_docx, db=db, service_report_docx_filename=service_report_docx_filename)


def customer_reimbursement_outgoing_attachments(reimbursement, order):
    return _split_documents.customer_reimbursement_outgoing_attachments(reimbursement, order, build_customer_reimbursement_pdf=build_customer_reimbursement_pdf, customer_reimbursement_attachment_path=customer_reimbursement_attachment_path, customer_reimbursement_items=customer_reimbursement_items, ensure_customer_reimbursement_pdf_record=ensure_customer_reimbursement_pdf_record, get_customer_reimbursement_attachments=get_customer_reimbursement_attachments, os=os, service_order_report_word_attachments=service_order_report_word_attachments)


def deliver_customer_reimbursement_email(reimbursement, order):
    return _split_documents.deliver_customer_reimbursement_email(reimbursement, order, customer_reimbursement_outgoing_attachments=customer_reimbursement_outgoing_attachments, db=db, html=html, log_action=log_action, re=re, record_email_delivery=record_email_delivery, send_email=send_email)


def expense_beneficiary_id(expense):
    return _expense_access.expense_beneficiary_id(expense)


def expense_beneficiary_options(expense=None):
    return _expense_access.expense_beneficiary_options(expense, db=db, expense_beneficiary_id=expense_beneficiary_id, g=g)


def posted_expense_beneficiary(expense=None):
    return _expense_access.posted_expense_beneficiary(expense, expense_beneficiary_id=expense_beneficiary_id, expense_beneficiary_options=expense_beneficiary_options, g=g, request=request)


def expense_access_filter():
    return _expense_access.expense_access_filter(g=g, normalized_role=normalized_role)


def notify_expense_participants(expense, title, body, link):
    return _expense_access.notify_expense_participants(expense, title, body, link, create_message=create_message, expense_beneficiary_id=expense_beneficiary_id)


def can_access_expense(expense):
    return _expense_access.can_access_expense(expense, expense_beneficiary_id=expense_beneficiary_id, g=g, normalized_role=normalized_role)


def can_delete_expense(expense):
    return _expense_access.can_delete_expense(expense, expense_beneficiary_id=expense_beneficiary_id, g=g, is_manager=is_manager, normalized_role=normalized_role)


def require_expense(expense_id):
    return _expense_access.require_expense(expense_id, abort=abort, can_access_expense=can_access_expense, db=db, require_service_order=require_service_order)


def expense_attachment_dir(expense_id):
    return _expense_storage.expense_attachment_dir(expense_id, EXPENSE_ATTACHMENTS_DIR=EXPENSE_ATTACHMENTS_DIR, os=os)


def expense_attachment_path(attachment):
    return _expense_storage.expense_attachment_path(attachment, EXPENSE_ATTACHMENTS_DIR=EXPENSE_ATTACHMENTS_DIR, os=os)


def expense_attachment_fingerprints(path):
    return _expense_storage.expense_attachment_fingerprints(path, Image=Image, ImageOps=ImageOps, hashlib=hashlib)


def image_hash_distance(left, right):
    return _expense_duplicates.image_hash_distance(left, right)


def save_expense_attachment(expense_id, uploaded, expense_item_key=None):
    return _expense_storage.save_expense_attachment(expense_id, uploaded, expense_item_key, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, db=db, expense_attachment_dir=expense_attachment_dir, expense_attachment_fingerprints=expense_attachment_fingerprints, g=g, now=now, os=os, secrets=secrets)


def save_expense_uploads(expense_id, item_rows=None):
    return _expense_storage.save_expense_uploads(expense_id, item_rows, request=request, save_expense_attachment=save_expense_attachment, uploaded_attachments_from_request=uploaded_attachments_from_request)


def get_expense_attachments(expense_id):
    return _expense_storage.get_expense_attachments(expense_id, db=db)


def expense_attachment_duplicate_context(attachment_id):
    return _expense_duplicates.expense_attachment_duplicate_context(attachment_id, db=db)


def ensure_expense_attachment_fingerprints(attachment):
    return _expense_duplicates.ensure_expense_attachment_fingerprints(attachment, db=db, expense_attachment_fingerprints=expense_attachment_fingerprints, expense_attachment_path=expense_attachment_path)


def deepseek_duplicate_analysis(current, matched, reasons):
    return _expense_duplicates.deepseek_duplicate_analysis(current, matched, reasons, call_deepseek_chat=call_deepseek_chat, deepseek_assistant_settings=deepseek_assistant_settings, json=json)


def run_expense_duplicate_checks(expense_id, use_deepseek=False):
    return _expense_duplicates.run_expense_duplicate_checks(expense_id, use_deepseek, date=date, db=db, deepseek_duplicate_analysis=deepseek_duplicate_analysis, ensure_expense_attachment_fingerprints=ensure_expense_attachment_fingerprints, expense_attachment_duplicate_context=expense_attachment_duplicate_context, image_hash_distance=image_hash_distance, now=now)


def expense_duplicate_checks(expense_id):
    return _expense_duplicates.expense_duplicate_checks(expense_id, db=db)


def service_report_workers(report_id):
    return _report_write_services.service_report_workers(report_id, db=db)


def service_report_worker_options(order, report_id=None):
    return _report_write_services.service_report_worker_options(order, report_id, db=db, g=g, is_external_employee=is_external_employee)


def report_writer_options():
    return _report_write_services.report_writer_options(db=db)


def group_expense_attachments(attachments):
    return _expense_storage.group_expense_attachments(attachments)


def posted_report_writer_id():
    return _report_write_services.posted_report_writer_id(db=db, normalize_customer_report_choice=normalize_customer_report_choice, request=request)


def report_parts(table, report_id):
    return _report_write_services.report_parts(table, report_id, db=db)


def parse_part_rows(prefix, fields):
    return _report_write_services.parse_part_rows(prefix, fields, request=request)


def report_form_defaults(report=None, order=None):
    return _report_write_services.report_form_defaults(report, order, date=date)


def save_report_detail_rows(report_id):
    return _report_write_services.save_report_detail_rows(report_id, db=db, parse_part_rows=parse_part_rows, report_worker_rows_from_form=report_worker_rows_from_form)


def load_invoice(invoice_id):
    return _split_invoice_loading.load_invoice(invoice_id, db=db, require_invoice_access=require_invoice_access)


def invoice_service_order(invoice):
    return _split_invoice_loading.invoice_service_order(invoice, db=db)


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


def record_email_delivery(entity_type, entity_id, recipient, subject, *,
                          status=None, error_message=None, employee_id=None):
    return _split_delivery_record.record_email_delivery(entity_type, entity_id, recipient, subject, status=status, error_message=error_message, employee_id=employee_id, db=db, g=g, now=now)


def email_delivery_summary(entity_type, entity_id):
    return _split_delivery_summary.email_delivery_summary(entity_type, entity_id, db=db)


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
            f"角色：{role_label(user['role'])}；IP：{request_ip_address() or '-'}",
            now(),
        ),
    )


def normalized_address(value):
    return " ".join(str(value or "").strip().split()).casefold()


def parse_coordinate_pair(value):
    text = str(value or "").strip()
    if not text:
        return None
    parts = [part for part in re.split(r"[,，\s]+", text) if part]
    if len(parts) != 2:
        raise ValueError("请一次粘贴“纬度, 经度”，例如：33.3252078, -112.7639562。")
    try:
        latitude, longitude = (float(part) for part in parts)
    except ValueError as error:
        raise ValueError("经纬度只能包含数字，请从 Google 地图复制后直接粘贴。") from error
    if not -90 <= latitude <= 90:
        raise ValueError("纬度必须在 -90 到 90 之间。")
    if not -180 <= longitude <= 180:
        raise ValueError("经度必须在 -180 到 180 之间。")
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
    action = "重新提交了发票" if is_resubmission else "提交了发票"
    client_label = client["short_name"] or client["name"]
    return f"{user_name}{action} {invoice['invoice_number']} 客户:{client_label} 金额:{money(total, invoice['currency'])} 请审核。"


def return_message_body(invoice, client, total, reason):
    client_label = client["short_name"] or client["name"]
    return f"发票 {invoice['invoice_number']} 已被经理退回。客户:{client_label} 金额:{money(total, invoice['currency'])} 原因：{reason}"


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
        "deployment_env": app.config["DEPLOYMENT_ENV"],
        "production_postgres": True,
    }


_views_authentication = _split_authentication.register_authentication_routes(
    app,
    _cb_resolve_DEFAULT_LANGUAGE=lambda: DEFAULT_LANGUAGE,
    _cb_resolve_IntegrityError=lambda: IntegrityError,
    _cb_resolve_SUPPORTED_LANGUAGES=lambda: SUPPORTED_LANGUAGES,
    _cb_resolve_check_password_hash=lambda: check_password_hash,
    _cb_resolve_clear_session_preserving_language=lambda: clear_session_preserving_language,
    _cb_resolve_communication_languages_from_form=lambda: communication_languages_from_form,
    _cb_resolve_country_from_form=lambda: country_from_form,
    _cb_resolve_country_rows=lambda: country_rows,
    _cb_resolve_current_language=lambda: current_language,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_generate_password_hash=lambda: generate_password_hash,
    _cb_resolve_is_safe_redirect_target=lambda: is_safe_redirect_target,
    _cb_resolve_log_login_action=lambda: log_login_action,
    _cb_resolve_normalize_phone=lambda: normalize_phone,
    _cb_resolve_notify_role=lambda: notify_role,
    _cb_resolve_now=lambda: now,
    _cb_resolve_re=lambda: re,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_session=lambda: session,
    _cb_resolve_url_for=lambda: url_for,
)
login = _views_authentication['login']
register = _views_authentication['register']


_views_mobile_install = _split_mobile_install.register_mobile_install_routes(
    app,
    _cb_resolve_IOS_APP_VERSION=lambda: IOS_APP_VERSION,
    _cb_resolve_IOS_BUNDLE_ID=lambda: IOS_BUNDLE_ID,
    _cb_resolve_IOS_IPA_PATH=lambda: IOS_IPA_PATH,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_app=lambda: app,
    _cb_resolve_html=lambda: html,
    _cb_resolve_os=lambda: os,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_url_for=lambda: url_for,
    _cb_resolve_urlencode=lambda: urlencode,
)
mobile_app_install = _views_mobile_install['mobile_app_install']
mobile_app_manifest = _views_mobile_install['mobile_app_manifest']




_views_mobile_package = _split_mobile_install.register_mobile_package_routes(
    app,
    _cb_resolve_IOS_IPA_PATH=lambda: IOS_IPA_PATH,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_os=lambda: os,
    _cb_resolve_send_file=lambda: send_file,
)
mobile_app_ipa = _views_mobile_package['mobile_app_ipa']




_views_language_session = _split_language_session.register_language_session_routes(
    app,
    _cb_resolve_SUPPORTED_LANGUAGES=lambda: SUPPORTED_LANGUAGES,
    _cb_resolve_clear_session_preserving_language=lambda: clear_session_preserving_language,
    _cb_resolve_g=lambda: g,
    _cb_resolve_parse_qsl=lambda: parse_qsl,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_request=lambda: request,
    _cb_resolve_session=lambda: session,
    _cb_resolve_url_for=lambda: url_for,
    _cb_resolve_urlencode=lambda: urlencode,
    _cb_resolve_urlsplit=lambda: urlsplit,
    _cb_resolve_urlunsplit=lambda: urlunsplit,
)
logout = _views_language_session['logout']
switch_language = _views_language_session['switch_language']




_views_messages_list = _split_messages_list.register_messages_list_routes(
    app,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
)
messages = _views_messages_list['messages']


def _message_ids_for_user(raw_ids):
    """把表单里的 id 收敛成当前用户自己的消息 id。

    消息是「每人一份」的私有数据（`where user_id = ?` 就是权限边界），
    所以这里必须再过滤一次 —— 不能因为前端只传自己的 id 就信任它。
    非数字、越界的 id 一律丢弃，不报错。
    """
    ids = []
    for value in raw_ids:
        try:
            message_id = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            ids.append(message_id)
    if not ids:
        return []
    # 去重后保持稳定顺序，便于日志与测试断言。
    unique = sorted(set(ids))
    placeholders = ",".join("?" for _ in unique)
    owned = db().execute(
        f"select id from messages where user_id = ? and id in ({placeholders})",
        (g.user["id"], *unique),
    ).fetchall()
    return [row["id"] for row in owned]


_views_messages_mark = _split_messages_list.register_messages_mark_routes(
    app,
    _cb_resolve__message_ids_for_user=lambda: _message_ids_for_user,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_request=lambda: request,
    _cb_resolve_url_for=lambda: url_for,
)
mark_messages = _views_messages_mark['mark_messages']


_views_messages_read = _split_messages_list.register_messages_read_routes(
    app,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_url_for=lambda: url_for,
)
mark_all_messages_read = _views_messages_read['mark_all_messages_read']


_views_message_detail = _split_messages_list.register_message_detail_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_url_for=lambda: url_for,
)
message_detail = _views_message_detail['message_detail']


_views_pwa_manifest = _split_pwa_manifest.register_pwa_manifest_routes(
    app,
    _cb_resolve_jsonify=lambda: jsonify,
)
app_manifest = _views_pwa_manifest['app_manifest']


_views_service_worker = _split_pwa_manifest.register_service_worker_routes(
    app,
    _cb_resolve_app=lambda: app,
)
app_service_worker = _views_service_worker['app_service_worker']


_views_workspace_pages = _split_workspace_pages.register_workspace_pages_routes(
    app,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_url_for=lambda: url_for,
)
workspace = _views_workspace_pages['workspace']
report_center = _views_workspace_pages['report_center']


_views_dashboard_page = _split_workspace_pages.register_dashboard_page_routes(
    app,
    _cb_resolve_STATUS_LABELS=lambda: STATUS_LABELS,
    _cb_resolve_can_view_invoices=lambda: can_view_invoices,
    _cb_resolve_client_filter_clause=lambda: client_filter_clause,
    _cb_resolve_dashboard_project_options=lambda: dashboard_project_options,
    _cb_resolve_db=lambda: db,
    _cb_resolve_get_metrics=lambda: get_metrics,
    _cb_resolve_is_external_user=lambda: is_external_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_monthly_paid_chart=lambda: monthly_paid_chart,
    _cb_resolve_monthly_project_chart=lambda: monthly_project_chart,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_url_for=lambda: url_for,
)
dashboard = _views_dashboard_page['dashboard']


_contract_views = register_contract_routes(
    app,
    CONTRACT_STATUS_LABELS=CONTRACT_STATUS_LABELS,
    CONTRACT_TYPE_LABELS=CONTRACT_TYPE_LABELS,
    STATUS_LABELS=STATUS_LABELS,
    can_manage_contracts=lambda *args, **kwargs: can_manage_contracts(*args, **kwargs),
    can_view_contracts=lambda *args, **kwargs: can_view_contracts(*args, **kwargs),
    contract_attachment_path=lambda *args, **kwargs: contract_attachment_path(*args, **kwargs),
    contract_form_values=lambda *args, **kwargs: contract_form_values(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    ensure_commercial_document=lambda *args, **kwargs: ensure_commercial_document(*args, **kwargs),
    is_external_manager=lambda *args, **kwargs: is_external_manager(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    next_contract_number=lambda *args, **kwargs: next_contract_number(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
    require_contract_access=lambda *args, **kwargs: require_contract_access(*args, **kwargs),
    safe_attachment_response=lambda *args, **kwargs: safe_attachment_response(*args, **kwargs),
    save_contract_uploads=lambda *args, **kwargs: save_contract_uploads(*args, **kwargs),
    contract_attachments_root=lambda: CONTRACT_ATTACHMENTS_DIR,
    login_required=login_required,
)
contracts = _contract_views['contracts']
new_contract = _contract_views['new_contract']
contract_detail = _contract_views['contract_detail']
edit_contract = _contract_views['edit_contract']
delete_contract = _contract_views['delete_contract']
preview_contract_attachment = _contract_views['preview_contract_attachment']
download_contract_attachment = _contract_views['download_contract_attachment']
delete_contract_attachment = _contract_views['delete_contract_attachment']


def payment_term_rows(active_only=False):
    return _payment_term_services["payment_term_rows"](active_only)


def default_payment_term_id():
    return _payment_term_services["default_payment_term_id"]()


def posted_payment_term_id(allow_inactive=False):
    return _payment_term_services["posted_payment_term_id"](allow_inactive)


def shifted_month(year, month, offset):
    return _payment_term_calculations.shifted_month(year, month, offset)


def calculate_payment_due_date(issue_date_value, payment_term):
    return _payment_term_calculations.calculate_payment_due_date(
        issue_date_value, payment_term, month_shifter=shifted_month)


def payment_term_description(payment_term):
    return _payment_term_calculations.payment_term_description(payment_term)


def payment_term_form_values():
    return _payment_term_services["payment_term_form_values"]()


def eligible_invoice_due_date_changes():
    return _payment_term_services["eligible_invoice_due_date_changes"]()


_payment_term_services = build_payment_term_services(
    db=lambda: db(),
    resolve_default_payment_term_id=lambda: default_payment_term_id(),
    resolve_calculate_payment_due_date=lambda *args: calculate_payment_due_date(*args),
)
_payment_term_route_exports = register_payment_term_routes(
    app,
    db=lambda: db(),
    now=lambda: now(),
    login_required=login_required,
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    payment_term_form_values=lambda: payment_term_form_values(),
    payment_term_rows=lambda *args, **kwargs: payment_term_rows(*args, **kwargs),
    eligible_invoice_due_date_changes=lambda: eligible_invoice_due_date_changes(),
    payment_term_description=lambda term: payment_term_description(term),
)
payment_terms = _payment_term_route_exports["payment_terms"]
edit_payment_term = _payment_term_route_exports["edit_payment_term"]
toggle_payment_term = _payment_term_route_exports["toggle_payment_term"]
recalculate_payment_term_invoices = _payment_term_route_exports["recalculate_payment_term_invoices"]


_views_system_settings = _split_system_settings.register_system_settings_routes(
    app,
    _cb_resolve_ACTION_LABELS=lambda: ACTION_LABELS,
    _cb_resolve_DEFAULT_SMTP_SETTINGS=lambda: DEFAULT_SMTP_SETTINGS,
    _cb_resolve_DatabaseError=lambda: DatabaseError,
    _cb_resolve_GOOGLE_PLACES_API_KEY_ENV=lambda: GOOGLE_PLACES_API_KEY_ENV,
    _cb_resolve_MENU_PERMISSION_GROUPS=lambda: MENU_PERMISSION_GROUPS,
    _cb_resolve_ROLE_ACTION_PERMISSION_GROUPS=lambda: ROLE_ACTION_PERMISSION_GROUPS,
    _cb_resolve_ROLE_OPTIONS=lambda: ROLE_OPTIONS,
    _cb_resolve_admin_required=lambda: admin_required,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_get_company_profile=lambda: get_company_profile,
    _cb_resolve_get_google_geocoding_api_key=lambda: get_google_geocoding_api_key,
    _cb_resolve_get_google_maps_browser_api_key=lambda: get_google_maps_browser_api_key,
    _cb_resolve_get_invoice_terms=lambda: get_invoice_terms,
    _cb_resolve_get_payment_instructions=lambda: get_payment_instructions,
    _cb_resolve_get_setting=lambda: get_setting,
    _cb_resolve_get_smtp_settings=lambda: get_smtp_settings,
    _cb_resolve_inspection_cycle_days=lambda: inspection_cycle_days,
    _cb_resolve_inspection_warning_days=lambda: inspection_warning_days,
    _cb_resolve_is_read_sql=lambda: is_read_sql,
    _cb_resolve_llm_config=lambda: llm_config,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_now=lambda: now,
    _cb_resolve_permission_tree_groups=lambda: permission_tree_groups,
    _cb_resolve_positive_int=lambda: positive_int,
    _cb_resolve_re=lambda: re,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_set_setting=lambda: set_setting,
    _cb_resolve_sql_statement_kind=lambda: sql_statement_kind,
    _cb_resolve_url_for=lambda: url_for,
)
system_settings = _views_system_settings['system_settings']
menu_permissions = _views_system_settings['menu_permissions']
database_console = _views_system_settings['database_console']


AI_SEARCH_DOMAINS = {
    "expenses", "service_orders", "sites", "invoices",
    "daily_reports", "settlements", "knowledge",
}


def deepseek_assistant_settings():
    try:
        cfg = llm_config.get_config(db(), "daily_intent")
    except ValueError:
        return {"enabled": False, "api_key": "", "model": "", "base_url": ""}
    return {
        "enabled": True,
        "api_key": cfg["api_key"] or DEEPSEEK_API_KEY_ENV,
        "model": cfg["model"],
        "base_url": cfg["base_url"],
    }


def vision_settings():
    """Vision API settings for Phase 5 photo classification.

    Privacy: VISION_EXTERNAL_API_ENABLED controls whether photos are sent to external API.
    If disabled, classification_status = disabled, no photo uploaded externally.
    Default model: deepseek-flash (from DEEPSEEK_VISION_MODEL env, not hardcoded).
    """
    try:
        cfg = llm_config.get_config(db(), "daily_vision")
    except ValueError:
        cfg = {"api_key": "", "model": "", "base_url": ""}
    return {
        "enabled": get_setting("vision_external_api_enabled", "false") == "true" and bool(cfg["model"]),
        "api_key": cfg["api_key"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "safety_auto_select_confidence": float(get_setting("safety_auto_select_confidence", "0.80")),
        "safety_verify_confidence": float(get_setting("safety_verify_confidence", "0.60")),
        "max_service_photos": int(get_setting("max_service_photos", "10")),
        "vision_max_image_size": int(get_setting("vision_max_image_size", "1280")),
        "vision_max_image_bytes": int(get_setting("vision_max_image_bytes", str(VISION_MAX_IMAGE_BYTES_ENV))),
    }


def ai_search_like(value):
    return f"%{str(value or '').strip()}%"


def ai_normalize_status(domain, value):
    raw = str(value or "").strip().lower()
    aliases = {
        "service_orders": {"进行中": "open", "未关闭": "open", "open": "open", "已关闭": "closed", "closed": "closed"},
        "expenses": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已通过": "approved", "approved": "approved",
            "待付款": "pending", "未付款": "pending", "待报销": "pending", "pending": "pending",
            "已付款": "paid", "已报销": "paid", "paid": "paid",
        },
        "invoices": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已完成": "completed", "completed": "completed",
            "作废": "void", "void": "void",
        },
        "settlements": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已通过": "approved", "approved": "approved",
        },
    }
    return aliases.get(domain, {}).get(raw, raw)


def ai_search_business_records(arguments):
    if not isinstance(arguments, dict):
        return {"error": "查询参数格式不正确。"}
    domain = str(arguments.get("domain") or "").strip()
    if domain not in AI_SEARCH_DOMAINS:
        return {"error": "不支持的数据类型。"}
    query = str(arguments.get("query") or "").strip()
    status = ai_normalize_status(domain, arguments.get("status"))
    date_from = str(arguments.get("date_from") or "").strip()
    date_to = str(arguments.get("date_to") or "").strip()
    clauses, params = [], []

    if domain == "expenses":
        if not has_action_permission("expenses", "view"):
            return {"error": "当前用户没有查看员工报销的权限。"}
        access_clause, access_params = expense_access_filter()
        clauses.append(access_clause)
        params.extend(access_params)
        if query:
            clauses.append("(expenses.expense_number like ? or expenses.project like ? or service_orders.order_number like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status:
            clauses.append("(expenses.status = ? or expenses.payout_status = ?)")
            params.extend([status, status])
        if date_from:
            clauses.append("expenses.expense_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("expenses.expense_date <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select expenses.id, expenses.expense_number, expenses.expense_date, expenses.project,
                   expenses.amount, expenses.currency, expenses.status, expenses.payout_status,
                   users.name as submitter, beneficiaries.name as beneficiary, service_orders.order_number
            from expenses join service_orders on service_orders.id = expenses.service_order_id
            join users on users.id = expenses.created_by
            left join users as beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
            where {where} order by expenses.expense_date desc, expenses.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(expenses.amount), 0) as amount
                from expenses join service_orders on service_orders.id = expenses.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {
            "summary": {"count": total["count"], "amount": total["amount"]},
            "records": [dict(row) | {"url": url_for("expense_detail", expense_id=row["id"], _external=True)} for row in rows],
        }

    if domain == "service_orders":
        if not has_action_permission("service_orders", "view"):
            return {"error": "当前用户没有查看工单的权限。"}
        access_clauses, access_params = service_order_access_filters("service_orders")
        clauses.extend(access_clauses)
        params.extend(access_params)
        if query:
            clauses.append("(order_number like ? or client_order_number like ? or client_name like ? or site_address like ?)")
            params.extend([ai_search_like(query)] * 4)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from:
            clauses.append("coalesce(start_date, created_at) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("coalesce(start_date, created_at) <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""select id, order_number, client_order_number, client_name, site_address, status, start_date
                from service_orders where {where}
                order by coalesce(start_date, created_at) desc, id desc limit 25""",
            params,
        ).fetchall()
        total = db().execute(f"select count(*) as count from service_orders where {where}", params).fetchone()
        return {"count": total["count"], "records": [dict(row) | {"url": url_for("service_order_detail", order_id=row["id"], _external=True)} for row in rows]}

    if domain == "sites":
        if not has_action_permission("buyers", "view"):
            return {"error": "当前用户没有查看站点的权限。"}
        if query:
            clauses.append("(buyer_number like ? or name like ? or detailed_address like ?)")
            params.extend([ai_search_like(query)] * 3)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""select id, buyer_number, name, detailed_address, latitude, longitude, email
                from buyers where {where} order by buyer_number limit 25""",
            params,
        ).fetchall()
        total = db().execute(f"select count(*) as count from buyers where {where}", params).fetchone()
        return {"count": total["count"], "records": [dict(row) | {"url": url_for("buyers", _external=True)} for row in rows]}

    if domain == "invoices":
        if not can_view_invoices():
            return {"error": "当前用户没有查看发票的权限。"}
        if query:
            clauses.append("(invoices.invoice_number like ? or clients.name like ? or service_orders.order_number like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status in {"待核销", "unpaid"}:
            clauses.append("invoices.paid_at is null")
        elif status in {"已核销", "paid"}:
            clauses.append("invoices.paid_at is not null")
        elif status:
            clauses.append("invoices.status = ?")
            params.append(status)
        if date_from:
            clauses.append("invoices.issue_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("invoices.issue_date <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select invoices.id, invoices.invoice_number, invoices.issue_date, invoices.due_date,
                   invoices.status, invoices.paid_at, clients.name as client_name,
                   service_orders.order_number, coalesce(sum(invoice_items.amount), 0) as amount
            from invoices join clients on clients.id = invoices.client_id
            left join service_orders on service_orders.id = invoices.service_order_id
            left join invoice_items on invoice_items.invoice_id = invoices.id
            where {where} group by invoices.id, clients.id, service_orders.id
            order by invoices.issue_date desc, invoices.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(distinct invoices.id) as count, coalesce(sum(invoice_items.amount), 0) as amount
                from invoices join clients on clients.id = invoices.client_id
                left join service_orders on service_orders.id = invoices.service_order_id
                left join invoice_items on invoice_items.invoice_id = invoices.id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "amount": total["amount"]}, "records": [dict(row) | {"url": url_for("invoice_detail", invoice_id=row["id"], _external=True)} for row in rows]}

    if domain == "daily_reports":
        if not has_action_permission("service_reports", "view"):
            return {"error": "当前用户没有查看工作日报的权限。"}
        access_clauses, access_params = service_order_access_filters("service_orders")
        clauses.extend(access_clauses)
        params.extend(access_params)
        if query:
            clauses.append("(service_orders.order_number like ? or service_orders.client_order_number like ? or service_reports.service_description like ?)")
            params.extend([ai_search_like(query)] * 3)
        if date_from:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select service_reports.id, service_reports.report_date, service_reports.actual_work_date,
                   service_reports.total_service_hours, service_reports.service_description,
                   service_orders.id as order_id, service_orders.order_number, service_orders.client_order_number
            from service_reports join service_orders on service_orders.id = service_reports.service_order_id
            where {where} order by coalesce(actual_work_date, report_date) desc, service_reports.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(service_reports.total_service_hours), 0) as service_hours
                from service_reports join service_orders on service_orders.id = service_reports.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "service_hours": total["service_hours"]}, "records": [dict(row) | {"url": url_for("service_order_detail", order_id=row["order_id"], _external=True)} for row in rows]}

    if domain == "settlements":
        if not can_view_customer_reimbursement():
            return {"error": "当前用户没有查看工单结算的权限。"}
        if query:
            clauses.append("(service_orders.order_number like ? or service_orders.client_order_number like ? or service_orders.client_name like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status:
            clauses.append("customer_reimbursements.status = ?")
            params.append(status)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select customer_reimbursements.id, customer_reimbursements.status,
                   customer_reimbursements.total_amount, customer_reimbursements.created_at,
                   service_orders.id as order_id, service_orders.order_number,
                   service_orders.client_order_number, service_orders.client_name
            from customer_reimbursements
            join service_orders on service_orders.id = customer_reimbursements.service_order_id
            where {where} order by customer_reimbursements.created_at desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(customer_reimbursements.total_amount), 0) as amount
                from customer_reimbursements
                join service_orders on service_orders.id = customer_reimbursements.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "amount": total["amount"]}, "records": [dict(row) | {"url": url_for("customer_reimbursement_form", order_id=row["order_id"], _external=True)} for row in rows]}

    if not has_action_permission("knowledge_base", "view"):
        return {"error": "当前用户没有查看知识库的权限。"}
    if query:
        clauses.append("(title like ? or category like ? or description like ? or original_filename like ? or search_text like ?)")
        params.extend([ai_search_like(query)] * 5)
    where = " and ".join(clauses) if clauses else "1 = 1"
    rows = db().execute(
        f"""select id, title, category, description, original_filename,
                   substr(search_text, 1, 1200) as text_excerpt, updated_at
            from knowledge_documents where {where}
            order by is_pinned desc, updated_at desc limit 15""",
        params,
    ).fetchall()
    total = db().execute(f"select count(*) as count from knowledge_documents where {where}", params).fetchone()
    return {"count": total["count"], "records": [dict(row) | {"url": url_for("preview_knowledge_document", document_id=row["id"], _external=True)} for row in rows]}


AI_ASSISTANT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_business_records",
            "description": "只读查询当前登录用户有权限查看的业务数据。需要数据事实时必须调用，不能猜测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": sorted(AI_SEARCH_DOMAINS)},
                    "query": {"type": "string", "description": "编号、名称、客户、站点或关键词"},
                    "status": {"type": "string", "description": "可选状态，可传中文（如进行中、已关闭、待审核、已通过、待报销、已报销、待核销）"},
                    "date_from": {"type": "string", "description": "可选，YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "可选，YYYY-MM-DD"},
                },
                "required": ["domain"],
            },
        },
    }
]


def deepseek_api_error_message(status_code, response_body):
    api_message = ""
    try:
        error_payload = json.loads(response_body or "{}")
        api_error = error_payload.get("error") if isinstance(error_payload, dict) else None
        if isinstance(api_error, dict):
            api_message = str(api_error.get("message") or "").strip()
        elif api_error:
            api_message = str(api_error).strip()
    except json.JSONDecodeError:
        pass
    friendly = {
        400: "请求参数不被 DeepSeek 接受",
        401: "API Key 无效或已失效",
        402: "DeepSeek 账户余额不足",
        403: "当前 API Key 没有调用权限",
        429: "DeepSeek 请求过于频繁，请稍后重试",
        500: "DeepSeek 服务内部错误",
        503: "DeepSeek 服务暂时繁忙",
    }.get(status_code, f"DeepSeek API 返回 HTTP {status_code}")
    return f"{friendly}：{api_message}" if api_message else friendly


def call_deepseek_chat(messages, settings, include_tools=True, max_tokens=1600):
    request_payload = {
        "model": settings["model"],
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if include_tools:
        request_payload["tools"] = AI_ASSISTANT_TOOLS
        request_payload["tool_choice"] = "auto"
    payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    _base = (settings.get("base_url") or "https://api.deepseek.com").rstrip("/")
    api_request = Request(
        _base + "/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(api_request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(deepseek_api_error_message(error.code, detail)) from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"服务器无法连接 DeepSeek API：{error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("DeepSeek API 返回了无法解析的数据。") from error
    if not isinstance(result, dict):
        raise RuntimeError("DeepSeek API 没有返回有效的 JSON 对象。")
    choices = result.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise RuntimeError("DeepSeek API 没有返回有效回答。")
    return choices[0]["message"]


_views_assistant = _split_assistant.register_assistant_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_admin_required=lambda: admin_required,
    _cb_resolve_ai_search_business_records=lambda: ai_search_business_records,
    _cb_resolve_call_deepseek_chat=lambda: call_deepseek_chat,
    _cb_resolve_date=lambda: date,
    _cb_resolve_db=lambda: db,
    _cb_resolve_deepseek_assistant_settings=lambda: deepseek_assistant_settings,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_json=lambda: json,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_llm_config=lambda: llm_config,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_normalized_role=lambda: normalized_role,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
)
deepseek_connection_test = _views_assistant['deepseek_connection_test']
ai_assistant = _views_assistant['ai_assistant']
ai_assistant_chat = _views_assistant['ai_assistant_chat']






# ─── AI Daily Report (Phase 1) ────────────────────────────────────────────

def _ai_daily_report_service():
    """Build a DailyReportService bound to current request context."""
    return DailyReportService(db(), now, g.user["id"], g.user["name"])


def _ai_daily_report_business_date() -> str:
    """Get current business date in app timezone."""
    return datetime.now(app_timezone()).date().isoformat()


def _auto_prepare_draft_media(draft_id: int) -> list:
    """Best-effort auto pipeline for a draft — no manual clicks required.

    Runs, in order (each step skipped when already done, degrades gracefully):
      1. discover_photos: scan site photos and build timeline candidates
         (only when the timeline has not been scanned yet).
      2. confirm_timeline: apply arrival/departure photo candidates
         (force-confirmed; SKIPPED when the user set times manually via
         user_input/manual sources, so human edits are never overwritten).
      3. classify_photos: Vision-classify photos and auto-select safety +
         construction (service) photos (only when not classified yet and
         Vision is enabled; user selections/removals are preserved).
      4. recalculate_mileage: for self_drive workers lacking a successful
         route — compute mileage via Google Routes and generate Static Maps
         evidence records.

    Returns a list of {"step", "ok", ...} summaries; never raises.
    """
    steps = []
    svc = _ai_daily_report_service()

    def _load_draft():
        row = svc.get_draft(draft_id)
        return svc.parse_draft_data(row) if row else None

    def _run(step_name, fn):
        try:
            info = fn() or {}
            db().commit()
            steps.append({"step": step_name, "ok": True, **info})
        except Exception as exc:  # auto pipeline must never break the caller
            try:
                db().rollback()
            except Exception:
                pass
            steps.append({"step": step_name, "ok": False, "error": str(exc)})

    draft_row = svc.get_draft(draft_id)
    if not draft_row or draft_row["status"] not in {"draft", "confirmed"}:
        return steps
    draft = _load_draft()

    # 1) Discover photos (only if not scanned yet)
    if draft.photo_timeline_status in (None, "", "not_scanned", "failed"):
        def _discover():
            photo_discovery = PhotoDiscoveryService(
                shared_photos_root=SHARED_PHOTOS_DIR,
                service_order_photo_folder_func=service_order_photo_folder,
            )
            photo_metadata = PhotoMetadataService(shared_photos_root=SHARED_PHOTOS_DIR)
            try:
                fp_rows = db().execute(
                    "select relative_path, photo_type from field_photos"
                ).fetchall()
                fp_map = {r["relative_path"]: (r["photo_type"] or "") for r in fp_rows}
            except Exception:
                fp_map = {}
            result = svc.discover_photos_for_draft(
                draft_id=draft_id,
                photo_discovery_service=photo_discovery,
                photo_metadata_service=photo_metadata,
                photo_type_lookup=lambda p: fp_map.get(p) or None,
            )
            return {"photo_count": result.get("photo_count")}
        _run("discover_photos", _discover)
        draft = _load_draft()

    # 2) Confirm photo timeline (skip when user set times manually)
    manual_times = draft.arrival_time_source in ("user_input", "manual") or (
        draft.departure_time_source in ("user_input", "manual")
    )
    if (
        not manual_times
        and draft.arrival_time_source != "photo_timeline_confirmed"
        and draft.photo_timeline_status
        not in (None, "", "not_scanned", "no_photos", "failed")
    ):
        _run("confirm_timeline", lambda: svc.confirm_photo_timeline(draft_id, force=True))

    # 3) Vision classify + auto-select safety/construction photos
    draft_row = svc.get_draft(draft_id)
    draft = _load_draft()
    vision_cfg = vision_settings()
    if (
        draft_row["status"] == "draft"
        and vision_cfg.get("enabled")
        and draft.photo_candidates
        and not draft.photo_analysis_results
    ):
        def _classify():
            photo_svc = _make_photo_classification_service()
            result = svc.classify_draft_photos(
                draft_id=draft_id,
                photo_classification_service=photo_svc,
                analysis_model=vision_cfg.get("model", ""),
            )
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "classification_failed"))
            return {
                "selected_service_count": result.get("selected_service_count"),
                "selected_safety": result.get("selected_safety"),
            }
        _run("classify_photos", _classify)

    # 4) Recalculate mileage for self_drive workers without a successful route
    draft = _load_draft()
    if any(
        w.transportation == "self_drive" and w.route_status != "success"
        for w in draft.workers
    ):
        def _mileage():
            try:
                order = require_service_order(draft.service_order_id)
            except Exception:
                return {"skipped": "order_not_found"}
            site_address = (order["site_address"] or "") if order else ""
            emp_resolver = EmployeeResolutionService(db(), g.user["id"], g.user["name"])
            travel_svc = TravelService(db(), emp_resolver)
            travel_svc.set_destination_for_all(draft.workers, site_address)
            missing, _msgs = travel_svc.verify_travel_fields(
                draft.workers, bool(site_address.strip())
            )
            routes_api_key = get_google_routes_api_key()
            if missing:
                return {"skipped": "missing_travel_fields"}
            if not routes_api_key:
                return {"skipped": "no_routes_api_key"}

            from ai_daily_report import (
                GoogleRoutesService,
                MileageService,
                GoogleStaticMapsService,
                MileageEvidenceService,
            )
            routes_svc = GoogleRoutesService(routes_api_key)
            mileage_svc = MileageService(routes_svc)
            for w in draft.workers:
                if w.transportation == "self_drive" and w.route_status != "success":
                    TravelService.invalidate_worker_route(w)
            mileage_svc.calculate_for_all(draft.workers)

            static_maps_key = get_google_static_maps_api_key()
            if static_maps_key:
                evidence_svc = MileageEvidenceService(
                    GoogleStaticMapsService(static_maps_key),
                    os.path.join(DATA_DIR, "ai-daily-report-drafts"),
                )
                for w in draft.workers:
                    if w.transportation == "self_drive" and w.route_status == "success":
                        record = evidence_svc.generate_evidence(
                            w,
                            draft_id=draft_id,
                            service_order_id=draft.service_order_id,
                            report_date=draft.report_date,
                            generated_by=g.user["id"],
                            draft_evidence_records=draft.evidence_records,
                        )
                        draft.evidence_records = MileageEvidenceService.upsert_evidence_record(
                            draft.evidence_records, record
                        )
                        w.mileage_evidence_id = record.evidence_id
                        w.mileage_evidence_path = record.file_relative_path
                        w.mileage_verification_required = (
                            record.evidence_status == "verification_required"
                        )
            svc.save_draft(draft_id, draft)
            return {"calculated": True}
        _run("recalculate_mileage", _mileage)

    return steps


_views_ai_confirmation = _split_ai_confirmation.register_ai_confirmation_routes(
    app,
    _cb_resolve_AIIntentService=lambda: AIIntentService,
    _cb_resolve_DailyReportService=lambda: DailyReportService,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve_EmployeeResolutionService=lambda: EmployeeResolutionService,
    _cb_resolve_FormalSaveError=lambda: FormalSaveError,
    _cb_resolve_ManifestError=lambda: ManifestError,
    _cb_resolve_TravelService=lambda: TravelService,
    _cb_resolve_WorkOrderContextService=lambda: WorkOrderContextService,
    _cb_resolve__ai_daily_report_business_date=lambda: _ai_daily_report_business_date,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__attachment_manifest_service=lambda: _attachment_manifest_service,
    _cb_resolve__auto_prepare_draft_media=lambda: _auto_prepare_draft_media,
    _cb_resolve__new_formal_save_service=lambda: _new_formal_save_service,
    _cb_resolve__run_phase7_validation=lambda: _run_phase7_validation,
    _cb_resolve_app=lambda: app,
    _cb_resolve_datetime=lambda: datetime,
    _cb_resolve_db=lambda: db,
    _cb_resolve_deepseek_assistant_settings=lambda: deepseek_assistant_settings,
    _cb_resolve_g=lambda: g,
    _cb_resolve_generate_action_id=lambda: generate_action_id,
    _cb_resolve_get_google_routes_api_key=lambda: get_google_routes_api_key,
    _cb_resolve_get_timezone_name=lambda: get_timezone_name,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_is_phase1_implemented=lambda: is_phase1_implemented,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_now=lambda: now,
    _cb_resolve_reconcile_travel_verification_fields=lambda: reconcile_travel_verification_fields,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_service_order=lambda: require_service_order,
    _cb_resolve_timezone=lambda: timezone,
    _cb_resolve_url_for=lambda: url_for,
)
ai_daily_report_chat = _views_ai_confirmation['ai_daily_report_chat']
ai_daily_report_confirm = _views_ai_confirmation['ai_daily_report_confirm']


_views_draft_get = _split_draft_get.register_draft_get_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
)
ai_daily_report_get_draft = _views_draft_get['ai_daily_report_get_draft']


_views_draft_prepare = _split_draft_get.register_draft_prepare_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__auto_prepare_draft_media=lambda: _auto_prepare_draft_media,
    _cb_resolve__build_review_center_preview=lambda: _build_review_center_preview,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_auto_prepare = _views_draft_prepare['ai_daily_report_auto_prepare']


_views_photos_discover = _split_photos_discover.register_photos_discover_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve_PhotoDiscoveryService=lambda: PhotoDiscoveryService,
    _cb_resolve_PhotoMetadataService=lambda: PhotoMetadataService,
    _cb_resolve_SHARED_PHOTOS_DIR=lambda: SHARED_PHOTOS_DIR,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_service_order_photo_folder=lambda: service_order_photo_folder,
)
ai_daily_report_discover_photos = _views_photos_discover['ai_daily_report_discover_photos']


_views_photo_timeline = _split_photos_discover.register_photo_timeline_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
)
ai_daily_report_confirm_photo_timeline = _views_photo_timeline['ai_daily_report_confirm_photo_timeline']


# ─── Phase 5: Photo Classification APIs ─────────────────────────────────────

def _make_photo_classification_service():
    """Create PhotoClassificationService with Vision provider from config."""
    from ai_daily_report import (
        VisionClassificationService, DeepSeekVisionProvider, PhotoClassificationService,
    )
    settings = vision_settings()
    provider = DeepSeekVisionProvider(
        api_key=settings["api_key"],
        model=settings["model"],
        api_url=(settings.get("base_url") or "https://api.deepseek.com").rstrip("/") + "/chat/completions",
    )
    vision = VisionClassificationService(
        provider=provider,
        enabled=settings["enabled"],
        max_image_size=settings["vision_max_image_size"],
        max_image_bytes=settings["vision_max_image_bytes"],
    )
    return PhotoClassificationService(
        vision_service=vision,
        shared_photos_root=SHARED_PHOTOS_DIR,
        db_connection=db(),
        safety_auto_select_confidence=settings["safety_auto_select_confidence"],
        safety_verify_confidence=settings["safety_verify_confidence"],
        max_service_photos=settings["max_service_photos"],
    )


_views_photos_classify = _split_photos_discover.register_photos_classify_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__make_photo_classification_service=lambda: _make_photo_classification_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_vision_settings=lambda: vision_settings,
)
ai_daily_report_classify_photos = _views_photos_classify['ai_daily_report_classify_photos']


_views_safety_photo = _split_photos_discover.register_safety_photo_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__make_photo_classification_service=lambda: _make_photo_classification_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
)
ai_daily_report_change_safety_photo = _views_safety_photo['ai_daily_report_change_safety_photo']


_views_service_photo_add = _split_photos_discover.register_service_photo_add_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__make_photo_classification_service=lambda: _make_photo_classification_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
)
ai_daily_report_add_service_photo = _views_service_photo_add['ai_daily_report_add_service_photo']


_views_service_photo_remove = _split_photos_discover.register_service_photo_remove_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__make_photo_classification_service=lambda: _make_photo_classification_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
)
ai_daily_report_remove_service_photo = _views_service_photo_remove['ai_daily_report_remove_service_photo']


_views_photos_auto_select = _split_photos_discover.register_photos_auto_select_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_auto_select_photos = _views_photos_auto_select['ai_daily_report_auto_select_photos']


_views_photo_mark = _split_photos_discover.register_photo_mark_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_mark_photo = _views_photo_mark['ai_daily_report_mark_photo']


_views_photo_time = _split_photos_discover.register_photo_time_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_update_photo_time = _views_photo_time['ai_daily_report_update_photo_time']




_views_draft_cancel = _split_draft_cancel.register_draft_cancel_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_datetime=lambda: datetime,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_timezone=lambda: timezone,
)
ai_daily_report_cancel = _views_draft_cancel['ai_daily_report_cancel']


_views_draft_delete = _split_draft_cancel.register_draft_delete_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__attachment_manifest_service=lambda: _attachment_manifest_service,
    _cb_resolve_datetime=lambda: datetime,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_timezone=lambda: timezone,
)
ai_daily_report_delete = _views_draft_delete['ai_daily_report_delete']


_views_draft_reopen = _split_draft_cancel.register_draft_reopen_routes(
    app,
    _cb_resolve_DraftStateError=lambda: DraftStateError,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_datetime=lambda: datetime,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_timezone=lambda: timezone,
)
ai_daily_report_reopen = _views_draft_reopen['ai_daily_report_reopen']


# ─── Phase 6: AI Daily Report Review Center APIs ───────────────────────────

_views_draft_csrf = _split_draft_csrf.register_draft_csrf_routes(
    app,
    _cb_resolve_ai_daily_report_csrf_token=lambda: ai_daily_report_csrf_token,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
)
ai_daily_report_csrf = _views_draft_csrf['ai_daily_report_csrf']


_views_draft_orders = _split_draft_csrf.register_draft_orders_routes(
    app,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_service_order_access_filters=lambda: service_order_access_filters,
)
ai_daily_report_service_orders_options = _views_draft_orders['ai_daily_report_service_orders_options']


_views_draft_create = _split_draft_cancel.register_draft_create_routes(
    app,
    _cb_resolve__ai_daily_report_business_date=lambda: _ai_daily_report_business_date,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_db=lambda: db,
    _cb_resolve_deepseek_assistant_settings=lambda: deepseek_assistant_settings,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_require_service_order=lambda: require_service_order,
)
ai_daily_report_create_draft = _views_draft_create['ai_daily_report_create_draft']


_views_draft_list = _split_draft_list.register_draft_list_routes(
    app,
    _cb_resolve_ai_daily_report_draft_list_filters=lambda: ai_daily_report_draft_list_filters,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_json=lambda: json,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_reconcile_travel_verification_fields=lambda: reconcile_travel_verification_fields,
    _cb_resolve_request=lambda: request,
    _cb_resolve_url_for=lambda: url_for,
)
ai_daily_report_drafts_list = _views_draft_list['ai_daily_report_drafts_list']


def _build_review_center_preview(draft_row):
    """Aggregated read-only preview for the Review Center detail page.

    Every response the review page assigns wholesale to ``currentPreview``
    must use this same shape (status / draft_version / basic_info / timeline /
    validation_result / ...). The flat DailyReportService.build_preview() is a
    chat-flow payload and must NOT be used here (v0.1.254: auto-prepare used
    to return it, which wiped the action buttons after re-render).
    """
    from ai_daily_report import PreviewAggregationService
    preview_svc = PreviewAggregationService(db(), SHARED_PHOTOS_DIR)
    preview = preview_svc.build_preview(draft_row)

    # Phase 8: Attachment Preparation summary (READ-ONLY; never materializes)
    validation = _run_phase7_validation(draft_row)
    manifest_svc = _attachment_manifest_service()
    preview["attachment_preparation"] = manifest_svc.summary_for_preview(draft_row, validation)

    # Phase 9: Formal Save status (READ-ONLY)
    preview["formal_save"] = _formal_save_preview_summary(draft_row)
    return preview


_views_ai_validation = _split_ai_validation.register_ai_validation_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__build_review_center_preview=lambda: _build_review_center_preview,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_can_view_ai_daily_report_draft=lambda: can_view_ai_daily_report_draft,
    _cb_resolve_datetime=lambda: datetime,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_json=lambda: json,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_timezone=lambda: timezone,
)
ai_daily_report_draft_preview = _views_ai_validation['ai_daily_report_draft_preview']
ai_daily_report_draft_validation = _views_ai_validation['ai_daily_report_draft_validation']
ai_daily_report_draft_acknowledge = _views_ai_validation['ai_daily_report_draft_acknowledge']


def _formal_save_preview_summary(draft_row):
    """Read-only Phase 9 status aggregation for the Review Center preview."""
    draft_id = int(draft_row["id"])
    commit = db().execute(
        """
        select status, service_report_id, draft_version, committed_at, failure_code
        from ai_daily_report_formal_commits
        where draft_id = ? order by id desc limit 1
        """,
        (draft_id,),
    ).fetchone()
    saved_report_id = draft_row.get("saved_report_id")
    summary = {
        "allowed": can_formal_save_draft(g.user, draft_row),
        "status": "not_saved",
        "service_report_id": saved_report_id,
    }
    if saved_report_id:
        summary["status"] = "saved"
    if commit:
        summary["commit_status"] = commit["status"]
        summary["commit_report_id"] = commit["service_report_id"]
        summary["committed_at"] = commit["committed_at"]
        summary["failure_code"] = commit["failure_code"]
        if commit["status"] == "committed" and not saved_report_id:
            summary["status"] = "saved"
            summary["service_report_id"] = commit["service_report_id"]
        elif commit["status"] in ("committing", "failed"):
            summary["status"] = "saved" if saved_report_id else "commit_" + commit["status"]
    return summary






_views_ai_evidence = _split_ai_evidence.register_ai_evidence_routes(
    app,
    _cb_resolve_DATA_DIR=lambda: DATA_DIR,
    _cb_resolve_FormalSaveCommitConflictError=lambda: FormalSaveCommitConflictError,
    _cb_resolve_FormalSaveComplianceError=lambda: FormalSaveComplianceError,
    _cb_resolve_FormalSaveIntegrityError=lambda: FormalSaveIntegrityError,
    _cb_resolve_FormalSaveStaleError=lambda: FormalSaveStaleError,
    _cb_resolve_FormalSaveStateError=lambda: FormalSaveStateError,
    _cb_resolve_FormalSaveValidationBlockedError=lambda: FormalSaveValidationBlockedError,
    _cb_resolve_ManifestError=lambda: ManifestError,
    _cb_resolve_ManifestNotFoundError=lambda: ManifestNotFoundError,
    _cb_resolve_Path=lambda: Path,
    _cb_resolve_SHARED_PHOTOS_DIR=lambda: SHARED_PHOTOS_DIR,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__attachment_manifest_service=lambda: _attachment_manifest_service,
    _cb_resolve__manifest_error_response=lambda: _manifest_error_response,
    _cb_resolve__new_formal_save_service=lambda: _new_formal_save_service,
    _cb_resolve__run_phase7_validation=lambda: _run_phase7_validation,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_formal_save_draft=lambda: can_formal_save_draft,
    _cb_resolve_can_prepare_attachment_manifest=lambda: can_prepare_attachment_manifest,
    _cb_resolve_can_view_ai_daily_report_draft=lambda: can_view_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_json=lambda: json,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_now=lambda: now,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_send_file=lambda: send_file,
    _cb_resolve_url_for=lambda: url_for,
)
ai_daily_report_draft_photo_preview = _views_ai_evidence['ai_daily_report_draft_photo_preview']
ai_daily_report_draft_evidence_preview = _views_ai_evidence['ai_daily_report_draft_evidence_preview']
ai_daily_report_draft_prepare_attachments = _views_ai_evidence['ai_daily_report_draft_prepare_attachments']
ai_daily_report_draft_formal_save = _views_ai_evidence['ai_daily_report_draft_formal_save']
ai_daily_report_manifest_compliance_review = _views_ai_evidence['ai_daily_report_manifest_compliance_review']
ai_daily_report_draft_manifest = _views_ai_evidence['ai_daily_report_draft_manifest']
ai_daily_report_manifest_asset_preview = _views_ai_evidence['ai_daily_report_manifest_asset_preview']




# ─── Phase 8: Attachment Preparation / Manifest ─────────────────────────────

def _attachment_manifest_service():
    return AttachmentManifestService(db(), SHARED_PHOTOS_DIR, DATA_DIR, int(g.user["id"]))


def _run_phase7_validation(draft_row):
    """Server-side Phase 7 Validation rerun (read-only).

    Phase 8 MUST NOT trust any frontend can_proceed flag; this is the only
    gate for manifest preparation.
    """
    draft_data = {}
    try:
        draft_data = json.loads(draft_row["draft_data"] or "{}")
    except (json.JSONDecodeError, TypeError):
        draft_data = {"verification_required": True, "verification_fields": ["draft_data_corrupted"]}
    from ai_daily_report.validation_engine import ValidationEngine, ValidationContextBuilder
    context = ValidationContextBuilder(db()).build(draft_data)
    return ValidationEngine().validate(
        draft_data=draft_data,
        context=context,
        draft_version=draft_row.get("draft_version", 1),
    )


def _manifest_error_response(exc, default_status=422):
    if isinstance(exc, ManifestValidationBlockedError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 422
    if isinstance(exc, ManifestIntegrityError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 422
    if isinstance(exc, ManifestStaleError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 409
    if isinstance(exc, ManifestNotFoundError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 404
    if isinstance(exc, ManifestError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), default_status
    return jsonify({"ok": False, "error": "附件准备失败"}), 500




# ─── Phase 9: Formal Save / Transactional Commit ────────────────────────────









@app.delete("/api/ai/daily-report/draft/<int:draft_id>/manifest/<manifest_id>")
@login_required
def ai_daily_report_manifest_cancel(draft_id, manifest_id):
    """Cancel a (non-ready) Manifest: mark cancelled + delete its staging only.

    Original Work Order photos / Phase 3B evidence / formal attachments are
    never touched. Requires CSRF + can_prepare (confirmed, non-read-only).
    """
    if not is_internal_user():
        return jsonify({"ok": False, "error": "无权限"}), 403

    require_ai_daily_report_csrf()

    svc = _ai_daily_report_service()
    draft_row = svc.get_draft(draft_id)
    if not draft_row:
        return jsonify({"ok": False, "error": "Draft 不存在"}), 404

    if not can_prepare_attachment_manifest(g.user, draft_row):
        return jsonify({"ok": False, "error": "无权操作附件清单"}), 403

    try:
        manifest_svc = _attachment_manifest_service()
        manifest = manifest_svc.cancel(draft_id, manifest_id)
    except ManifestError as exc:
        return _manifest_error_response(exc, default_status=400)

    db().commit()
    return jsonify({"ok": True, "manifest": manifest})


_views_ai_workers = _split_ai_workers.register_ai_workers_routes(
    app,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve_TravelService=lambda: TravelService,
    _cb_resolve_WorkerTravel=lambda: WorkerTravel,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__fold_staff_query=lambda: _fold_staff_query,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_normalize_trip_type=lambda: normalize_trip_type,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_update_worker = _views_ai_workers['ai_daily_report_update_worker']
ai_daily_report_staff_search = _views_ai_workers['ai_daily_report_staff_search']
ai_daily_report_add_worker = _views_ai_workers['ai_daily_report_add_worker']
ai_daily_report_remove_worker = _views_ai_workers['ai_daily_report_remove_worker']


def _fold_staff_query(text: str) -> str:
    """Casefold + strip diacritics for tolerant staff matching.

    SQL equality does not provide the required Unicode folding, so searching
    "Antonio" would not match a stored "Ant\u00f3nio". Fold both sides.
    """
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()








_views_ai_work = _split_ai_work.register_ai_work_routes(
    app,
    _cb_resolve_DATA_DIR=lambda: DATA_DIR,
    _cb_resolve_DraftVersionConflict=lambda: DraftVersionConflict,
    _cb_resolve_EmployeeResolutionService=lambda: EmployeeResolutionService,
    _cb_resolve_GoogleRoutesService=lambda: GoogleRoutesService,
    _cb_resolve_MileageService=lambda: MileageService,
    _cb_resolve_TravelService=lambda: TravelService,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_can_edit_ai_daily_report_draft=lambda: can_edit_ai_daily_report_draft,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_get_google_routes_api_key=lambda: get_google_routes_api_key,
    _cb_resolve_get_google_static_maps_api_key=lambda: get_google_static_maps_api_key,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_os=lambda: os,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
    _cb_resolve_require_service_order=lambda: require_service_order,
)
ai_daily_report_recalculate_mileage = _views_ai_work['ai_daily_report_recalculate_mileage']
ai_daily_report_update_arrival_departure = _views_ai_work['ai_daily_report_update_arrival_departure']
ai_daily_report_update_work_item = _views_ai_work['ai_daily_report_update_work_item']






# ─── Phase 6: Review Center Page Routes ─────────────────────────────────────

_views_draft_pages = _split_draft_list.register_draft_pages_routes(
    app,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_ai_daily_report_csrf_token=lambda: ai_daily_report_csrf_token,
    _cb_resolve_can_view_ai_daily_report_draft=lambda: can_view_ai_daily_report_draft,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
)
ai_daily_report_drafts_page = _views_draft_pages['ai_daily_report_drafts_page']
ai_daily_report_draft_detail_page = _views_draft_pages['ai_daily_report_draft_detail_page']








_views_company_page = _split_company_page.register_company_page_routes(
    app,
    _cb_resolve_COMPANY_ATTACHMENT_DIR=lambda: COMPANY_ATTACHMENT_DIR,
    _cb_resolve_DEFAULT_PAYMENT_INSTRUCTIONS=lambda: DEFAULT_PAYMENT_INSTRUCTIONS,
    _cb_resolve_DEFAULT_TIMEZONE=lambda: DEFAULT_TIMEZONE,
    _cb_resolve_ZoneInfo=lambda: ZoneInfo,
    _cb_resolve_ZoneInfoNotFoundError=lambda: ZoneInfoNotFoundError,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_manage_company_info=lambda: can_manage_company_info,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_get_company_attachments=lambda: get_company_attachments,
    _cb_resolve_get_company_profile=lambda: get_company_profile,
    _cb_resolve_get_invoice_terms=lambda: get_invoice_terms,
    _cb_resolve_get_payment_instructions=lambda: get_payment_instructions,
    _cb_resolve_get_timezone_name=lambda: get_timezone_name,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_save_named_attachment=lambda: save_named_attachment,
    _cb_resolve_set_setting=lambda: set_setting,
    _cb_resolve_uploaded_attachments_from_request=lambda: uploaded_attachments_from_request,
    _cb_resolve_url_for=lambda: url_for,
)
company_info = _views_company_page['company_info']


_views_company_attachment_read = _split_company_page.register_company_attachment_read_routes(
    app,
    _cb_resolve_COMPANY_ATTACHMENT_DIR=lambda: COMPANY_ATTACHMENT_DIR,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_os=lambda: os,
    _cb_resolve_safe_attachment_response=lambda: safe_attachment_response,
    _cb_resolve_send_file=lambda: send_file,
)
download_company_attachment = _views_company_attachment_read['download_company_attachment']
preview_company_attachment = _views_company_attachment_read['preview_company_attachment']




_views_company_attachment_delete = _split_company_page.register_company_attachment_delete_routes(
    app,
    _cb_resolve_COMPANY_ATTACHMENT_DIR=lambda: COMPANY_ATTACHMENT_DIR,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_os=lambda: os,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_url_for=lambda: url_for,
)
delete_company_attachment = _views_company_attachment_delete['delete_company_attachment']
legacy_company_settings = _views_company_attachment_delete['legacy_company_settings']




_views_invoice_list = _split_invoice_list.register_invoice_list_routes(
    app,
    _cb_resolve_STATUS_LABELS=lambda: STATUS_LABELS,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_view_invoices=lambda: can_view_invoices,
    _cb_resolve_client_filter_clause=lambda: client_filter_clause,
    _cb_resolve_db=lambda: db,
    _cb_resolve_invoice_totals=lambda: invoice_totals,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
)
invoices = _views_invoice_list['invoices']


_views_invoice_query_page = _split_invoice_list.register_invoice_query_page_routes(
    app,
    _cb_resolve_STATUS_LABELS=lambda: STATUS_LABELS,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_view_invoices=lambda: can_view_invoices,
    _cb_resolve_client_filter_clause=lambda: client_filter_clause,
    _cb_resolve_db=lambda: db,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_report_location_filters=lambda: report_location_filters,
    _cb_resolve_report_project_options=lambda: report_project_options,
    _cb_resolve_request=lambda: request,
)
invoice_query = _views_invoice_query_page['invoice_query']




_views_order_query_page = _split_order_query_page.register_order_query_page_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_current_language=lambda: current_language,
    _cb_resolve_db=lambda: db,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
)
service_order_query = _views_order_query_page['service_order_query']


_views_buyer_query_page = _split_order_query_page.register_buyer_query_page_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_buyer_map_rows=lambda: buyer_map_rows,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_report_location_filters=lambda: report_location_filters,
    _cb_resolve_request=lambda: request,
)
buyer_query = _views_buyer_query_page['buyer_query']


_views_report_query_page = _split_report_query_page.register_report_query_page_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_current_language=lambda: current_language,
    _cb_resolve_db=lambda: db,
    _cb_resolve_has_action_permission=lambda: has_action_permission,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_service_order_access_filters=lambda: service_order_access_filters,
)
service_report_query = _views_report_query_page['service_report_query']


def labor_report_entries(date_from="", date_to="", worker_id="", date_mode="actual"):
    return _split_labor_entries.labor_report_entries(date_from, date_to, worker_id, date_mode, db=db, split_report_labor_hours=split_report_labor_hours)


def parsed_iso_date(value):
    return _split_iso_date.parsed_iso_date(value, date=date)


def service_order_access_filters(table_alias="service_orders"):
    return _calendar_map_services.service_order_access_filters(table_alias, g=g, is_external_employee=is_external_employee, is_external_manager=is_external_manager)


def service_order_calendar_rows(date_from, date_to):
    return _calendar_map_services.service_order_calendar_rows(date_from, date_to, db=db, service_order_access_filters=service_order_access_filters)


def assign_service_order_calendar_lanes(week):
    return _calendar_map_services.assign_service_order_calendar_lanes(week)


def service_order_calendar_weeks(year, month):
    return _calendar_map_services.service_order_calendar_weeks(year, month, STATUS_LABELS=STATUS_LABELS, assign_service_order_calendar_lanes=assign_service_order_calendar_lanes, client_order_number_warning=client_order_number_warning, date=date, parsed_iso_date=parsed_iso_date, service_order_calendar_rows=service_order_calendar_rows, timedelta=timedelta, url_for=url_for)


def xlsx_column_name(index):
    return _split_xlsx_columns.xlsx_column_name(index)


def build_simple_xlsx(headers, rows, sheet_name="Sheet1"):
    return _split_xlsx_single.build_simple_xlsx(headers, rows, sheet_name, BytesIO=BytesIO, Decimal=Decimal, html=html, xlsx_column_name=xlsx_column_name, zipfile=zipfile)


def build_multi_sheet_xlsx(sheets):
    return _split_xlsx_multi.build_multi_sheet_xlsx(sheets, BytesIO=BytesIO, Decimal=Decimal, html=html, re=re, xlsx_column_name=xlsx_column_name, zipfile=zipfile)


_views_report_queries = _split_report_queries.register_report_queries_routes(
    app,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS=lambda: CUSTOMER_REIMBURSEMENT_STATUS_LABELS,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_build_simple_xlsx=lambda: build_simple_xlsx,
    _cb_resolve_can_view_audit_logs=lambda: can_view_audit_logs,
    _cb_resolve_can_view_customer_reimbursement=lambda: can_view_customer_reimbursement,
    _cb_resolve_current_language=lambda: current_language,
    _cb_resolve_date=lambda: date,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_external_manager=lambda: is_external_manager,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_re=lambda: re,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_send_file=lambda: send_file,
)
export_visible_report = _views_report_queries['export_visible_report']
customer_reimbursement_query = _views_report_queries['customer_reimbursement_query']
audit_log_report = _views_report_queries['audit_log_report']




_expense_query_views = register_expense_query_routes(
    app,
    resolve_EXPENSE_PAYOUT_LABELS=lambda: EXPENSE_PAYOUT_LABELS,
    resolve_EXPENSE_STATUS_LABELS=lambda: EXPENSE_STATUS_LABELS,
    current_language=lambda *args, **kwargs: current_language(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    expense_access_filter=lambda *args, **kwargs: expense_access_filter(*args, **kwargs),
    is_internal_user=lambda *args, **kwargs: is_internal_user(*args, **kwargs),
    login_required=login_required,
)
expense_query = _expense_query_views['expense_query']
expense_processing = _expense_query_views['expense_processing']




_expense_reset_views = register_expense_reset_routes(
    app,
    cancel_expense_payment_order=lambda expense_id: _expense_cancel_services.cancel_expense_payment_order(expense_id, cancel_expense_payment_order=cancel_expense_payment_order, db=db, now=now, log_action=log_action),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    is_internal_user=lambda *args, **kwargs: is_internal_user(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    normalized_role=lambda *args, **kwargs: normalized_role(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
)
process_expense_action = _expense_reset_views['process_expense_action']




_service_order_views = register_service_order_routes(
    app,
    EXPENSE_STATUS_LABELS=EXPENSE_STATUS_LABELS,
    STATUS_LABELS=STATUS_LABELS,
    can_create_service_order=lambda *args, **kwargs: can_create_service_order(*args, **kwargs),
    can_delete_service_order=lambda *args, **kwargs: can_delete_service_order(*args, **kwargs),
    can_manage_buyers=lambda *args, **kwargs: can_manage_buyers(*args, **kwargs),
    can_view_customer_reimbursement=lambda *args, **kwargs: can_view_customer_reimbursement(*args, **kwargs),
    country_from_form=lambda *args, **kwargs: country_from_form(*args, **kwargs),
    country_rows=lambda *args, **kwargs: country_rows(*args, **kwargs),
    current_language=lambda *args, **kwargs: current_language(*args, **kwargs),
    customer_reimbursement_item_expense_amount=lambda *args, **kwargs: customer_reimbursement_item_expense_amount(*args, **kwargs),
    customer_reimbursement_items=lambda *args, **kwargs: customer_reimbursement_items(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    ensure_service_order_picture_folder=lambda *args, **kwargs: ensure_service_order_picture_folder(*args, **kwargs),
    expense_access_filter=lambda *args, **kwargs: expense_access_filter(*args, **kwargs),
    is_external_employee=lambda *args, **kwargs: is_external_employee(*args, **kwargs),
    is_external_manager=lambda *args, **kwargs: is_external_manager(*args, **kwargs),
    is_external_user=lambda *args, **kwargs: is_external_user(*args, **kwargs),
    is_manager=lambda *args, **kwargs: is_manager(*args, **kwargs),
    latest_customer_reimbursement=lambda *args, **kwargs: latest_customer_reimbursement(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    manufacturer_from_form=lambda *args, **kwargs: manufacturer_from_form(*args, **kwargs),
    manufacturer_options=lambda *args, **kwargs: manufacturer_options(*args, **kwargs),
    next_service_order_number=lambda *args, **kwargs: next_service_order_number(*args, **kwargs),
    normalized_role=lambda *args, **kwargs: normalized_role(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
    order_employee_settlement_rows=lambda *args, **kwargs: order_employee_settlement_rows(*args, **kwargs),
    report_duration_hours=lambda *args, **kwargs: report_duration_hours(*args, **kwargs),
    require_service_order=lambda *args, **kwargs: require_service_order(*args, **kwargs),
    service_order_delete_blockers=lambda *args, **kwargs: service_order_delete_blockers(*args, **kwargs),
    service_order_dependency_counts=lambda *args, **kwargs: service_order_dependency_counts(*args, **kwargs),
    service_order_photo_folder=lambda *args, **kwargs: service_order_photo_folder(*args, **kwargs),
    service_order_photo_summary=lambda *args, **kwargs: service_order_photo_summary(*args, **kwargs),
    sync_order_commercial_document=lambda *args, **kwargs: sync_order_commercial_document(*args, **kwargs),
)
service_orders = _service_order_views['service_orders']
new_service_order = _service_order_views['new_service_order']
edit_service_order = _service_order_views['edit_service_order']
service_order_detail = _service_order_views['service_order_detail']
delete_service_order = _service_order_views['delete_service_order']


_calendar_map_views = register_calendar_map_routes(
    app,
    resolve_GEOCODER_VERSION=lambda: GEOCODER_VERSION,
    resolve_GEOCODING_ENABLED=lambda: GEOCODING_ENABLED,
    resolve_SERVICE_ORDER_STATIC_MAP_CENTER=lambda: SERVICE_ORDER_STATIC_MAP_CENTER,
    resolve_SERVICE_ORDER_STATIC_MAP_LABELED_LIMIT=lambda: SERVICE_ORDER_STATIC_MAP_LABELED_LIMIT,
    resolve_SERVICE_ORDER_STATIC_MAP_MAX_MARKERS=lambda: SERVICE_ORDER_STATIC_MAP_MAX_MARKERS,
    resolve_SERVICE_ORDER_STATIC_MAP_STATUS_COLORS=lambda: SERVICE_ORDER_STATIC_MAP_STATUS_COLORS,
    resolve_SERVICE_ORDER_STATIC_MAP_ZOOM=lambda: SERVICE_ORDER_STATIC_MAP_ZOOM,
    resolve_STATIC_MAP_COUNTRIES=lambda: STATIC_MAP_COUNTRIES,
    TemporaryGeocodingError=TemporaryGeocodingError,
    buyer_map_payload=lambda *args, **kwargs: buyer_map_payload(*args, **kwargs),
    buyer_map_rows=lambda *args, **kwargs: buyer_map_rows(*args, **kwargs),
    can_view_invoices=lambda *args, **kwargs: can_view_invoices(*args, **kwargs),
    resolve_date=lambda: date,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    geocode_buyer=lambda *args, **kwargs: geocode_buyer(*args, **kwargs),
    get_company_profile=lambda *args, **kwargs: get_company_profile(*args, **kwargs),
    get_google_maps_browser_api_key=lambda *args, **kwargs: get_google_maps_browser_api_key(*args, **kwargs),
    get_google_static_maps_api_key=lambda *args, **kwargs: get_google_static_maps_api_key(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    headquarters_coordinates=lambda *args, **kwargs: headquarters_coordinates(*args, **kwargs),
    is_external_manager=lambda *args, **kwargs: is_external_manager(*args, **kwargs),
    is_internal_user=lambda *args, **kwargs: is_internal_user(*args, **kwargs),
    login_required=login_required,
    request_country_code=lambda *args, **kwargs: request_country_code(*args, **kwargs),
    service_order_calendar_weeks=lambda *args, **kwargs: service_order_calendar_weeks(*args, **kwargs),
)
service_order_calendar = _calendar_map_views['service_order_calendar']
service_order_map = _calendar_map_views['service_order_map']
service_order_map_static_image = _calendar_map_views['service_order_map_static_image']
geocode_next_service_order = _calendar_map_views['geocode_next_service_order']
retry_failed_service_order_geocodes = _calendar_map_views['retry_failed_service_order_geocodes']


def buyer_map_payload(buyer):
    return _calendar_map_services.buyer_map_payload(buyer, can_manage_buyers=can_manage_buyers, can_view_invoices=can_view_invoices, date=date, inspection_cycle_days=inspection_cycle_days, inspection_warning_days=inspection_warning_days, parsed_iso_date=parsed_iso_date, url_for=url_for)


def buyer_map_rows(where_clause="1 = 1", params=()):
    return _calendar_map_services.buyer_map_rows(where_clause, params, current_language=current_language, db=db)




SERVICE_ORDER_STATIC_MAP_MAX_MARKERS = 200
SERVICE_ORDER_STATIC_MAP_LABELED_LIMIT = 35
# Fixed view: the contiguous United States (excludes Alaska / Hawaii on
# purpose - zooming out far enough to include them would shrink the sites
# to a few pixels). The map never auto-fits the filtered sites, so the
# picture stays stable when filters change.
SERVICE_ORDER_STATIC_MAP_CENTER = "39.8283,-98.5795"
SERVICE_ORDER_STATIC_MAP_ZOOM = 4
SERVICE_ORDER_STATIC_MAP_STATUS_COLORS = {
    "overdue": "red",
    "warning": "orange",
    "fresh": "green",
    "none": "gray",
}

# Countries whose visitors normally cannot reach Google from the browser
# (the Great Firewall blocks maps.googleapis.com). For them the site map
# opens directly in static mode: the server (which does reach Google) draws
# the image, so the first visit is not left blank while the browser waits
# for a script that will never arrive. Detected from the Cloudflare header;
# unknown/absent means "assume the interactive map works".
STATIC_MAP_COUNTRIES = {"CN"}


def request_country_code():
    return _calendar_map_services.request_country_code(request=request)
















_views_order_attachments = _split_order_attachments.register_order_attachments_routes(
    app,
    _cb_resolve_ALLOWED_IMAGE_EXTENSIONS=lambda: ALLOWED_IMAGE_EXTENSIONS,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR=lambda: CUSTOMER_REIMBURSEMENT_DIR,
    _cb_resolve_Path=lambda: Path,
    _cb_resolve_REPORT_ATTACHMENTS_DIR=lambda: REPORT_ATTACHMENTS_DIR,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_app=lambda: app,
    _cb_resolve_can_create_service_report=lambda: can_create_service_report,
    _cb_resolve_can_view_customer_reimbursement=lambda: can_view_customer_reimbursement,
    _cb_resolve_customer_reimbursement_attachment_path=lambda: customer_reimbursement_attachment_path,
    _cb_resolve_customer_reimbursement_file_path=lambda: customer_reimbursement_file_path,
    _cb_resolve_db=lambda: db,
    _cb_resolve_ensure_customer_reimbursement_pdf_file=lambda: ensure_customer_reimbursement_pdf_file,
    _cb_resolve_ensure_service_order_picture_folder=lambda: ensure_service_order_picture_folder,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_has_action_permission=lambda: has_action_permission,
    _cb_resolve_has_menu_permission=lambda: has_menu_permission,
    _cb_resolve_is_google_photos_share_url=lambda: is_google_photos_share_url,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_os=lambda: os,
    _cb_resolve_re=lambda: re,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_report_attachment_path=lambda: report_attachment_path,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_service_order=lambda: require_service_order,
    _cb_resolve_secure_filename=lambda: secure_filename,
    _cb_resolve_send_file=lambda: send_file,
    _cb_resolve_service_order_incoming_folder=lambda: service_order_incoming_folder,
    _cb_resolve_shared_photos_root=lambda: shared_photos_root,
    _cb_resolve_start_google_photos_import_job=lambda: start_google_photos_import_job,
    _cb_resolve_tempfile=lambda: tempfile,
    _cb_resolve_url_for=lambda: url_for,
    _cb_resolve_zipfile=lambda: zipfile,
)
import_google_photos_for_service_order = _views_order_attachments['import_google_photos_for_service_order']
download_service_order_attachments = _views_order_attachments['download_service_order_attachments']


_views_settlement_form = _split_settlement_form.register_settlement_form_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_build_customer_reimbursement_pdf=lambda: build_customer_reimbursement_pdf,
    _cb_resolve_build_simple_xlsx=lambda: build_simple_xlsx,
    _cb_resolve_can_create_invoice=lambda: can_create_invoice,
    _cb_resolve_can_manage_customer_reimbursement=lambda: can_manage_customer_reimbursement,
    _cb_resolve_can_view_customer_reimbursement=lambda: can_view_customer_reimbursement,
    _cb_resolve_contract_rate_missing_summary=lambda: contract_rate_missing_summary,
    _cb_resolve_create_customer_reimbursement=lambda: create_customer_reimbursement,
    _cb_resolve_customer_reimbursement_column_totals=lambda: customer_reimbursement_column_totals,
    _cb_resolve_customer_reimbursement_file_path=lambda: customer_reimbursement_file_path,
    _cb_resolve_customer_reimbursement_gate_error=lambda: customer_reimbursement_gate_error,
    _cb_resolve_customer_reimbursement_item_expense_amount=lambda: customer_reimbursement_item_expense_amount,
    _cb_resolve_customer_reimbursement_items=lambda: customer_reimbursement_items,
    _cb_resolve_customer_reimbursement_items_from_form=lambda: customer_reimbursement_items_from_form,
    _cb_resolve_customer_reimbursement_linked_invoice=lambda: customer_reimbursement_linked_invoice,
    _cb_resolve_customer_reimbursement_lodging_range=lambda: customer_reimbursement_lodging_range,
    _cb_resolve_customer_reimbursement_pending_sources=lambda: customer_reimbursement_pending_sources,
    _cb_resolve_customer_reimbursement_totals=lambda: customer_reimbursement_totals,
    _cb_resolve_customer_reimbursement_workspace_data=lambda: customer_reimbursement_workspace_data,
    _cb_resolve_db=lambda: db,
    _cb_resolve_deliver_customer_reimbursement_email=lambda: deliver_customer_reimbursement_email,
    _cb_resolve_email_delivery_summary=lambda: email_delivery_summary,
    _cb_resolve_ensure_customer_reimbursement_pdf_file=lambda: ensure_customer_reimbursement_pdf_file,
    _cb_resolve_ensure_customer_reimbursement_pdf_record=lambda: ensure_customer_reimbursement_pdf_record,
    _cb_resolve_excessive_following_mileage_rows=lambda: excessive_following_mileage_rows,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_get_customer_reimbursement_attachments=lambda: get_customer_reimbursement_attachments,
    _cb_resolve_json=lambda: json,
    _cb_resolve_latest_customer_reimbursement=lambda: latest_customer_reimbursement,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_money=lambda: money,
    _cb_resolve_money_decimal=lambda: money_decimal,
    _cb_resolve_notify_role=lambda: notify_role,
    _cb_resolve_now=lambda: now,
    _cb_resolve_re=lambda: re,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_remove_customer_reimbursement_pdf=lambda: remove_customer_reimbursement_pdf,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_customer_reimbursement=lambda: require_customer_reimbursement,
    _cb_resolve_require_service_order=lambda: require_service_order,
    _cb_resolve_require_service_order_start_date=lambda: require_service_order_start_date,
    _cb_resolve_resolve_contract_rates=lambda: resolve_contract_rates,
    _cb_resolve_save_customer_reimbursement_items=lambda: save_customer_reimbursement_items,
    _cb_resolve_save_customer_reimbursement_uploads=lambda: save_customer_reimbursement_uploads,
    _cb_resolve_send_file=lambda: send_file,
    _cb_resolve_service_order_active_invoice=lambda: service_order_active_invoice,
    _cb_resolve_to_float=lambda: to_float,
    _cb_resolve_update_customer_reimbursement_totals=lambda: update_customer_reimbursement_totals,
    _cb_resolve_url_for=lambda: url_for,
)
customer_reimbursement_form = _views_settlement_form['customer_reimbursement_form']
download_customer_reimbursement = _views_settlement_form['download_customer_reimbursement']
download_customer_reimbursement_excel = _views_settlement_form['download_customer_reimbursement_excel']
preview_customer_reimbursement = _views_settlement_form['preview_customer_reimbursement']








_views_settlement_actions = _split_settlement_actions.register_settlement_actions_routes(
    app,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR=lambda: CUSTOMER_REIMBURSEMENT_DIR,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS=lambda: CUSTOMER_REIMBURSEMENT_STATUS_LABELS,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_approve_customer_reimbursement=lambda: can_approve_customer_reimbursement,
    _cb_resolve_can_delete_customer_reimbursement=lambda: can_delete_customer_reimbursement,
    _cb_resolve_can_manage_customer_reimbursement=lambda: can_manage_customer_reimbursement,
    _cb_resolve_can_reset_customer_reimbursement=lambda: can_reset_customer_reimbursement,
    _cb_resolve_create_message=lambda: create_message,
    _cb_resolve_customer_reimbursement_gate_error=lambda: customer_reimbursement_gate_error,
    _cb_resolve_customer_reimbursement_items=lambda: customer_reimbursement_items,
    _cb_resolve_customer_reimbursement_linked_invoice=lambda: customer_reimbursement_linked_invoice,
    _cb_resolve_customer_reimbursement_pending_sources=lambda: customer_reimbursement_pending_sources,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_json=lambda: json,
    _cb_resolve_latest_customer_reimbursement=lambda: latest_customer_reimbursement,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_money=lambda: money,
    _cb_resolve_now=lambda: now,
    _cb_resolve_os=lambda: os,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_remove_customer_reimbursement_pdf=lambda: remove_customer_reimbursement_pdf,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_customer_reimbursement=lambda: require_customer_reimbursement,
    _cb_resolve_require_service_order=lambda: require_service_order,
    _cb_resolve_shutil=lambda: shutil,
    _cb_resolve_update_customer_reimbursement_totals=lambda: update_customer_reimbursement_totals,
    _cb_resolve_url_for=lambda: url_for,
)
approve_customer_reimbursement = _views_settlement_actions['approve_customer_reimbursement']
sync_customer_reimbursement_sources = _views_settlement_actions['sync_customer_reimbursement_sources']
ignore_customer_reimbursement_source = _views_settlement_actions['ignore_customer_reimbursement_source']
unignore_customer_reimbursement_source = _views_settlement_actions['unignore_customer_reimbursement_source']
return_customer_reimbursement = _views_settlement_actions['return_customer_reimbursement']
reset_customer_reimbursement = _views_settlement_actions['reset_customer_reimbursement']
delete_customer_reimbursement = _views_settlement_actions['delete_customer_reimbursement']














_views_settlement_attachments = _split_settlement_attachments.register_settlement_attachments_routes(
    app,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_can_manage_customer_reimbursement=lambda: can_manage_customer_reimbursement,
    _cb_resolve_customer_reimbursement_attachment_path=lambda: customer_reimbursement_attachment_path,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_os=lambda: os,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_require_customer_reimbursement=lambda: require_customer_reimbursement,
    _cb_resolve_safe_attachment_response=lambda: safe_attachment_response,
    _cb_resolve_send_file=lambda: send_file,
    _cb_resolve_url_for=lambda: url_for,
)
download_customer_reimbursement_attachment = _views_settlement_attachments['download_customer_reimbursement_attachment']
preview_customer_reimbursement_attachment = _views_settlement_attachments['preview_customer_reimbursement_attachment']
delete_customer_reimbursement_attachment = _views_settlement_attachments['delete_customer_reimbursement_attachment']






_report_write_views = register_report_write_routes(
    app,
    resolve_REPORT_ATTACHMENTS_DIR=lambda: REPORT_ATTACHMENTS_DIR,
    calculated_report_driving_miles=lambda *args, **kwargs: calculated_report_driving_miles(*args, **kwargs),
    calculated_report_total_service_hours=lambda *args, **kwargs: calculated_report_total_service_hours(*args, **kwargs),
    calculated_report_total_time=lambda *args, **kwargs: calculated_report_total_time(*args, **kwargs),
    can_create_service_report=lambda *args, **kwargs: can_create_service_report(*args, **kwargs),
    claim_report_save_token=lambda *args, **kwargs: claim_report_save_token(*args, **kwargs),
    resolve_date=lambda: date,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    finish_report_save_token=lambda *args, **kwargs: finish_report_save_token(*args, **kwargs),
    get_report_attachments=lambda *args, **kwargs: get_report_attachments(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    is_external_employee=lambda *args, **kwargs: is_external_employee(*args, **kwargs),
    is_external_manager=lambda *args, **kwargs: is_external_manager(*args, **kwargs),
    is_manager=lambda *args, **kwargs: is_manager(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    now=lambda *args, **kwargs: now(*args, **kwargs),
    posted_report_time=lambda *args, **kwargs: posted_report_time(*args, **kwargs),
    posted_report_writer_id=lambda *args, **kwargs: posted_report_writer_id(*args, **kwargs),
    prune_empty_report_folders=lambda *args, **kwargs: prune_empty_report_folders(*args, **kwargs),
    relocate_report_attachments=lambda *args, **kwargs: relocate_report_attachments(*args, **kwargs),
    report_attachment_path=lambda *args, **kwargs: report_attachment_path(*args, **kwargs),
    report_form_defaults=lambda *args, **kwargs: report_form_defaults(*args, **kwargs),
    report_locked_by_approved_reimbursement=lambda *args, **kwargs: report_locked_by_approved_reimbursement(*args, **kwargs),
    report_parts=lambda *args, **kwargs: report_parts(*args, **kwargs),
    report_storage_context_values=lambda *args, **kwargs: report_storage_context_values(*args, **kwargs),
    report_worker_rows_from_form=lambda *args, **kwargs: report_worker_rows_from_form(*args, **kwargs),
    report_writer_options=lambda *args, **kwargs: report_writer_options(*args, **kwargs),
    require_service_order=lambda *args, **kwargs: require_service_order(*args, **kwargs),
    require_service_order_start_date=lambda *args, **kwargs: require_service_order_start_date(*args, **kwargs),
    require_service_report=lambda *args, **kwargs: require_service_report(*args, **kwargs),
    reset_ai_drafts_for_deleted_service_report=lambda *args, **kwargs: reset_ai_drafts_for_deleted_service_report(*args, **kwargs),
    same_order_adjacent_report_ids=lambda *args, **kwargs: same_order_adjacent_report_ids(*args, **kwargs),
    save_report_detail_rows=lambda *args, **kwargs: save_report_detail_rows(*args, **kwargs),
    save_report_uploads=lambda *args, **kwargs: save_report_uploads(*args, **kwargs),
    service_report_worker_options=lambda *args, **kwargs: service_report_worker_options(*args, **kwargs),
    service_report_workers=lambda *args, **kwargs: service_report_workers(*args, **kwargs),
)
new_service_report = _report_write_views['new_service_report']
edit_service_report = _report_write_views['edit_service_report']
delete_service_report = _report_write_views['delete_service_report']




_report_assist_views = register_report_assist_routes(
    app,
    resolve_DATA_DIR=lambda: DATA_DIR,
    resolve_DEFAULT_TRIP_TYPE=lambda: DEFAULT_TRIP_TYPE,
    EmployeeResolutionService=lambda *args, **kwargs: EmployeeResolutionService(*args, **kwargs),
    GoogleRoutesService=lambda *args, **kwargs: GoogleRoutesService(*args, **kwargs),
    GoogleStaticMapsService=lambda *args, **kwargs: GoogleStaticMapsService(*args, **kwargs),
    MileageEvidenceService=lambda *args, **kwargs: MileageEvidenceService(*args, **kwargs),
    PhotoDiscoveryService=lambda *args, **kwargs: PhotoDiscoveryService(*args, **kwargs),
    PhotoMetadataService=lambda *args, **kwargs: PhotoMetadataService(*args, **kwargs),
    resolve_SHARED_PHOTOS_DIR=lambda: SHARED_PHOTOS_DIR,
    ServiceReportAssistService=lambda *args, **kwargs: ServiceReportAssistService(*args, **kwargs),
    ServiceReportEvidenceService=lambda *args, **kwargs: ServiceReportEvidenceService(*args, **kwargs),
    can_create_service_report=lambda *args, **kwargs: can_create_service_report(*args, **kwargs),
    resolve_datetime=lambda: datetime,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    get_google_routes_api_key=lambda *args, **kwargs: get_google_routes_api_key(*args, **kwargs),
    get_google_static_maps_api_key=lambda *args, **kwargs: get_google_static_maps_api_key(*args, **kwargs),
    is_external_employee=lambda *args, **kwargs: is_external_employee(*args, **kwargs),
    is_external_manager=lambda *args, **kwargs: is_external_manager(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    now=lambda *args, **kwargs: now(*args, **kwargs),
    require_service_order=lambda *args, **kwargs: require_service_order(*args, **kwargs),
    require_service_report=lambda *args, **kwargs: require_service_report(*args, **kwargs),
    save_generated_report_attachment=lambda *args, **kwargs: save_generated_report_attachment(*args, **kwargs),
    service_order_photo_folder=lambda *args, **kwargs: service_order_photo_folder(*args, **kwargs),
    service_report_worker_options=lambda *args, **kwargs: service_report_worker_options(*args, **kwargs),
    service_report_workers=lambda *args, **kwargs: service_report_workers(*args, **kwargs),
)
generate_service_report_mileage_evidence = _report_assist_views['generate_service_report_mileage_evidence']
service_report_assist_plan = _report_assist_views['service_report_assist_plan']






def clock_in_site_rows():
    return _photo_capture_services.clock_in_site_rows(db=db, g=g, is_external_user=is_external_user)


_photo_capture_views = register_photo_capture_routes(
    app,
    resolve_ALLOWED_IMAGE_EXTENSIONS=lambda: ALLOWED_IMAGE_EXTENSIONS,
    app_timezone=lambda *args, **kwargs: app_timezone(*args, **kwargs),
    clock_in_site_rows=lambda *args, **kwargs: clock_in_site_rows(*args, **kwargs),
    count_shared_images=lambda *args, **kwargs: count_shared_images(*args, **kwargs),
    resolve_date=lambda: date,
    resolve_datetime=lambda: datetime,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    is_internal_user=lambda *args, **kwargs: is_internal_user(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    now=lambda *args, **kwargs: now(*args, **kwargs),
    order_photo_status=lambda *args, **kwargs: order_photo_status(*args, **kwargs),
    resolve_shared_photo=lambda *args, **kwargs: resolve_shared_photo(*args, **kwargs),
    resolve_shared_picture=lambda *args, **kwargs: resolve_shared_picture(*args, **kwargs),
    shared_photo_relative=lambda *args, **kwargs: shared_photo_relative(*args, **kwargs),
    shared_photos_root=lambda *args, **kwargs: shared_photos_root(*args, **kwargs),
    valid_image_file=lambda *args, **kwargs: valid_image_file(*args, **kwargs),
)
mobile_clock_in = _photo_capture_views['mobile_clock_in']
upload_mobile_clock_in = _photo_capture_views['upload_mobile_clock_in']
browse_shared_photos = _photo_capture_views['browse_shared_photos']
download_shared_photos = _photo_capture_views['download_shared_photos']
shared_photo_preview = _photo_capture_views['shared_photo_preview']
shared_photo_thumbnail = _photo_capture_views['shared_photo_thumbnail']
















def adjacent_service_report_ids(report_id):
    return _report_read_services.adjacent_service_report_ids(report_id, db=db, service_order_access_filters=service_order_access_filters)


def same_order_adjacent_report_ids(report_id):
    return _report_read_services.same_order_adjacent_report_ids(report_id, db=db, g=g, is_external_employee=is_external_employee)


_report_read_views = register_report_read_routes(
    app,
    adjacent_service_report_ids=lambda *args, **kwargs: adjacent_service_report_ids(*args, **kwargs),
    build_service_report_docx=lambda *args, **kwargs: build_service_report_docx(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    get_company_profile=lambda *args, **kwargs: get_company_profile(*args, **kwargs),
    get_report_attachments=lambda *args, **kwargs: get_report_attachments(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    login_required=login_required,
    prune_empty_report_folders=lambda *args, **kwargs: prune_empty_report_folders(*args, **kwargs),
    report_attachment_path=lambda *args, **kwargs: report_attachment_path(*args, **kwargs),
    report_parts=lambda *args, **kwargs: report_parts(*args, **kwargs),
    require_service_report=lambda *args, **kwargs: require_service_report(*args, **kwargs),
    safe_attachment_response=lambda *args, **kwargs: safe_attachment_response(*args, **kwargs),
    service_report_workers=lambda *args, **kwargs: service_report_workers(*args, **kwargs),
)
view_service_report = _report_read_views['view_service_report']
export_service_report = _report_read_views['export_service_report']
preview_report_attachment = _report_read_views['preview_report_attachment']
delete_report_attachment = _report_read_views['delete_report_attachment']




def expense_defaults(expense=None):
    return _expense_forms.expense_defaults(expense, date=date, expense_beneficiary_id=expense_beneficiary_id, g=g, next_expense_number=next_expense_number)


def expense_items(expense_id):
    return _expense_forms.expense_items(expense_id, db=db)


def expense_items_from_form(expense_id=None):
    return _expense_forms.expense_items_from_form(expense_id, db=db, is_fuel_project_name=is_fuel_project_name, re=re, request=request, secrets=secrets, to_float=to_float)


def save_expense_items(expense_id, item_rows):
    return _expense_forms.save_expense_items(expense_id, item_rows, db=db, expense_attachment_path=expense_attachment_path, get_expense_attachments=get_expense_attachments, os=os)


_expense_write_views = register_expense_write_routes(
    app,
    resolve_EXPENSE_STATUS_LABELS=lambda: EXPENSE_STATUS_LABELS,
    can_create_expense=lambda *args, **kwargs: can_create_expense(*args, **kwargs),
    can_delete_expense=lambda *args, **kwargs: can_delete_expense(*args, **kwargs),
    claim_expense_save_token=lambda *args, **kwargs: claim_expense_save_token(*args, **kwargs),
    create_message=lambda *args, **kwargs: create_message(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    employee_expense_block_reason=lambda *args, **kwargs: employee_expense_block_reason(*args, **kwargs),
    expense_beneficiary_id=lambda *args, **kwargs: expense_beneficiary_id(*args, **kwargs),
    expense_beneficiary_options=lambda *args, **kwargs: expense_beneficiary_options(*args, **kwargs),
    expense_defaults=lambda *args, **kwargs: expense_defaults(*args, **kwargs),
    expense_duplicate_checks=lambda *args, **kwargs: expense_duplicate_checks(*args, **kwargs),
    expense_items=lambda *args, **kwargs: expense_items(*args, **kwargs),
    expense_items_from_form=lambda *args, **kwargs: expense_items_from_form(*args, **kwargs),
    finish_expense_save_token=lambda *args, **kwargs: finish_expense_save_token(*args, **kwargs),
    get_expense_attachments=lambda *args, **kwargs: get_expense_attachments(*args, **kwargs),
    group_expense_attachments=lambda *args, **kwargs: group_expense_attachments(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    is_manager=lambda *args, **kwargs: is_manager(*args, **kwargs),
    latest_customer_reimbursement=lambda *args, **kwargs: latest_customer_reimbursement(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    money=lambda *args, **kwargs: money(*args, **kwargs),
    next_expense_number=lambda *args, **kwargs: next_expense_number(*args, **kwargs),
    notify_role=lambda *args, **kwargs: notify_role(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
    posted_expense_beneficiary=lambda *args, **kwargs: posted_expense_beneficiary(*args, **kwargs),
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
    require_service_order=lambda *args, **kwargs: require_service_order(*args, **kwargs),
    require_service_order_start_date=lambda *args, **kwargs: require_service_order_start_date(*args, **kwargs),
    run_expense_duplicate_checks=lambda *args, **kwargs: run_expense_duplicate_checks(*args, **kwargs),
    save_expense_items=lambda *args, **kwargs: save_expense_items(*args, **kwargs),
    save_expense_uploads=lambda *args, **kwargs: save_expense_uploads(*args, **kwargs),
    sync_expense_attachments_to_settlement=lambda *args, **kwargs: sync_expense_attachments_to_settlement(*args, **kwargs),
)
new_expense = _expense_write_views['new_expense']
edit_expense = _expense_write_views['edit_expense']
expense_detail = _expense_write_views['expense_detail']






_expense_review_views = register_expense_review_routes(
    app,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    normalized_role=lambda *args, **kwargs: normalized_role(*args, **kwargs),
    notify_expense_participants=lambda *args, **kwargs: notify_expense_participants(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
)
review_expense_duplicate_check = _expense_review_views['review_expense_duplicate_check']
return_expense = _expense_review_views['return_expense']


_expense_approval_views = register_expense_approval_routes(
    app,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    ensure_expense_payment_order=lambda expense_id: _expense_approval_services.ensure_expense_payment_order(expense_id, ensure_expense_payment_order=ensure_expense_payment_order, db=db, now=now, log_action=log_action, lock_number_allocation=lock_number_allocation, worker_tax_status_lookup=worker_tax_status_lookup),
    expense_beneficiary_id=lambda *args, **kwargs: expense_beneficiary_id(*args, **kwargs),
    latest_customer_reimbursement=lambda *args, **kwargs: latest_customer_reimbursement(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    money=lambda *args, **kwargs: money(*args, **kwargs),
    notify_expense_participants=lambda *args, **kwargs: notify_expense_participants(*args, **kwargs),
    notify_role=lambda *args, **kwargs: notify_role(*args, **kwargs),
    now=lambda *args, **kwargs: now(*args, **kwargs),
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
    update_customer_reimbursement_totals=lambda *args, **kwargs: update_customer_reimbursement_totals(*args, **kwargs),
)
approve_expense = _expense_approval_views['approve_expense']




_expense_cancel_views = register_expense_cancel_routes(
    app,
    cancel_expense_payment_order=lambda expense_id: _expense_cancel_services.cancel_expense_payment_order(expense_id, cancel_expense_payment_order=cancel_expense_payment_order, db=db, now=now, log_action=log_action),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    now=lambda *args, **kwargs: now(*args, **kwargs),
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
)
cancel_expense = _expense_cancel_views['cancel_expense']


_expense_delete_views = register_expense_delete_routes(
    app,
    can_delete_expense=lambda *args, **kwargs: can_delete_expense(*args, **kwargs),
    cancel_expense_payment_order=lambda expense_id: _expense_cancel_services.cancel_expense_payment_order(expense_id, cancel_expense_payment_order=cancel_expense_payment_order, db=db, now=now, log_action=log_action),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    expense_attachment_dir=lambda *args, **kwargs: expense_attachment_dir(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
)
delete_expense = _expense_delete_views['delete_expense']


_expense_attachment_views = register_expense_attachment_routes(
    app,
    resolve_EXPENSE_ATTACHMENTS_DIR=lambda: EXPENSE_ATTACHMENTS_DIR,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    expense_attachment_path=lambda *args, **kwargs: expense_attachment_path(*args, **kwargs),
    login_required=login_required,
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
    run_expense_attachment_interpretation=lambda *args, **kwargs: run_expense_attachment_interpretation(*args, **kwargs),
    safe_attachment_response=lambda *args, **kwargs: safe_attachment_response(*args, **kwargs),
)
preview_expense_attachment = _expense_attachment_views['preview_expense_attachment']
download_expense_attachment = _expense_attachment_views['download_expense_attachment']
get_expense_attachment_interpretation = _expense_attachment_views['get_expense_attachment_interpretation']
interpret_expense_attachment = _expense_attachment_views['interpret_expense_attachment']








_expense_ai_review_views = register_expense_ai_review_routes(
    app,
    resolve_EXPENSE_ATTACHMENTS_DIR=lambda: EXPENSE_ATTACHMENTS_DIR,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    login_required=login_required,
    run_expense_ai_review_task=lambda *args, **kwargs: run_expense_ai_review_task(*args, **kwargs),
)
expense_ai_review = _expense_ai_review_views['expense_ai_review']


_expense_attachment_delete_views = register_expense_attachment_delete_routes(
    app,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    expense_attachment_path=lambda *args, **kwargs: expense_attachment_path(*args, **kwargs),
    is_manager=lambda *args, **kwargs: is_manager(*args, **kwargs),
    login_required=login_required,
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
)
delete_expense_attachment = _expense_attachment_delete_views['delete_expense_attachment']


_expense_transfer_views = register_expense_transfer_routes(
    app,
    DatabaseError=DatabaseError,
    IntegrityError=IntegrityError,
    can_transfer_expense_attachment=lambda *args, **kwargs: can_transfer_expense_attachment(*args, **kwargs),
    copy_file_to_customer_reimbursement_attachment=lambda *args, **kwargs: copy_file_to_customer_reimbursement_attachment(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    expense_attachment_path=lambda *args, **kwargs: expense_attachment_path(*args, **kwargs),
    latest_customer_reimbursement=lambda *args, **kwargs: latest_customer_reimbursement(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_expense=lambda *args, **kwargs: require_expense(*args, **kwargs),
)
transfer_expense_attachment = _expense_transfer_views['transfer_expense_attachment']




@app.route("/invoices/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    if not can_create_invoice():
        abort(403)
    requested_order_id = request.args.get("service_order_id", "")
    requested_reimbursement_id = request.args.get("customer_reimbursement_id", "")
    source_reimbursement = None
    source_order = None
    prefilled_items = []
    if request.method == "GET" and not requested_reimbursement_id.isdigit():
        flash("请从审核通过的工单结算生成发票。", "error")
        if requested_order_id.isdigit():
            return redirect(url_for("customer_reimbursement_form", order_id=int(requested_order_id)))
        return redirect(url_for("service_orders"))
    if request.method == "GET" and requested_order_id.isdigit():
        requested_order = require_service_order(int(requested_order_id))
        start_date_redirect = require_service_order_start_date(requested_order)
        if start_date_redirect:
            return start_date_redirect
    if request.method == "GET" and requested_reimbursement_id.isdigit():
        source_reimbursement, source_order = require_customer_reimbursement(int(requested_reimbursement_id))
        start_date_redirect = require_service_order_start_date(source_order)
        if start_date_redirect:
            return start_date_redirect
        if source_reimbursement["invoice_id"]:
            flash("这份工单结算已经生成过发票。", "success")
            return redirect(url_for("invoice_detail", invoice_id=source_reimbursement["invoice_id"]))
        linked_invoice = service_order_active_invoice(source_order["id"])
        if linked_invoice:
            db().execute(
                "update customer_reimbursements set invoice_id = ? where id = ?",
                (linked_invoice["id"], source_reimbursement["id"]),
            )
            db().commit()
            flash("这个工单已经有关联发票。", "success")
            return redirect(url_for("invoice_detail", invoice_id=linked_invoice["id"]))
        if source_reimbursement["status"] != "approved":
            flash("工单结算审核通过后才能生成发票。", "error")
            return redirect(url_for("customer_reimbursement_form", order_id=source_order["id"]))
        requested_order_id = str(source_order["id"])
        try:
            prefilled_items = customer_reimbursement_invoice_items(source_reimbursement)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("customer_reimbursement_form", order_id=source_order["id"]))
    if is_external_user():
        if not g.user["client_id"]:
            flash("请联系管理员绑定客户。", "error")
            return redirect(url_for("dashboard"))
        clients_rows = db().execute("select * from clients where id = ?", (g.user["client_id"],)).fetchall()
    else:
        clients_rows = db().execute("select * from clients order by client_number").fetchall()
    if not clients_rows:
        flash("请先创建一个客户。", "error")
        return redirect(url_for("clients"))
    projects_rows = db().execute(
        "select * from projects where project_type = 'invoice' and is_active = 1 order by name"
    ).fetchall()
    service_orders_rows = db().execute("select * from service_orders where status != 'closed' order by created_at desc, id desc").fetchall()
    if not projects_rows:
        flash("请先由经理或管理员维护项目。", "error")
        return redirect(url_for("projects") if is_manager() else url_for("dashboard"))
    if request.method == "POST":
        save_token = request.form.get("save_token", "")
        if not claim_invoice_save_token(save_token):
            existing = db().execute(
                "select invoice_id from invoice_save_tokens where token = ?",
                (save_token,),
            ).fetchone()
            if existing and existing["invoice_id"]:
                return redirect(url_for("edit_invoice", invoice_id=existing["invoice_id"]))
            flash("该发票正在保存，请稍候。", "error")
            return redirect(url_for("new_invoice"))
        uploads = uploaded_attachments_from_request()
        if not validate_attachment_uploads(uploads):
            db().rollback()
            return redirect(url_for("new_invoice"))
        try:
            invoice_id = create_invoice_from_form(save_token=save_token)
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
            return redirect(url_for("new_invoice"))
        except IntegrityError:
            db().rollback()
            flash("保存发票时发生数据库冲突，请重新提交。", "error")
            return redirect(url_for("new_invoice"))
        flash("发票已生成。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))
    today = date.today()
    selected_client_id = source_order["client_id"] if source_order else clients_rows[0]["id"]
    selected_client = next((row for row in clients_rows if row["id"] == selected_client_id), clients_rows[0])
    selected_term = db().execute(
        "select * from payment_terms where id = ?",
        (selected_client["payment_term_id"],),
    ).fetchone()
    defaults = {
        "invoice_number": next_invoice_number(),
        "issue_date": today.isoformat(),
        "due_date": calculate_payment_due_date(today.isoformat(), selected_term).isoformat(),
        "service_order_id": int(requested_order_id) if requested_order_id.isdigit() else None,
        "customer_reimbursement_id": source_reimbursement["id"] if source_reimbursement else None,
        "client_id": source_order["client_id"] if source_order else None,
    }
    return render_template(
        "invoice_form.html",
        clients=clients_rows,
        projects=projects_rows,
        service_orders=service_orders_rows,
        defaults=defaults,
        form_title="根据工单结算生成发票" if source_reimbursement else "新建发票",
        form_items=prefilled_items,
        is_edit=False,
        attachments=[],
        save_token=secrets.token_urlsafe(24),
        payment_terms=payment_term_rows(),
    )


def invoice_items_from_form():
    return _invoice_forms.invoice_items_from_form(db=db, request=request, to_float=to_float)


def posted_client_id():
    return _invoice_forms.posted_client_id(request=request)


def posted_service_order_id(require_start_date=False, required=False):
    return _invoice_forms.posted_service_order_id(require_start_date, required, request=request, require_service_order=require_service_order)


def create_invoice_from_form(save_token=None):
    return _split_invoice_create.create_invoice_from_form(save_token, IntegrityError=IntegrityError, abort=abort, can_access_client=can_access_client, copy_customer_reimbursement_attachments_to_invoice=copy_customer_reimbursement_attachments_to_invoice, copy_mileage_proofs_to_invoice=copy_mileage_proofs_to_invoice, db=db, finish_invoice_save_token=finish_invoice_save_token, g=g, invoice_items_from_form=invoice_items_from_form, invoice_number_exists=invoice_number_exists, invoice_totals=invoice_totals, log_action=log_action, money=money, next_invoice_number=next_invoice_number, now=now, posted_client_id=posted_client_id, posted_service_order_id=posted_service_order_id, recognize_invoice_if_accounting_enabled=recognize_invoice_if_accounting_enabled, request=request, save_uploaded_attachment=save_uploaded_attachment, service_order_active_invoice=service_order_active_invoice, to_float=to_float, uploaded_attachments_from_request=uploaded_attachments_from_request)


@app.route("/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_invoice(invoice_id):
    invoice, client, items = load_invoice(invoice_id)
    if invoice["status"] != "draft":
        flash("只有管理员调整为草稿的发票可以编辑。", "error")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))
    if invoice["created_by"] != g.user["id"] and not is_manager():
        abort(403)
    if is_external_user():
        clients_rows = db().execute("select * from clients where id = ?", (g.user["client_id"],)).fetchall()
    else:
        clients_rows = db().execute("select * from clients order by client_number").fetchall()
    projects_rows = db().execute(
        """
        select * from projects
        where project_type = 'invoice' and (is_active = 1 or id in (
            select project_id from invoice_items where invoice_id = ?
        ))
        order by is_active desc, name
        """,
        (invoice_id,),
    ).fetchall()
    service_orders_rows = db().execute("select * from service_orders where status != 'closed' or id = ? order by created_at desc, id desc", (invoice["service_order_id"] or 0,)).fetchall()
    if request.method == "POST":
        save_token = request.form.get("save_token", "")
        if not claim_invoice_save_token(save_token, invoice_id):
            flash("该发票已经保存，请勿重复提交。", "success")
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        uploads = uploaded_attachments_from_request()
        if not validate_attachment_uploads(uploads, invoice_id):
            db().rollback()
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        try:
            update_invoice_from_form(invoice_id)
            log_action("update", "invoice", invoice_id, invoice["invoice_number"], "修改发票内容")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        except IntegrityError:
            db().rollback()
            flash("发票编号已经存在，请更换一个发票编号后再保存。", "error")
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        db().commit()
        flash("发票已保存。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))
    defaults = {
        "id": invoice["id"],
        "invoice_number": invoice["invoice_number"],
        "client_id": invoice["client_id"],
        "created_by": invoice["created_by"],
        "status": invoice["status"],
        "issue_date": invoice["issue_date"],
        "due_date": invoice["due_date"],
        "currency": invoice["currency"],
        "notes": invoice["notes"],
        "service_order_id": invoice["service_order_id"],
    }
    return render_template(
        "invoice_form.html",
        clients=clients_rows,
        projects=projects_rows,
        service_orders=service_orders_rows,
        defaults=defaults,
        form_title="编辑发票",
        form_items=items,
        is_edit=True,
        attachments=get_invoice_attachments(invoice_id),
        save_token=secrets.token_urlsafe(24),
        payment_terms=payment_term_rows(),
    )


def update_invoice_from_form(invoice_id):
    return _split_invoice_update.update_invoice_from_form(invoice_id, abort=abort, accounting_base_enabled=accounting_base_enabled, can_access_client=can_access_client, db=db, invoice_items_from_form=invoice_items_from_form, posted_client_id=posted_client_id, posted_service_order_id=posted_service_order_id, recognize_invoice_if_accounting_enabled=recognize_invoice_if_accounting_enabled, request=request, save_uploaded_attachment=save_uploaded_attachment, uploaded_attachments_from_request=uploaded_attachments_from_request)


_invoice_detail_views = register_invoice_detail_routes(
    app,
    resolve_STATUS_LABELS=lambda: STATUS_LABELS,
    accounting_base_enabled=lambda *args, **kwargs: accounting_base_enabled(*args, **kwargs),
    resolve_date=lambda: date,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    email_delivery_summary=lambda *args, **kwargs: email_delivery_summary(*args, **kwargs),
    get_company_profile=lambda *args, **kwargs: get_company_profile(*args, **kwargs),
    get_invoice_attachments=lambda *args, **kwargs: get_invoice_attachments(*args, **kwargs),
    get_invoice_terms=lambda *args, **kwargs: get_invoice_terms(*args, **kwargs),
    get_payment_instructions=lambda *args, **kwargs: get_payment_instructions(*args, **kwargs),
    invoice_service_order=lambda *args, **kwargs: invoice_service_order(*args, **kwargs),
    invoice_totals=lambda *args, **kwargs: invoice_totals(*args, **kwargs),
    load_invoice=lambda *args, **kwargs: load_invoice(*args, **kwargs),
    login_required=login_required,
    render_template=lambda *args, **kwargs: render_template(*args, **kwargs),
)
invoice_detail = _invoice_detail_views['invoice_detail']


_invoice_admin_status_views = register_invoice_admin_status_routes(
    app,
    create_message=lambda *args, **kwargs: create_message(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
)
admin_update_invoice_status = _invoice_admin_status_views['admin_update_invoice_status']


_invoice_void_views = register_invoice_void_routes(
    app,
    InvoiceRecognitionService=lambda *args, **kwargs: InvoiceRecognitionService(*args, **kwargs),
    InvoiceVoidError=InvoiceVoidError,
    PostingError=PostingError,
    resolve_date=lambda: date,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
)
void_invoice = _invoice_void_views['void_invoice']


_invoice_delete_views = register_invoice_delete_routes(
    app,
    IntegrityError=IntegrityError,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    has_action_permission=lambda *args, **kwargs: has_action_permission(*args, **kwargs),
    invoice_attachment_path=lambda *args, **kwargs: invoice_attachment_path(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
    resolve_shutil=lambda: shutil,
)
delete_invoice = _invoice_delete_views['delete_invoice']


_invoice_payment_views = register_invoice_payment_routes(
    app,
    resolve_date=lambda: date,
    db=lambda *args, **kwargs: db(*args, **kwargs),
    invoice_totals=lambda *args, **kwargs: invoice_totals(*args, **kwargs),
    is_internal_user=lambda *args, **kwargs: is_internal_user(*args, **kwargs),
    log_action=lambda *args, **kwargs: log_action(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
    to_float=lambda *args, **kwargs: to_float(*args, **kwargs),
)
mark_invoice_paid = _invoice_payment_views['mark_invoice_paid']
unmark_invoice_paid = _invoice_payment_views['unmark_invoice_paid']




_invoice_send_views = register_invoice_send_routes(
    app,
    is_external_user=lambda *args, **kwargs: is_external_user(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
    send_invoice_email=lambda *args, **kwargs: send_invoice_email(*args, **kwargs),
)
send_invoice = _invoice_send_views['send_invoice']


_invoice_attachment_write_views = register_invoice_attachment_write_routes(
    app,
    attachment_file_path=lambda *args, **kwargs: attachment_file_path(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    login_required=login_required,
    resolve_os=lambda: os,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
    save_uploaded_attachment=lambda *args, **kwargs: save_uploaded_attachment(*args, **kwargs),
    uploaded_attachments_from_request=lambda *args, **kwargs: uploaded_attachments_from_request(*args, **kwargs),
    validate_attachment_uploads=lambda *args, **kwargs: validate_attachment_uploads(*args, **kwargs),
)
upload_attachment = _invoice_attachment_write_views['upload_attachment']
delete_attachment = _invoice_attachment_write_views['delete_attachment']


_invoice_attachment_read_views = register_invoice_attachment_read_routes(
    app,
    attachment_file_path=lambda *args, **kwargs: attachment_file_path(*args, **kwargs),
    db=lambda *args, **kwargs: db(*args, **kwargs),
    login_required=login_required,
    require_invoice_access=lambda *args, **kwargs: require_invoice_access(*args, **kwargs),
    safe_attachment_response=lambda *args, **kwargs: safe_attachment_response(*args, **kwargs),
)
download_attachment = _invoice_attachment_read_views['download_attachment']
preview_attachment = _invoice_attachment_read_views['preview_attachment']






_invoice_export_views = register_invoice_export_routes(
    app,
    BytesIO=lambda *args, **kwargs: BytesIO(*args, **kwargs),
    build_invoice_zip=lambda *args, **kwargs: build_invoice_zip(*args, **kwargs),
    load_invoice=lambda *args, **kwargs: load_invoice(*args, **kwargs),
    login_required=login_required,
    render_invoice_pdf=lambda *args, **kwargs: render_invoice_pdf(*args, **kwargs),
    secure_filename=lambda *args, **kwargs: secure_filename(*args, **kwargs),
)
export_invoice = _invoice_export_views['export_invoice']
export_invoice_pdf = _invoice_export_views['export_invoice_pdf']




def build_invoice_zip(invoice, client, items):
    return _invoice_exports.build_invoice_zip(invoice, client, items, BytesIO=BytesIO, attachment_file_path=attachment_file_path, get_invoice_attachments=get_invoice_attachments, render_invoice_pdf=render_invoice_pdf, secure_filename=secure_filename, zipfile=zipfile)


def customer_reimbursement_email_attachments(invoice):
    return _invoice_email_attachments.customer_reimbursement_email_attachments(invoice, customer_reimbursement_outgoing_attachments=customer_reimbursement_outgoing_attachments, latest_customer_reimbursement=latest_customer_reimbursement, require_service_order=require_service_order)


def unique_email_attachment_filename(filename, used_names):
    return _invoice_email_attachments.unique_email_attachment_filename(filename, used_names, os=os)


def invoice_email_attachments(invoice, client, items):
    return _invoice_email_attachments.invoice_email_attachments(invoice, client, items, attachment_file_path=attachment_file_path, customer_reimbursement_email_attachments=customer_reimbursement_email_attachments, get_invoice_attachments=get_invoice_attachments, os=os, render_invoice_pdf=render_invoice_pdf, secure_filename=secure_filename, unique_email_attachment_filename=unique_email_attachment_filename)


def render_invoice_export_html(invoice, client, items):
    return _invoice_exports.render_invoice_export_html(invoice, client, items, STATUS_LABELS=STATUS_LABELS, get_company_profile=get_company_profile, get_invoice_terms=get_invoice_terms, get_payment_instructions=get_payment_instructions, invoice_service_order=invoice_service_order, invoice_totals=invoice_totals, render_template=render_template)


def render_invoice_pdf(invoice, client, items):
    return _invoice_pdf.render_invoice_pdf(invoice, client, items, A4=A4, BytesIO=BytesIO, UnicodeCIDFont=UnicodeCIDFont, canvas=canvas, colors=colors, get_company_profile=get_company_profile, get_invoice_terms=get_invoice_terms, get_payment_instructions=get_payment_instructions, invoice_service_order=invoice_service_order, invoice_totals=invoice_totals, money=money, pdf_text=pdf_text, pdfmetrics=pdfmetrics)


def build_service_report_docx(report, order):
    return _report_documents.build_service_report_docx(report, order, BytesIO=BytesIO, Document=Document, Image=Image, ImageOps=ImageOps, Inches=Inches, OxmlElement=OxmlElement, Pt=Pt, RGBColor=RGBColor, WD_ALIGN_PARAGRAPH=WD_ALIGN_PARAGRAPH, WD_CELL_VERTICAL_ALIGNMENT=WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT=WD_TABLE_ALIGNMENT, get_company_profile=get_company_profile, get_report_attachments=get_report_attachments, hours=hours, os=os, qn=qn, report_attachment_path=report_attachment_path, report_parts=report_parts, service_report_workers=service_report_workers)


def send_invoice_email(invoice_id):
    return _split_invoice_mail.send_invoice_email(invoice_id, db=db, get_company_profile=get_company_profile, get_invoice_terms=get_invoice_terms, get_payment_instructions=get_payment_instructions, invoice_email_attachments=invoice_email_attachments, invoice_service_order=invoice_service_order, invoice_totals=invoice_totals, load_invoice=load_invoice, log_action=log_action, now=now, record_email_delivery=record_email_delivery, render_template=render_template, send_email=send_email)


def send_email(to, subject, html, attachments=None):
    return _split_smtp_transport.send_email(to, subject, html, attachments, EmailMessage=EmailMessage, app=app, get_smtp_settings=get_smtp_settings, smtplib=smtplib)


@app.errorhandler(RuntimeError)
def runtime_error(error):
    flash(str(error), "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.errorhandler(403)
def forbidden(error):
    if is_external_user():
        message = "你的外部账号只能查看与自己所属客户相关的工单和工作日报，这张工单不在你的可见范围内。如需访问，请联系内部管理员开通权限。"
    else:
        message = "你没有权限访问这个页面或记录。如需访问，请联系管理员。"
    return (
        render_template(
            "error.html",
            status_code="403",
            title="没有访问权限",
            message=message,
        ),
        403,
    )


@app.errorhandler(404)
def not_found(error):
    app.logger.warning(
        "404 response: method=%s path=%s endpoint=%s description=%s",
        request.method,
        request.path,
        request.endpoint,
        getattr(error, "description", ""),
    )
    return (
        render_template(
            "error.html",
            status_code="404",
            title="没有找到这个页面或记录",
            message="你输入的地址可能不正确，或者这张发票、附件、客户资料已经被删除。",
        ),
        404,
    )


@app.errorhandler(500)
def internal_server_error(error):
    """兜底：任何未捕获异常都显示中文提示页，而不是 Werkzeug 的英文 500 页。

    真实堆栈只进日志（便于排查），页面只给可操作提示。数据是事务性的，
    出错路径都已 rollback，这里明确告知用户「数据没有被改动」。
    """
    app.logger.exception("未处理的服务器错误：%s", error)
    return (
        render_template(
            "error.html",
            status_code="500",
            title="服务器出了点问题",
            message="这个操作没能完成，数据没有被改动。请返回上一页重试；如果反复出现，请把当前页面地址和你做的操作发给管理员。",
        ),
        500,
    )


@app.errorhandler(413)
def request_entity_too_large(error):
    app.logger.warning("413 caught: error=%r content_length=%s max_content_length=%s path=%s environ=%s",
        error, request.content_length, request.max_content_length, request.path,
        {k:v for k,v in request.environ.items() if 'CONTENT' in k.upper() or 'MAX' in k.upper()})
    return (
        render_template(
            "error.html",
            status_code="413",
            title="提交内容过大",
            message="提交的数据超过了服务器限制。请减少同时编辑的行数或稍后重试。",
        ),
        413,
    )


def get_metrics():
    return _split_dashboard_metrics.get_metrics(client_filter_clause=client_filter_clause, db=db, has_action_permission=has_action_permission, invoice_totals=invoice_totals, is_external_user=is_external_user)


def monthly_chart():
    return _split_dashboard_monthly.monthly_chart(client_filter_clause=client_filter_clause, db=db, invoice_totals=invoice_totals)


def available_projects_for_access():
    return _split_dashboard_projects.available_projects_for_access(client_filter_clause=client_filter_clause, db=db)


def dashboard_project_options():
    return _split_dashboard_projects.dashboard_project_options(available_projects_for_access=available_projects_for_access, project_color=project_color)


def report_project_options():
    return _split_dashboard_projects.report_project_options(client_filter_clause=client_filter_clause, db=db, is_external_user=is_external_user)


def monthly_project_chart(selected_project_ids):
    return _split_dashboard_monthly.monthly_project_chart(selected_project_ids, client_filter_clause=client_filter_clause, db=db, project_color=project_color)


def monthly_paid_chart():
    return _split_dashboard_monthly.monthly_paid_chart(client_filter_clause=client_filter_clause, db=db, invoice_totals=invoice_totals)


_user_services = build_user_services(
    db=db,
    now=now,
    is_external_manager=is_external_manager,
)
get_user_attachments = _user_services["get_user_attachments"]
assigned_service_order_ids = _user_services["assigned_service_order_ids"]
save_user_service_order_assignments = _user_services["save_user_service_order_assignments"]
employee_grade_options = _user_services["employee_grade_options"]


_employee_grade_exports = register_employee_grade_routes(
    app,
    db=db,
    now=now,
    login_required=login_required,
    can_manage_employee_grades=can_manage_employee_grades,
    has_action_permission=has_action_permission,
    employee_grade_options=employee_grade_options,
    employee_grade_rate_snapshot=employee_grade_rate_snapshot,
    employee_rate_labels=EMPLOYEE_RATE_LABELS,
    employee_rate_types=EMPLOYEE_RATE_TYPES,
    to_float=to_float,
    log_action=log_action,
)
employee_grade_usage = _employee_grade_exports["employee_grade_usage"]
employee_grade_panel = _employee_grade_exports["employee_grade_panel"]
render_employee_grades_page = _employee_grade_exports["render_employee_grades_page"]
is_member_ajax_request = _employee_grade_exports["is_member_ajax_request"]
employee_grade_member_fragments = _employee_grade_exports["employee_grade_member_fragments"]
employee_grades = _employee_grade_exports["employee_grades"]
set_employee_grade_state = _employee_grade_exports["set_employee_grade_state"]
delete_employee_grade = _employee_grade_exports["delete_employee_grade"]
update_employee_grade_members = _employee_grade_exports["update_employee_grade_members"]


_catalog_services = build_catalog_services(db=db, current_language=current_language)
merge_mro_project_aliases = _catalog_services["merge_mro_project_aliases"]
merge_duplicate_projects = _catalog_services["merge_duplicate_projects"]
project_name_exists = _catalog_services["project_name_exists"]
country_rows = _catalog_services["country_rows"]
country_by_code = _catalog_services["country_by_code"]
country_translations = _catalog_services["country_translations"]
country_from_form = _catalog_services["country_from_form"]

_customer_services = build_customer_services(
    db=db,
    lock_number_allocation=lock_number_allocation,
    now=now,
    normalized_address=normalized_address,
    country_by_code=country_by_code,
)
next_client_number = _customer_services["next_client_number"]
next_buyer_number = _customer_services["next_buyer_number"]
next_owner_number = _customer_services["next_owner_number"]
next_manufacturer_number = _customer_services["next_manufacturer_number"]
unknown_owner = _customer_services["unknown_owner"]
owner_options = _customer_services["owner_options"]
manufacturer_options = _customer_services["manufacturer_options"]
manufacturer_from_form = _customer_services["manufacturer_from_form"]
manufacturer_by_name = _customer_services["manufacturer_by_name"]
normalize_import_header = _customer_services["normalize_import_header"]
get_or_create_owner_by_name = _customer_services["get_or_create_owner_by_name"]
country_from_import_value = _customer_services["country_from_import_value"]
imported_buyer_rows = _customer_services["imported_buyer_rows"]
import_buyers_from_file = _customer_services["import_buyers_from_file"]

_catalog_route_exports = register_catalog_routes(
    app,
    db=db,
    now=now,
    login_required=login_required,
    has_action_permission=has_action_permission,
    to_float=to_float,
    project_type_labels=PROJECT_TYPE_LABELS,
    settlement_expense_field_labels=SETTLEMENT_EXPENSE_FIELD_LABELS,
    customer_reimbursement_invoice_projects=CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS,
    merge_duplicate_projects=merge_duplicate_projects,
    project_name_exists=project_name_exists,
    country_rows=country_rows,
    country_translations=country_translations,
)
work_order_types = _catalog_route_exports["work_order_types"]
edit_work_order_type = _catalog_route_exports["edit_work_order_type"]
delete_work_order_type = _catalog_route_exports["delete_work_order_type"]
projects = _catalog_route_exports["projects"]
edit_project = _catalog_route_exports["edit_project"]
delete_project = _catalog_route_exports["delete_project"]
countries = _catalog_route_exports["countries"]

_customer_route_exports = register_customer_routes(
    app,
    db=db,
    now=now,
    login_required=login_required,
    normalized_role=normalized_role,
    is_external_manager=is_external_manager,
    is_external_user=is_external_user,
    posted_payment_term_id=posted_payment_term_id,
    payment_term_rows=payment_term_rows,
    log_action=log_action,
    has_action_permission=has_action_permission,
    parse_coordinate_pair=parse_coordinate_pair,
    current_language=current_language,
    can_access_buyer=can_access_buyer,
    geocoder_version=GEOCODER_VERSION,
    country_rows=country_rows,
    country_from_form=country_from_form,
    normalized_address=normalized_address,
    next_client_number=next_client_number,
    next_buyer_number=next_buyer_number,
    next_owner_number=next_owner_number,
    next_manufacturer_number=next_manufacturer_number,
    unknown_owner=unknown_owner,
    owner_options=owner_options,
    manufacturer_options=manufacturer_options,
    manufacturer_from_form=manufacturer_from_form,
    import_buyers_from_file=import_buyers_from_file,
)
clients = _customer_route_exports["clients"]
edit_client = _customer_route_exports["edit_client"]
delete_client = _customer_route_exports["delete_client"]
owners = _customer_route_exports["owners"]
edit_owner = _customer_route_exports["edit_owner"]
delete_owner = _customer_route_exports["delete_owner"]
manufacturers = _customer_route_exports["manufacturers"]
edit_manufacturer = _customer_route_exports["edit_manufacturer"]
delete_manufacturer = _customer_route_exports["delete_manufacturer"]
buyers = _customer_route_exports["buyers"]
import_buyers = _customer_route_exports["import_buyers"]
edit_buyer = _customer_route_exports["edit_buyer"]
delete_buyer = _customer_route_exports["delete_buyer"]


_employee_user_exports = register_employee_user_routes(
    app,
    db=db,
    now=now,
    login_required=login_required,
    can_assign_external_employees=can_assign_external_employees,
    country_from_form=country_from_form,
    language_from_form=language_from_form,
    current_language=current_language,
    communication_languages_from_form=communication_languages_from_form,
    can_manage_users=can_manage_users,
    is_external_manager=is_external_manager,
    role_options=ROLE_OPTIONS,
    requires_user_address=requires_user_address,
    save_user_service_order_assignments=save_user_service_order_assignments,
    uploaded_attachments_from_request=uploaded_attachments_from_request,
    save_named_attachment=save_named_attachment,
    user_attachment_dir=USER_ATTACHMENT_DIR,
    get_user_attachments=get_user_attachments,
    assigned_service_order_ids=assigned_service_order_ids,
    can_approve_users=can_approve_users,
    country_rows=country_rows,
    employee_grade_options=employee_grade_options,
    normalized_role=normalized_role,
    default_language_code=DEFAULT_LANGUAGE,
    country_by_code=country_by_code,
    normalize_phone=normalize_phone,
    can_manage_user_record=can_manage_user_record,
    safe_attachment_response=safe_attachment_response,
    create_message=create_message,
    # payroll 服务在下方才构建；延迟到请求期取用，避免注册时的名称顺序问题
    worker_tax_status_current=lambda uid: _payroll_services["worker_tax_status_current"](uid),
)
users = _employee_user_exports["users"]
edit_user = _employee_user_exports["edit_user"]
update_user_status = _employee_user_exports["update_user_status"]
download_user_attachment = _employee_user_exports["download_user_attachment"]
preview_user_attachment = _employee_user_exports["preview_user_attachment"]
delete_user_attachment = _employee_user_exports["delete_user_attachment"]
delete_user = _employee_user_exports["delete_user"]


_knowledge_service = KnowledgeService(
    db=db,
    storage_dir=KNOWLEDGE_BASE_DIR,
    max_pdf_bytes=KNOWLEDGE_MAX_PDF_BYTES,
    max_extracted_text=KNOWLEDGE_MAX_EXTRACTED_TEXT,
)
_knowledge_exports = register_knowledge_routes(
    app,
    service=_knowledge_service,
    db=db,
    now=now,
    app_timezone=app_timezone,
    login_required=login_required,
    log_action=log_action,
)
extract_knowledge_pdf_text = _knowledge_exports["extract_knowledge_pdf_text"]
save_knowledge_pdf_upload = _knowledge_exports["save_knowledge_pdf_upload"]
knowledge_expiry_from_form = _knowledge_exports["knowledge_expiry_from_form"]
knowledge_document_path = _knowledge_exports["knowledge_document_path"]
knowledge_document_or_404 = _knowledge_exports["knowledge_document_or_404"]
knowledge_version_or_404 = _knowledge_exports["knowledge_version_or_404"]
knowledge_version_path = _knowledge_exports["knowledge_version_path"]
knowledge_base = _knowledge_exports["knowledge_base"]
preview_knowledge_document = _knowledge_exports["preview_knowledge_document"]
download_knowledge_document = _knowledge_exports["download_knowledge_document"]
edit_knowledge_document = _knowledge_exports["edit_knowledge_document"]
upload_knowledge_document_version = _knowledge_exports["upload_knowledge_document_version"]
preview_knowledge_document_version = _knowledge_exports["preview_knowledge_document_version"]
download_knowledge_document_version = _knowledge_exports["download_knowledge_document_version"]
delete_knowledge_document = _knowledge_exports["delete_knowledge_document"]

register_field_routes(app, globals())
register_staff_reports(app, globals())
register_rate_routes(app, globals())
register_settlement_review_routes(app, globals())
register_profitability_routes(app, globals())
register_order_data_transfer_routes(app, globals())
register_travel_tools_routes(app, globals())
register_airbnb_receipt_routes(app, globals())

# ---------------------------------------------------------------------------
# Payroll / 工时：计算与服务实现放在 invoice_tool/payroll/ 下，这里只做组装。
#   - calculations.py  纯计算（工时拆分、工资聚合），不碰 db / flask
#   - services.py      取数 + 组装，依赖通过 api（= 本模块 globals()）注入
#   - routes.py        路由，URL / endpoint / 权限判断一律不动
# 下面这些名字保留在根模块：既不给外部调用方（scripts、employee_finance、
# 既有测试）制造断层，也让 monkeypatch 继续生效（所有依赖都在调用时解析）。
# ---------------------------------------------------------------------------
# Payroll Cycle Correction（Option C）：冻结保全纠偏的统一读层。
# 工资明细的有效归属一律经过它 —— Annual / CPA / Statement / Batch / Ledger /
# Payroll totals / integrity 都从这里取，任何页面不得自己解释
# payroll_component_correction_allocations。
_payroll_correction_services = build_payroll_correction_services(globals())
correction_component_cte = _payroll_correction_services["correction_component_cte"]
standalone_component_cte = _payroll_correction_services["standalone_component_cte"]
excluded_component_clause = _payroll_correction_services["excluded_component_clause"]
choose_duplicate_keeper = _payroll_correction_services["choose_duplicate_keeper"]
excluded_component_params = _payroll_correction_services["excluded_component_params"]
component_correction_state = _payroll_correction_services["component_correction_state"]
payable_order_clause = _payroll_correction_services["payable_order_clause"]
payable_order_params = _payroll_correction_services["payable_order_params"]
payable_totals = _payroll_correction_services["payable_totals"]
payment_batch_block_reason = _payroll_correction_services["payment_batch_block_reason"]
correction_banner = _payroll_correction_services["correction_banner"]
applied_correction_group = _payroll_correction_services["applied_correction_group"]
attach_carry_forward = _payroll_correction_services["attach_carry_forward"]
flag_carry_forward_components = _payroll_correction_services["flag_carry_forward_components"]
carry_forward_identities = _payroll_correction_services["carry_forward_identities"]
carry_forward_amount = _payroll_correction_services["carry_forward_amount"]
replacement_closure_report = _payroll_correction_services["replacement_closure_report"]
payroll_correction_integrity_check = _payroll_correction_services[
    "payroll_correction_integrity_check"]
correction_dispositions = _payroll_correction_services["correction_dispositions"]
excluded_dispositions = _payroll_correction_services["excluded_dispositions"]
correction_statuses = _payroll_correction_services["correction_statuses"]
non_payable_correction_statuses = _payroll_correction_services[
    "non_payable_correction_statuses"]
batch_blocked_correction_statuses = _payroll_correction_services[
    "batch_blocked_correction_statuses"]
correction_status_labels = _payroll_correction_services["correction_status_labels"]
correction_disposition_labels = _payroll_correction_services["correction_disposition_labels"]

_payroll_services = build_payroll_services(globals())
split_report_labor_hours = _payroll_services["split_report_labor_hours"]
labor_report_entries = _payroll_services["labor_report_entries"]
payroll_rows_for_range = _payroll_services["payroll_rows_for_range"]
payroll_rows_for_period = _payroll_services["payroll_rows_for_period"]
payroll_row_export = _payroll_services["payroll_row_export"]
payroll_payslip_payload = _payroll_services["payroll_payslip_payload"]
payroll_period_dates = _payroll_services["payroll_period_dates"]
payroll_calendar_weeks = _payroll_services["payroll_calendar_weeks"]
payroll_cycle_start_date = _payroll_services["payroll_cycle_start_date"]
payroll_historical_paid_date = _payroll_services["payroll_historical_paid_date"]
payroll_periods_for_month = _payroll_services["payroll_periods_for_month"]
payroll_batch_payload = _payroll_services["payroll_batch_payload"]
current_payroll_period_start = _payroll_services["current_payroll_period_start"]
effective_payroll_worker_id = _payroll_services["effective_payroll_worker_id"]
historical_payroll_period = _payroll_services["historical_payroll_period"]
# Phase 2A：税务组件快照（生成 SL 时冻结 employee_payment_components）
payroll_payment_components = _payroll_services["payroll_payment_components"]
offline_salary_payment_components = _payroll_services["offline_salary_payment_components"]
component_tax_config_lookup = _payroll_services["component_tax_config_lookup"]
worker_tax_status_lookup = _payroll_services["worker_tax_status_lookup"]
mileage_evidence_lookup = _payroll_services["mileage_evidence_lookup"]
# Phase 2B：W-2 / 1099 税务身份历史（当前身份一律按日期查 history）
worker_tax_status_entries = _payroll_services["worker_tax_status_entries"]
create_worker_tax_status_entry = _payroll_services["create_worker_tax_status_entry"]
worker_tax_status_current = _payroll_services["worker_tax_status_current"]
# Phase 4A：Tax Review 工作台（组件快照只读，复核只追加 review 行）
tax_review_rows = _payroll_services["tax_review_rows"]
tax_review_detail = _payroll_services["tax_review_detail"]
tax_review_history = _payroll_services["tax_review_history"]
record_tax_review = _payroll_services["record_tax_review"]
payment_tax_components = _payroll_services["payment_tax_components"]
# Phase 4B：Annual Tax Summary（只读 reporting，绝不写业务表）
# 注意：服务层与路由层同名会互相覆盖（路由函数名决定 endpoint，不能改），
# 所以服务在 globals 里一律用 *_rows 名字暴露，路由内部也按这个名字取。
annual_tax_summary_rows = _payroll_services["annual_tax_summary"]
annual_tax_summary_options = _payroll_services["annual_tax_summary_options"]
annual_tax_summary_component_rows = _payroll_services["annual_tax_summary_components"]
tax_policy_decision_packages = _payroll_services["tax_policy_decision_packages"]
tax_policy_simulation = _payroll_services["simulate_tax_policy_decision"]
# Phase 6B：Resolution Queue 摘要 + CPA Workpaper（只读）
tax_review_queue_summaries = _payroll_services["tax_review_queue_summaries"]
closing_exceptions = _payroll_services["closing_exceptions"]
cpa_workpaper_rows = _payroll_services["cpa_workpaper"]
_payroll_routes = register_payroll_routes(app, globals())
payroll_subsidies = _payroll_routes["payroll_subsidies"]
labor_hours_report = _payroll_routes["labor_hours_report"]
payroll_report = _payroll_routes["payroll_report"]
payroll_detail_report = _payroll_routes["payroll_detail_report"]
payroll_calendar = _payroll_routes["payroll_calendar"]
payroll_calendar_batch = _payroll_routes["payroll_calendar_batch"]
payroll_calendar_export = _payroll_routes["payroll_calendar_export"]
worker_tax_status_history = _payroll_routes["worker_tax_status_history"]
tax_review = _payroll_routes["tax_review"]
tax_review_component = _payroll_routes["tax_review_component"]
save_tax_review = _payroll_routes["save_tax_review"]
annual_tax_summary = _payroll_routes["annual_tax_summary"]
annual_tax_summary_components = _payroll_routes["annual_tax_summary_components"]
annual_tax_summary_export = _payroll_routes["annual_tax_summary_export"]
policy_decisions = _payroll_routes["policy_decisions"]
policy_decisions_export = _payroll_routes["policy_decisions_export"]
cpa_workpaper = _payroll_routes["cpa_workpaper"]
cpa_workpaper_export = _payroll_routes["cpa_workpaper_export"]

register_employee_finance_routes(app, globals())
register_expense_smart_fill_routes(app, globals())
register_order_employee_settlement_routes(app, globals())

# ---------------------------------------------------------------------------
# Quotation / 报价单：业务模块在 invoice_tool/quotations/ 下，这里只做组装。
#   - documents.py  模板版式常量与 PDF 生成（对齐 Quote Template v2）
#   - routes.py     路由 / 表单解析 / 编号 / 权限判断
# ---------------------------------------------------------------------------
_quotation_exports = register_quotation_routes(app, globals())
_accounting_route_exports = register_accounting_routes(app, globals())


def can_view_quotations():
    return _quotation_exports["can_view_quotations"](globals())


def can_manage_quotations():
    return _quotation_exports["can_manage_quotations"](globals())


def next_quotation_number():
    return _quotation_exports["next_quotation_number"](globals())


app.jinja_env.globals["can_view_quotations"] = can_view_quotations
app.jinja_env.globals["can_manage_quotations"] = can_manage_quotations


if __name__ == "__main__":
    verify_data_directory_identity()
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
else:
    verify_data_directory_identity()
    init_db()
