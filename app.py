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
from invoice_tool.invoices import editor_routes as _split_invoice_editor
from invoice_tool.authorization import policy_helpers as _split_authorization
from invoice_tool.ai_daily_report_web import workflow_helpers as _split_ai_workflow
from invoice_tool.authentication import identity_helpers as _split_identity
from invoice_tool.ai_daily_report_web import access_helpers as _split_ai_access
from invoice_tool.customer_reimbursements import report_locks as _split_report_locks
from invoice_tool.presentation import formatting as _split_formatting
from invoice_tool.app_settings import ai_helpers as _split_ai_settings
from invoice_tool.service_orders import validation_helpers as _split_order_validation
from invoice_tool.employee_expenses import numbering as _split_expense_numbering
from invoice_tool.photo_imports import storage_helpers as _split_photo_storage
from invoice_tool.photo_imports import google_helpers as _split_google_photos
from invoice_tool.attachments import response_helpers as _split_attachment_response
from invoice_tool.customer_reimbursements import support_helpers as _split_settlement_support
from invoice_tool.audit_log import record_helpers as _split_audit
from invoice_tool.geocoding import lookup_helpers as _split_geocoding
from invoice_tool.messages import query_helpers as _split_messages
from invoice_tool.ai_assistant import query_helpers as _split_assistant_helpers
from invoice_tool.ai_daily_report_web import manifest_actions as _split_manifest_delete
from invoice_tool.geocoding import request_transport as _geocoder_request
from invoice_tool.app_bootstrap import startup_checks as _split_residual_bootstrap
from invoice_tool.user_preferences import language_helpers as _split_residual_language
from invoice_tool.app_settings import profile_helpers as _split_residual_settings
from invoice_tool.employee_expenses import project_policy as _split_residual_expense_policy
from invoice_tool.presentation import value_helpers as _split_residual_formatting
from invoice_tool.database_console import statement_helpers as _split_residual_sql
from invoice_tool.service_orders import start_date_helpers as _split_residual_order_guard
from invoice_tool.photo_imports import path_helpers as _split_residual_photo_paths
from invoice_tool.customer_reimbursements import path_helpers as _split_residual_settlement_paths
from invoice_tool.customer_reimbursements import value_helpers as _split_residual_settlement_values
from invoice_tool.messages import content_helpers as _split_residual_messages
from invoice_tool.audit_log import request_helpers as _split_residual_audit
from invoice_tool.geocoding import address_helpers as _split_residual_geo_values
from invoice_tool.ai_daily_report_web import value_helpers as _split_residual_ai_values
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
    return _split_authorization.permission_tree_groups(DEFAULT_MENU_ROLES=DEFAULT_MENU_ROLES, MENU_PERMISSION_GROUPS=MENU_PERMISSION_GROUPS, ROLE_ACTION_PERMISSION_GROUPS=ROLE_ACTION_PERMISSION_GROUPS)

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
    return _split_residual_bootstrap.verify_data_directory_identity(DATA_DIRECTORY_IDENTITY=DATA_DIRECTORY_IDENTITY, DATA_IDENTITY_FILE=DATA_IDENTITY_FILE, PostgreSQLConnection=PostgreSQLConnection, REQUIRE_DATA_DIRECTORY_IDENTITY=REQUIRE_DATA_DIRECTORY_IDENTITY)


# MRO 项目合并后的规范全名：裸名（MRO Supplies / MroSupplies）一律归到它名下。












@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def reset_ai_drafts_for_deleted_service_report(connection, report_id):
    return _split_ai_workflow.reset_ai_drafts_for_deleted_service_report(connection, report_id, now=now)


def ensure_postgres_admin():
    return _split_residual_bootstrap.ensure_postgres_admin(IntegrityError=IntegrityError, app=app, db=db, generate_password_hash=generate_password_hash, now=now, os=os)


def init_db():
    return _split_residual_bootstrap.init_db(ensure_postgres_admin=ensure_postgres_admin, verify_postgres_schema=verify_postgres_schema)


def current_language():
    return _split_residual_language.current_language(DEFAULT_LANGUAGE=DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES=SUPPORTED_LANGUAGES, session=session)


def language_from_form(default=DEFAULT_LANGUAGE):
    return _split_residual_language.language_from_form(default, DEFAULT_LANGUAGE=DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES=SUPPORTED_LANGUAGES, request=request)


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
    return _split_residual_language.clear_session_preserving_language(current_language=current_language, session=session)










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
    return _split_residual_settings.inspection_cycle_days(setting_int=setting_int)


def inspection_warning_days():
    return _split_residual_settings.inspection_warning_days(setting_int=setting_int)


def positive_int(value, default=1):
    return _split_setting_float.positive_int(value, default)


def payroll_subsidy_settings():
    return _split_subsidy_settings.payroll_subsidy_settings(get_setting=get_setting, setting_float=setting_float)


def lodging_reimbursement_limit():
    return _split_residual_settings.lodging_reimbursement_limit(payroll_subsidy_settings=payroll_subsidy_settings)


def is_lodging_project_name(name):
    return _split_residual_expense_policy.is_lodging_project_name(name)


def is_fuel_project_name(name):
    return _split_residual_expense_policy.is_fuel_project_name(name)


def validate_lodging_reimbursement(amount, label="住宿报销"):
    return _split_subsidy_settings.validate_lodging_reimbursement(amount, label, lodging_reimbursement_limit=lodging_reimbursement_limit)


def validate_fuel_reimbursement_allowed(amount, label="油费报销"):
    return _split_residual_expense_policy.validate_fuel_reimbursement_allowed(amount, label, payroll_subsidy_settings=payroll_subsidy_settings)


def get_timezone_name():
    return _split_timezone.get_timezone_name(DEFAULT_TIMEZONE=DEFAULT_TIMEZONE, get_setting=get_setting)


def app_timezone():
    return _split_timezone.app_timezone(DEFAULT_TIMEZONE=DEFAULT_TIMEZONE, ZoneInfo=ZoneInfo, ZoneInfoNotFoundError=ZoneInfoNotFoundError, get_timezone_name=get_timezone_name)


def get_company_profile():
    return _split_residual_settings.get_company_profile(DEFAULT_COMPANY_PROFILE=DEFAULT_COMPANY_PROFILE, get_setting=get_setting)


def get_payment_instructions():
    return _split_residual_settings.get_payment_instructions(DEFAULT_PAYMENT_INSTRUCTIONS=DEFAULT_PAYMENT_INSTRUCTIONS, get_setting=get_setting)


def get_invoice_terms():
    return _split_residual_settings.get_invoice_terms(DEFAULT_INVOICE_TERMS=DEFAULT_INVOICE_TERMS, get_setting=get_setting)


def get_smtp_settings():
    return _split_residual_settings.get_smtp_settings(DEFAULT_SMTP_SETTINGS=DEFAULT_SMTP_SETTINGS, get_setting=get_setting)


def headquarters_coordinates():
    return _split_headquarters.headquarters_coordinates(HEADQUARTERS_LATITUDE=HEADQUARTERS_LATITUDE, HEADQUARTERS_LONGITUDE=HEADQUARTERS_LONGITUDE, math=math)


def get_google_maps_browser_api_key():
    return _split_residual_settings.get_google_maps_browser_api_key(GOOGLE_MAPS_BROWSER_API_KEY_ENV=GOOGLE_MAPS_BROWSER_API_KEY_ENV, get_setting=get_setting)


def get_google_geocoding_api_key():
    return _split_residual_settings.get_google_geocoding_api_key(GOOGLE_GEOCODING_API_KEY_ENV=GOOGLE_GEOCODING_API_KEY_ENV, get_setting=get_setting)


def get_google_routes_api_key():
    return _split_residual_settings.get_google_routes_api_key(GOOGLE_ROUTES_API_KEY_ENV=GOOGLE_ROUTES_API_KEY_ENV, get_setting=get_setting)


def get_google_static_maps_api_key():
    return _split_residual_settings.get_google_static_maps_api_key(GOOGLE_STATIC_MAPS_API_KEY_ENV=GOOGLE_STATIC_MAPS_API_KEY_ENV, get_setting=get_setting)


def get_google_places_api_key():
    return _split_headquarters.get_google_places_api_key(GOOGLE_PLACES_API_KEY_ENV=GOOGLE_PLACES_API_KEY_ENV, get_google_geocoding_api_key=get_google_geocoding_api_key, get_setting=get_setting)


def save_named_attachment(uploaded, directory, table, owner_column=None, owner_id=None):
    return _split_named_attachment.save_named_attachment(uploaded, directory, table, owner_column, owner_id, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, db=db, g=g, now=now, os=os, secrets=secrets)


def get_company_attachments():
    return _split_company_attachments.get_company_attachments(db=db)








@app.template_filter("nl2br")
def nl2br_filter(value):
    return _split_residual_formatting.nl2br_filter(value, Markup=Markup, escape=escape)


def current_user():
    return _split_identity.current_user(clear_session_preserving_language=clear_session_preserving_language, db=db, session=session)


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
    return _split_identity.login_required(view, g=g, redirect=redirect, request=request, url_for=url_for, wraps=wraps)


def is_safe_redirect_target(target):
    return _split_identity.is_safe_redirect_target(target, unquote=unquote, urlsplit=urlsplit)


def admin_required(view):
    return _split_identity.admin_required(view, abort=abort, g=g, redirect=redirect, request=request, url_for=url_for, wraps=wraps)


def is_external_user():
    return _split_identity.is_external_user(g=g, normalized_role=normalized_role)


def is_external_manager():
    return _split_identity.is_external_manager(g=g, normalized_role=normalized_role)


def is_external_employee():
    return _split_identity.is_external_employee(g=g, normalized_role=normalized_role)


def normalized_role(role=None):
    return _split_identity.normalized_role(role, g=g)


def requires_user_address(role):
    return _split_identity.requires_user_address(role, normalized_role=normalized_role)


def is_internal_user():
    return _split_identity.is_internal_user(g=g, normalized_role=normalized_role)


# ─── Phase 6: AI Daily Report CSRF Protection ──────────────────────────────

def ai_daily_report_csrf_token():
    return _split_ai_access.ai_daily_report_csrf_token(secrets=secrets, session=session)


def require_ai_daily_report_csrf():
    return _split_ai_access.require_ai_daily_report_csrf(abort=abort, request=request, secrets=secrets, session=session)


# ─── Phase 6: AI Daily Report Authorization Helpers ────────────────────────

def can_view_ai_daily_report_draft(user, draft_row):
    return _split_ai_access.can_view_ai_daily_report_draft(user, draft_row, json=json)


def can_edit_ai_daily_report_draft(user, draft_row):
    return _split_ai_access.can_edit_ai_daily_report_draft(user, draft_row, can_view_ai_daily_report_draft=can_view_ai_daily_report_draft)


def can_prepare_attachment_manifest(user, draft_row):
    return _split_ai_access.can_prepare_attachment_manifest(user, draft_row, can_view_ai_daily_report_draft=can_view_ai_daily_report_draft)


def can_formal_save_draft(user, draft_row):
    return _split_ai_access.can_formal_save_draft(user, draft_row, can_view_ai_daily_report_draft=can_view_ai_daily_report_draft)


def ai_daily_report_draft_list_filters(user):
    return _split_ai_access.ai_daily_report_draft_list_filters(user)


def is_manager():
    return _split_identity.is_manager(g=g, normalized_role=normalized_role)


def can_approve_users():
    return _split_authorization.can_approve_users(g=g, has_action_permission=has_action_permission)


def can_view_invoices():
    return _split_authorization.can_view_invoices(g=g, has_action_permission=has_action_permission)


def can_view_contracts():
    return _split_authorization.can_view_contracts(g=g, has_action_permission=has_action_permission)


def can_manage_contracts():
    return _split_authorization.can_manage_contracts(g=g, has_action_permission=has_action_permission)


def can_create_invoice():
    return _split_authorization.can_create_invoice(g=g, has_action_permission=has_action_permission)


def can_create_service_order():
    return _split_authorization.can_create_service_order(g=g, has_action_permission=has_action_permission)


def can_delete_service_order():
    return _split_authorization.can_delete_service_order(g=g, has_action_permission=has_action_permission)


def can_create_expense():
    return _split_authorization.can_create_expense(g=g, has_action_permission=has_action_permission)


def can_manage_customer_reimbursement():
    return _split_access.can_manage_customer_reimbursement(g=g, has_action_permission=has_action_permission)


# v0.1.239: 工单结算的「修改状态」「审核」「删除」改为完全由菜单权限配置决定
# （权限管理页面可勾选），默认财务/经理/管理员均有权限。
def can_approve_customer_reimbursement():
    return _split_authorization.can_approve_customer_reimbursement(g=g, has_action_permission=has_action_permission)


def can_reset_customer_reimbursement():
    return _split_authorization.can_reset_customer_reimbursement(g=g, has_action_permission=has_action_permission)


def can_delete_customer_reimbursement():
    return _split_authorization.can_delete_customer_reimbursement(g=g, has_action_permission=has_action_permission)


def can_transfer_expense_attachment(expense):
    return _split_authorization.can_transfer_expense_attachment(expense, g=g, has_action_permission=has_action_permission)


def can_manage_employee_grades():
    return _split_authorization.can_manage_employee_grades(g=g, has_action_permission=has_action_permission)


def can_view_labor_payroll_reports():
    return _split_authorization.can_view_labor_payroll_reports(g=g, has_action_permission=has_action_permission)


def can_view_customer_reimbursement():
    return _split_authorization.can_view_customer_reimbursement(g=g, has_action_permission=has_action_permission)


def order_has_approved_reimbursement(order_id):
    return _split_report_locks.order_has_approved_reimbursement(order_id, db=db)


def report_locked_by_approved_reimbursement(order):
    return _split_report_locks.report_locked_by_approved_reimbursement(order, flash=flash, order_has_approved_reimbursement=order_has_approved_reimbursement)


def can_create_service_report(order=None):
    return _split_authorization.can_create_service_report(order, can_access_service_order=can_access_service_order, g=g, has_action_permission=has_action_permission, is_external_employee=is_external_employee, is_external_user=is_external_user)


def can_manage_users():
    return _split_authorization.can_manage_users(g=g, has_action_permission=has_action_permission, is_external_user=is_external_user)


def can_manage_company_info():
    return _split_authorization.can_manage_company_info(g=g, has_action_permission=has_action_permission)


def can_assign_external_employees():
    return _split_authorization.can_assign_external_employees(can_manage_users=can_manage_users, is_external_manager=is_external_manager)


def can_manage_user_record(user):
    return _split_authorization.can_manage_user_record(user, can_manage_users=can_manage_users, g=g, is_external_manager=is_external_manager, normalized_role=normalized_role)


def can_manage_buyers():
    return _split_authorization.can_manage_buyers(g=g, has_action_permission=has_action_permission)


def can_manage_owners():
    return _split_authorization.can_manage_owners(g=g, has_action_permission=has_action_permission)


def can_manage_manufacturers():
    return _split_authorization.can_manage_manufacturers(g=g, has_action_permission=has_action_permission)


def can_access_buyer(buyer):
    return _split_authorization.can_access_buyer(buyer, can_manage_buyers=can_manage_buyers, g=g, is_external_manager=is_external_manager, is_external_user=is_external_user)


def can_view_audit_logs():
    return _split_authorization.can_view_audit_logs(g=g, has_action_permission=has_action_permission)


def can_use_database_console():
    return _split_authorization.can_use_database_console(g=g, has_action_permission=has_action_permission)


def menu_permission_overrides():
    return _split_authorization.menu_permission_overrides(db=db, g=g)


def has_menu_permission(menu_key, role=None):
    return _split_authorization.has_menu_permission(menu_key, role, DEFAULT_ACTION_ROLES=DEFAULT_ACTION_ROLES, DEFAULT_MENU_ROLES=DEFAULT_MENU_ROLES, ROLE_OPTIONS=ROLE_OPTIONS, action_permission_overrides=action_permission_overrides, g=g, menu_permission_overrides=menu_permission_overrides, normalized_role=normalized_role)


def action_permission_overrides():
    return _split_authorization.action_permission_overrides(db=db, g=g)


def has_action_permission(resource_key, action_key, role=None):
    return _split_authorization.has_action_permission(resource_key, action_key, role, DEFAULT_ACTION_ROLES=DEFAULT_ACTION_ROLES, DEFAULT_MENU_ROLES=DEFAULT_MENU_ROLES, MANAGER_FINANCE_INHERITANCE_EXCEPTIONS=MANAGER_FINANCE_INHERITANCE_EXCEPTIONS, ROLE_OPTIONS=ROLE_OPTIONS, action_permission_overrides=action_permission_overrides, g=g, menu_permission_overrides=menu_permission_overrides, normalized_role=normalized_role)


def required_action_for_request():
    return _split_authorization.required_action_for_request(g=g, request=request)


def can_access_client(client_id):
    return _split_authorization.can_access_client(client_id, g=g, is_external_manager=is_external_manager, is_internal_user=is_internal_user)


def require_invoice_access(invoice_id):
    return _split_invoice_access.require_invoice_access(invoice_id, abort=abort, can_access_client=can_access_client, can_view_invoices=can_view_invoices, db=db)


def require_contract_access(contract_id):
    return _split_authorization.require_contract_access(contract_id, abort=abort, can_access_client=can_access_client, can_view_contracts=can_view_contracts, db=db)


def client_filter_clause(alias="invoices"):
    return _split_authorization.client_filter_clause(alias, g=g, is_external_employee=is_external_employee, is_external_manager=is_external_manager)


def role_label(role):
    return _split_formatting.role_label(role)


def state_label(state_code):
    return _split_formatting.state_label(state_code, current_language=current_language, localized_state_name=localized_state_name)


def money(value, currency="USD"):
    return _split_formatting.money(value, currency)


def hours(value):
    return _split_formatting.hours(value)


PROJECT_COLORS = ["#0f766e", "#175cd3", "#b42318", "#7a271a", "#6941c6", "#027a48", "#b54708", "#3538cd"]


def project_color(project_id):
    return _split_residual_formatting.project_color(project_id, PROJECT_COLORS=PROJECT_COLORS)


def pdf_text(value):
    return _split_residual_formatting.pdf_text(value)


def to_float(value, default=0):
    return _split_residual_formatting.to_float(value, default)


CENT = Decimal("0.01")


def decimal_value(value, default="0"):
    return _split_residual_formatting.decimal_value(value, default, Decimal=Decimal, InvalidOperation=InvalidOperation)


def money_decimal(value):
    return _split_residual_formatting.money_decimal(value, CENT=CENT, Decimal=Decimal, ROUND_HALF_UP=ROUND_HALF_UP, decimal_value=decimal_value)


def money_float(value):
    return _split_residual_formatting.money_float(value, money_decimal=money_decimal)


def invoice_totals(invoice_id):
    return _split_invoice_totals.invoice_totals(invoice_id, db=db)


def accounting_base_enabled(connection=None):
    return _split_ai_settings.accounting_base_enabled(connection, db=db)


def recognize_invoice_if_accounting_enabled(invoice_id, *, source_version=1):
    return _split_invoice_recognition.recognize_invoice_if_accounting_enabled(invoice_id, source_version=source_version, InvoiceRecognitionService=InvoiceRecognitionService, accounting_base_enabled=accounting_base_enabled, db=db, g=g)


def payment_label(invoice):
    return _split_formatting.payment_label(invoice)


def local_datetime(value):
    return _split_formatting.local_datetime(value, app_timezone=app_timezone, datetime=datetime, timezone=timezone)


def sql_statement_kind(sql):
    return _split_residual_sql.sql_statement_kind(sql)


def is_read_sql(sql):
    return _split_residual_sql.is_read_sql(sql, sql_statement_kind=sql_statement_kind)


def client_order_number_warning(customer_name, client_order_number):
    return _split_order_validation.client_order_number_warning(customer_name, client_order_number)


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
    return _split_expense_numbering.next_expense_number(date=date, db=db, lock_number_allocation=lock_number_allocation, set_setting=set_setting, setting_int=setting_int)


def invoice_number_exists(invoice_number, exclude_invoice_id=None):
    return _invoice_numbering.invoice_number_exists(invoice_number, exclude_invoice_id, db=db)


def allowed_attachment(filename):
    return _split_residual_photo_paths.allowed_attachment(filename, ALLOWED_ATTACHMENT_EXTENSIONS=ALLOWED_ATTACHMENT_EXTENSIONS)


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
    return _split_authorization.can_access_service_order(order, db=db, g=g, is_external_employee=is_external_employee, is_external_manager=is_external_manager, is_internal_user=is_internal_user)


def require_service_order(order_id):
    return _split_authorization.require_service_order(order_id, abort=abort, can_access_service_order=can_access_service_order, current_language=current_language, db=db)


def service_order_dependency_counts(order_id):
    return _service_order_services.service_order_dependency_counts(order_id, db=db)


def service_order_delete_blockers(order_id):
    return _service_order_services.service_order_delete_blockers(order_id, service_order_dependency_counts=service_order_dependency_counts)


def require_service_order_start_date(order):
    return _split_residual_order_guard.require_service_order_start_date(order, flash=flash, redirect=redirect, url_for=url_for)


def require_service_report(report_id):
    return _split_authorization.require_service_report(report_id, abort=abort, db=db, require_service_order=require_service_order)


def allowed_image(filename):
    return _split_residual_photo_paths.allowed_image(filename, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS)


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
    return _split_photo_storage.file_sha256(path, hashlib=hashlib)


def normalized_report_photo_name(filename):
    return _report_storage.normalized_report_photo_name(filename, os=os, re=re)


def is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
    return _report_storage.is_duplicate_report_photo(report_id, category, original_filename, candidate_hash, db=db, file_sha256=file_sha256, normalized_report_photo_name=normalized_report_photo_name, os=os, report_attachment_path=report_attachment_path)


def save_report_attachment(report_id, uploaded, category, storage_context=None):
    return _report_storage.save_report_attachment(report_id, uploaded, category, storage_context, allowed_image=allowed_image, compress_report_image=compress_report_image, db=db, file_sha256=file_sha256, g=g, is_duplicate_report_photo=is_duplicate_report_photo, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_relative_path=report_attachment_relative_path, secrets=secrets)


def save_report_file_attachment(report_id, uploaded, category, storage_context=None):
    return _report_storage.save_report_file_attachment(report_id, uploaded, category, storage_context, ALLOWED_ATTACHMENT_LABEL=ALLOWED_ATTACHMENT_LABEL, allowed_attachment=allowed_attachment, db=db, file_sha256=file_sha256, g=g, is_duplicate_report_photo=is_duplicate_report_photo, now=now, os=os, report_attachment_dir=report_attachment_dir, report_attachment_relative_path=report_attachment_relative_path, secrets=secrets)


def shared_photos_root():
    return _split_residual_photo_paths.shared_photos_root(Path=Path, SHARED_PHOTOS_DIR=SHARED_PHOTOS_DIR)


def service_order_photo_folder(order_number):
    return _split_photo_storage.service_order_photo_folder(order_number, secure_filename=secure_filename, shared_photos_root=shared_photos_root)


def service_order_photo_summary(order_number):
    return _split_photo_storage.service_order_photo_summary(order_number, service_order_photo_folder=service_order_photo_folder)


def ensure_service_order_picture_folder(order_number):
    return _split_residual_photo_paths.ensure_service_order_picture_folder(order_number, service_order_photo_folder=service_order_photo_folder)


def service_order_incoming_folder(order_number, source_name="uploads"):
    return _split_residual_photo_paths.service_order_incoming_folder(order_number, source_name, secure_filename=secure_filename, shared_photos_root=shared_photos_root)


def is_google_photos_share_url(value):
    return _split_residual_photo_paths.is_google_photos_share_url(value, GOOGLE_PHOTOS_ALLOWED_HOSTS=GOOGLE_PHOTOS_ALLOWED_HOSTS, urlsplit=urlsplit)


def google_photos_page_html(share_url):
    return _split_google_photos.google_photos_page_html(share_url, HTTPError=HTTPError, Request=Request, URLError=URLError, urlopen=urlopen, urlsplit=urlsplit)


def normalize_embedded_google_url(value):
    return _split_google_photos.normalize_embedded_google_url(value, html=html)


def google_image_score(url):
    return _split_google_photos.google_image_score(url, re=re)


def google_image_dimensions(url):
    return _split_google_photos.google_image_dimensions(url, re=re)


def is_likely_google_account_image(url):
    return _split_google_photos.is_likely_google_account_image(url, google_image_dimensions=google_image_dimensions, urlsplit=urlsplit)


def google_photo_url_key(url):
    return _split_residual_photo_paths.google_photo_url_key(url)


def google_photo_download_candidates(url):
    return _split_google_photos.google_photo_download_candidates(url, google_photo_url_key=google_photo_url_key)


def extract_google_photo_urls(page_html):
    return _split_google_photos.extract_google_photo_urls(page_html, GOOGLE_PHOTOS_MAX_IMPORT=GOOGLE_PHOTOS_MAX_IMPORT, google_image_score=google_image_score, google_photo_url_key=google_photo_url_key, is_likely_google_account_image=is_likely_google_account_image, normalize_embedded_google_url=normalize_embedded_google_url, re=re)


def downloaded_image_extension(content_type, url):
    return _split_google_photos.downloaded_image_extension(content_type, url, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, Path=Path, urlsplit=urlsplit)


def unique_download_path(folder, filename):
    return _split_google_photos.unique_download_path(folder, filename, secure_filename=secure_filename)


def download_google_photo_image(url, target_dir, index):
    return _split_google_photos.download_google_photo_image(url, target_dir, index, GOOGLE_PHOTOS_MAX_IMAGE_BYTES=GOOGLE_PHOTOS_MAX_IMAGE_BYTES, HTTPError=HTTPError, Request=Request, URLError=URLError, downloaded_image_extension=downloaded_image_extension, google_photo_download_candidates=google_photo_download_candidates, hashlib=hashlib, unique_download_path=unique_download_path, urlopen=urlopen)


def import_google_photos_share_to_incoming(order, share_url):
    return _split_google_photos.import_google_photos_share_to_incoming(order, share_url, download_google_photo_image=download_google_photo_image, extract_google_photo_urls=extract_google_photo_urls, google_photos_page_html=google_photos_page_html, is_google_photos_share_url=is_google_photos_share_url, service_order_incoming_folder=service_order_incoming_folder)


def send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found=0, truncated_count=0, error=None):
    return _split_google_photos.send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found, truncated_count, error, GOOGLE_PHOTOS_MAX_IMPORT=GOOGLE_PHOTOS_MAX_IMPORT, app=app, create_message=create_message, html=html, send_email=send_email)


def run_google_photos_import_job(order_id, user_id, share_url):
    return _split_google_photos.run_google_photos_import_job(order_id, user_id, share_url, app=app, db=db, import_google_photos_share_to_incoming=import_google_photos_share_to_incoming, send_google_photos_import_result=send_google_photos_import_result)


def start_google_photos_import_job(order_id, user_id, share_url):
    return _split_google_photos.start_google_photos_import_job(order_id, user_id, share_url, run_google_photos_import_job=run_google_photos_import_job, threading=threading)


def resolve_shared_photo(relative_path="", require_file=False, allow_missing=False):
    return _photo_capture_services.resolve_shared_photo(relative_path, require_file, allow_missing, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, abort=abort, shared_photos_root=shared_photos_root)


def shared_photo_relative(path):
    return _photo_capture_services.shared_photo_relative(path, shared_photos_root=shared_photos_root)


def resolve_shared_picture(relative_path):
    return _photo_capture_services.resolve_shared_picture(relative_path, abort=abort, resolve_shared_photo=resolve_shared_photo, shared_photos_root=shared_photos_root)


def count_shared_images(path, excluded_dirs=None):
    return _photo_capture_services.count_shared_images(path, excluded_dirs, ALLOWED_IMAGE_EXTENSIONS=ALLOWED_IMAGE_EXTENSIONS, valid_image_file=valid_image_file)


def valid_image_file(path):
    return _split_photo_storage.valid_image_file(path, Image=Image)


def safe_attachment_response(path, original_filename):
    return _split_attachment_response.safe_attachment_response(path, original_filename, Image=Image, send_file=send_file)


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
    return _split_residual_settlement_paths.customer_reimbursement_dir(reimbursement_id, CUSTOMER_REIMBURSEMENT_DIR=CUSTOMER_REIMBURSEMENT_DIR, os=os)


def customer_reimbursement_attachment_dir(reimbursement_id):
    return _split_residual_settlement_paths.customer_reimbursement_attachment_dir(reimbursement_id, customer_reimbursement_dir=customer_reimbursement_dir, os=os)


def customer_reimbursement_file_path(reimbursement):
    return _split_residual_settlement_paths.customer_reimbursement_file_path(reimbursement, CUSTOMER_REIMBURSEMENT_DIR=CUSTOMER_REIMBURSEMENT_DIR, os=os)


def customer_reimbursement_attachment_path(attachment):
    return _split_residual_settlement_paths.customer_reimbursement_attachment_path(attachment, CUSTOMER_REIMBURSEMENT_DIR=CUSTOMER_REIMBURSEMENT_DIR, os=os)


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
    return _split_residual_settlement_values.is_us_weekend_or_holiday(day, observed_us_holidays=observed_us_holidays)


def report_duration_hours(report, worker_count=1):
    return _report_calculations.report_duration_hours(report, worker_count, rounded_report_service_hours=rounded_report_service_hours)


def report_actual_date(report):
    return _report_calculations.report_actual_date(report)


def _parse_quotation_revision_snapshot(quotation_revision):
    return _split_settlement_support._parse_quotation_revision_snapshot(quotation_revision)


def build_quotation_settlement_rows(order, quotation_revision):
    return _split_quotation_rows.build_quotation_settlement_rows(order, quotation_revision, _parse_quotation_revision_snapshot=_parse_quotation_revision_snapshot, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, canonical_commercial_rate_key=canonical_commercial_rate_key, db=db, driving_rate_missing=driving_rate_missing, driving_transport_amount=driving_transport_amount, money_decimal=money_decimal, money_float=money_float, normalize_driving_mode=normalize_driving_mode, normalize_settlement_mode=normalize_settlement_mode, report_actual_date=report_actual_date, split_report_labor_hours=split_report_labor_hours, to_float=to_float)


def customer_reimbursement_seed_rows(order_id):
    return _split_quotation_rows.customer_reimbursement_seed_rows(order_id, build_quotation_settlement_rows=build_quotation_settlement_rows, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, db=db, report_actual_date=report_actual_date, resolve_contract_rates=resolve_contract_rates, split_report_labor_hours=split_report_labor_hours)


def merge_service_reports_into_customer_reimbursement(rows, order_id):
    return _split_source_merge.merge_service_reports_into_customer_reimbursement(rows, order_id, calculate_customer_reimbursement_item=calculate_customer_reimbursement_item, customer_reimbursement_seed_rows=customer_reimbursement_seed_rows, normalized_project_name=normalized_project_name)


def excessive_following_mileage_rows(order_id, threshold=500):
    return _split_settlement_support.excessive_following_mileage_rows(order_id, threshold, db=db)


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
    return _split_settlement_support._fallback_row_for_worker(candidates_by_worker, worker_key, _reimbursement_date_key=_reimbursement_date_key)


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
    return _split_residual_settlement_paths.save_customer_reimbursement_uploads(reimbursement_id, save_customer_reimbursement_attachment=save_customer_reimbursement_attachment, uploaded_attachments_from_request=uploaded_attachments_from_request)


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
    return _split_settlement_support.service_order_active_invoice(order_id, db=db)


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
    return _split_residual_settlement_values.reimbursement_number(value)


def reimbursement_paragraph(value, style):
    return _split_residual_settlement_values.reimbursement_paragraph(value, style, Paragraph=Paragraph)


def build_customer_reimbursement_pdf(reimbursement, order, rows):
    return _split_documents.build_customer_reimbursement_pdf(reimbursement, order, rows, A4=A4, Decimal=Decimal, LongTable=LongTable, Paragraph=Paragraph, ParagraphStyle=ParagraphStyle, SimpleDocTemplate=SimpleDocTemplate, Spacer=Spacer, TA_CENTER=TA_CENTER, TA_LEFT=TA_LEFT, TableStyle=TableStyle, UnicodeCIDFont=UnicodeCIDFont, colors=colors, customer_reimbursement_file_path=customer_reimbursement_file_path, customer_reimbursement_item_expense_amount=customer_reimbursement_item_expense_amount, customer_reimbursement_totals=customer_reimbursement_totals, get_company_profile=get_company_profile, landscape=landscape, mm=mm, money=money, money_decimal=money_decimal, os=os, pdfmetrics=pdfmetrics, reimbursement_number=reimbursement_number, reimbursement_paragraph=reimbursement_paragraph)


def remove_customer_reimbursement_pdf(reimbursement):
    return _split_settlement_support.remove_customer_reimbursement_pdf(reimbursement, customer_reimbursement_file_path=customer_reimbursement_file_path, os=os)


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
    return _split_residual_messages.create_message(user_id, title, body, link, db=db, now=now)


def log_action(action, entity_type, entity_id, entity_label, summary=""):
    return _split_audit.log_action(action, entity_type, entity_id, entity_label, summary, db=db, g=g, now=now)


def record_email_delivery(entity_type, entity_id, recipient, subject, *,
                          status=None, error_message=None, employee_id=None):
    return _split_delivery_record.record_email_delivery(entity_type, entity_id, recipient, subject, status=status, error_message=error_message, employee_id=employee_id, db=db, g=g, now=now)


def email_delivery_summary(entity_type, entity_id):
    return _split_delivery_summary.email_delivery_summary(entity_type, entity_id, db=db)


def request_ip_address():
    return _split_residual_audit.request_ip_address(request=request)


def log_login_action(user):
    return _split_audit.log_login_action(user, db=db, now=now, request_ip_address=request_ip_address, role_label=role_label)


def normalized_address(value):
    return _split_residual_geo_values.normalized_address(value)


def parse_coordinate_pair(value):
    return _split_geocoding.parse_coordinate_pair(value, re=re)


class TemporaryGeocodingError(Exception):
    pass


def fetch_geocoder_json(request_url):
    return _geocoder_request.fetch_geocoder_json(request_url, HTTPError=HTTPError, NOMINATIM_USER_AGENT=NOMINATIM_USER_AGENT, Request=Request, TemporaryGeocodingError=TemporaryGeocodingError, URLError=URLError, _geocode_lock=_geocode_lock, json=json, time=time, urlopen=urlopen, _state_get=lambda: _last_geocode_request_at, _state_set=lambda value: globals().__setitem__('_last_geocode_request_at', value))


def configured_country_codes():
    return _split_residual_geo_values.configured_country_codes(NOMINATIM_COUNTRY_CODES=NOMINATIM_COUNTRY_CODES)


def address_expectations(address):
    return _split_residual_geo_values.address_expectations(address, US_STATE_CODES=US_STATE_CODES, re=re)


def census_result_matches_address(match, address):
    return _split_geocoding.census_result_matches_address(match, address, address_expectations=address_expectations)


def geocode_with_google(address):
    return _split_geocoding.geocode_with_google(address, GOOGLE_GEOCODING_URL=GOOGLE_GEOCODING_URL, HTTPError=HTTPError, TemporaryGeocodingError=TemporaryGeocodingError, address_expectations=address_expectations, app=app, fetch_geocoder_json=fetch_geocoder_json, get_google_geocoding_api_key=get_google_geocoding_api_key, json=json, urlencode=urlencode)


def geocode_with_census(address):
    return _split_geocoding.geocode_with_census(address, CENSUS_GEOCODER_URL=CENSUS_GEOCODER_URL, HTTPError=HTTPError, TemporaryGeocodingError=TemporaryGeocodingError, census_result_matches_address=census_result_matches_address, fetch_geocoder_json=fetch_geocoder_json, json=json, urlencode=urlencode)


def nominatim_address_candidates(address):
    return _split_geocoding.nominatim_address_candidates(address, normalized_address=normalized_address, re=re)


def geocode_with_nominatim(address):
    return _split_geocoding.geocode_with_nominatim(address, HTTPError=HTTPError, NOMINATIM_COUNTRY_CODES=NOMINATIM_COUNTRY_CODES, NOMINATIM_URL=NOMINATIM_URL, TemporaryGeocodingError=TemporaryGeocodingError, address_expectations=address_expectations, configured_country_codes=configured_country_codes, fetch_geocoder_json=fetch_geocoder_json, json=json, nominatim_address_candidates=nominatim_address_candidates, urlencode=urlencode)


def geocode_address(address):
    return _split_geocoding.geocode_address(address, GEOCODING_ENABLED=GEOCODING_ENABLED, TemporaryGeocodingError=TemporaryGeocodingError, configured_country_codes=configured_country_codes, geocode_with_census=geocode_with_census, geocode_with_google=geocode_with_google, geocode_with_nominatim=geocode_with_nominatim)


def geocode_service_order(order_id, force=False):
    return _split_geocoding.geocode_service_order(order_id, force, GEOCODER_VERSION=GEOCODER_VERSION, HTTPError=HTTPError, TemporaryGeocodingError=TemporaryGeocodingError, URLError=URLError, app=app, db=db, geocode_address=geocode_address, json=json, normalized_address=normalized_address, now=now)


def geocode_buyer(buyer_id, force=False):
    return _split_geocoding.geocode_buyer(buyer_id, force, GEOCODER_VERSION=GEOCODER_VERSION, HTTPError=HTTPError, TemporaryGeocodingError=TemporaryGeocodingError, URLError=URLError, app=app, db=db, geocode_address=geocode_address, json=json, normalized_address=normalized_address, now=now)


def notify_role(roles, title, body, link=None, exclude_user_ids=None):
    return _split_messages.notify_role(roles, title, body, link, exclude_user_ids, create_message=create_message, db=db)


def review_message_body(user_name, invoice, client, total, is_resubmission=False):
    return _split_residual_messages.review_message_body(user_name, invoice, client, total, is_resubmission, money=money)


def return_message_body(invoice, client, total, reason):
    return _split_residual_messages.return_message_body(invoice, client, total, reason, money=money)


def unread_message_count():
    return _split_messages.unread_message_count(db=db, g=g)


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
    return _split_messages._message_ids_for_user(raw_ids, db=db, g=g)


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
    return _split_ai_settings.deepseek_assistant_settings(DEEPSEEK_API_KEY_ENV=DEEPSEEK_API_KEY_ENV, db=db, llm_config=llm_config)


def vision_settings():
    return _split_ai_settings.vision_settings(VISION_MAX_IMAGE_BYTES_ENV=VISION_MAX_IMAGE_BYTES_ENV, db=db, get_setting=get_setting, llm_config=llm_config)


def ai_search_like(value):
    return _split_residual_ai_values.ai_search_like(value)


def ai_normalize_status(domain, value):
    return _split_assistant_helpers.ai_normalize_status(domain, value)


def ai_search_business_records(arguments):
    return _split_assistant_helpers.ai_search_business_records(arguments, AI_SEARCH_DOMAINS=AI_SEARCH_DOMAINS, ai_normalize_status=ai_normalize_status, ai_search_like=ai_search_like, can_view_customer_reimbursement=can_view_customer_reimbursement, can_view_invoices=can_view_invoices, db=db, expense_access_filter=expense_access_filter, has_action_permission=has_action_permission, service_order_access_filters=service_order_access_filters, url_for=url_for)


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
    return _split_assistant_helpers.deepseek_api_error_message(status_code, response_body, json=json)


def call_deepseek_chat(messages, settings, include_tools=True, max_tokens=1600):
    return _split_assistant_helpers.call_deepseek_chat(messages, settings, include_tools, max_tokens, AI_ASSISTANT_TOOLS=AI_ASSISTANT_TOOLS, HTTPError=HTTPError, Request=Request, URLError=URLError, deepseek_api_error_message=deepseek_api_error_message, json=json, urlopen=urlopen)


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
    return _split_residual_ai_values._ai_daily_report_business_date(app_timezone=app_timezone, datetime=datetime)


def _auto_prepare_draft_media(draft_id: int) -> list:
    return _split_ai_workflow._auto_prepare_draft_media(draft_id, DATA_DIR=DATA_DIR, EmployeeResolutionService=EmployeeResolutionService, PhotoDiscoveryService=PhotoDiscoveryService, PhotoMetadataService=PhotoMetadataService, SHARED_PHOTOS_DIR=SHARED_PHOTOS_DIR, TravelService=TravelService, _ai_daily_report_service=_ai_daily_report_service, _make_photo_classification_service=_make_photo_classification_service, db=db, g=g, get_google_routes_api_key=get_google_routes_api_key, get_google_static_maps_api_key=get_google_static_maps_api_key, os=os, require_service_order=require_service_order, service_order_photo_folder=service_order_photo_folder, vision_settings=vision_settings)


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
    return _split_ai_workflow._build_review_center_preview(draft_row, SHARED_PHOTOS_DIR=SHARED_PHOTOS_DIR, _attachment_manifest_service=_attachment_manifest_service, _formal_save_preview_summary=_formal_save_preview_summary, _run_phase7_validation=_run_phase7_validation, db=db)


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
    return _split_ai_workflow._formal_save_preview_summary(draft_row, can_formal_save_draft=can_formal_save_draft, db=db, g=g)






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
    return _split_ai_workflow._run_phase7_validation(draft_row, db=db, json=json)


def _manifest_error_response(exc, default_status=422):
    return _split_ai_workflow._manifest_error_response(exc, default_status, ManifestError=ManifestError, ManifestIntegrityError=ManifestIntegrityError, ManifestNotFoundError=ManifestNotFoundError, ManifestStaleError=ManifestStaleError, ManifestValidationBlockedError=ManifestValidationBlockedError, jsonify=jsonify)




# ─── Phase 9: Formal Save / Transactional Commit ────────────────────────────









_views_manifest_delete = _split_manifest_delete.register_manifest_delete_routes(
    app,
    _cb_resolve_ManifestError=lambda: ManifestError,
    _cb_resolve__ai_daily_report_service=lambda: _ai_daily_report_service,
    _cb_resolve__attachment_manifest_service=lambda: _attachment_manifest_service,
    _cb_resolve__manifest_error_response=lambda: _manifest_error_response,
    _cb_resolve_can_prepare_attachment_manifest=lambda: can_prepare_attachment_manifest,
    _cb_resolve_db=lambda: db,
    _cb_resolve_g=lambda: g,
    _cb_resolve_is_internal_user=lambda: is_internal_user,
    _cb_resolve_jsonify=lambda: jsonify,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_require_ai_daily_report_csrf=lambda: require_ai_daily_report_csrf,
)
ai_daily_report_manifest_cancel = _views_manifest_delete['ai_daily_report_manifest_cancel']


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
    return _split_ai_workflow._fold_staff_query(text)








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




_views_invoice_editor = _split_invoice_editor.register_invoice_editor_routes(
    app,
    _cb_resolve_IntegrityError=lambda: IntegrityError,
    _cb_resolve_abort=lambda: abort,
    _cb_resolve_calculate_payment_due_date=lambda: calculate_payment_due_date,
    _cb_resolve_can_create_invoice=lambda: can_create_invoice,
    _cb_resolve_claim_invoice_save_token=lambda: claim_invoice_save_token,
    _cb_resolve_create_invoice_from_form=lambda: create_invoice_from_form,
    _cb_resolve_customer_reimbursement_invoice_items=lambda: customer_reimbursement_invoice_items,
    _cb_resolve_date=lambda: date,
    _cb_resolve_db=lambda: db,
    _cb_resolve_flash=lambda: flash,
    _cb_resolve_g=lambda: g,
    _cb_resolve_get_invoice_attachments=lambda: get_invoice_attachments,
    _cb_resolve_is_external_user=lambda: is_external_user,
    _cb_resolve_is_manager=lambda: is_manager,
    _cb_resolve_load_invoice=lambda: load_invoice,
    _cb_resolve_log_action=lambda: log_action,
    _cb_resolve_login_required=lambda: login_required,
    _cb_resolve_next_invoice_number=lambda: next_invoice_number,
    _cb_resolve_payment_term_rows=lambda: payment_term_rows,
    _cb_resolve_redirect=lambda: redirect,
    _cb_resolve_render_template=lambda: render_template,
    _cb_resolve_request=lambda: request,
    _cb_resolve_require_customer_reimbursement=lambda: require_customer_reimbursement,
    _cb_resolve_require_service_order=lambda: require_service_order,
    _cb_resolve_require_service_order_start_date=lambda: require_service_order_start_date,
    _cb_resolve_secrets=lambda: secrets,
    _cb_resolve_service_order_active_invoice=lambda: service_order_active_invoice,
    _cb_resolve_update_invoice_from_form=lambda: update_invoice_from_form,
    _cb_resolve_uploaded_attachments_from_request=lambda: uploaded_attachments_from_request,
    _cb_resolve_url_for=lambda: url_for,
    _cb_resolve_validate_attachment_uploads=lambda: validate_attachment_uploads,
)
new_invoice = _views_invoice_editor['new_invoice']
edit_invoice = _views_invoice_editor['edit_invoice']


def invoice_items_from_form():
    return _invoice_forms.invoice_items_from_form(db=db, request=request, to_float=to_float)


def posted_client_id():
    return _invoice_forms.posted_client_id(request=request)


def posted_service_order_id(require_start_date=False, required=False):
    return _invoice_forms.posted_service_order_id(require_start_date, required, request=request, require_service_order=require_service_order)


def create_invoice_from_form(save_token=None):
    return _split_invoice_create.create_invoice_from_form(save_token, IntegrityError=IntegrityError, abort=abort, can_access_client=can_access_client, copy_customer_reimbursement_attachments_to_invoice=copy_customer_reimbursement_attachments_to_invoice, copy_mileage_proofs_to_invoice=copy_mileage_proofs_to_invoice, db=db, finish_invoice_save_token=finish_invoice_save_token, g=g, invoice_items_from_form=invoice_items_from_form, invoice_number_exists=invoice_number_exists, invoice_totals=invoice_totals, log_action=log_action, money=money, next_invoice_number=next_invoice_number, now=now, posted_client_id=posted_client_id, posted_service_order_id=posted_service_order_id, recognize_invoice_if_accounting_enabled=recognize_invoice_if_accounting_enabled, request=request, save_uploaded_attachment=save_uploaded_attachment, service_order_active_invoice=service_order_active_invoice, to_float=to_float, uploaded_attachments_from_request=uploaded_attachments_from_request)




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
