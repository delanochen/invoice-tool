"""AI Daily Report Module (Phase 4)

AIIntentService      -> natural language to validated AIAction
ActionValidator      -> JSON parse + Pydantic validate + allowlist + idempotency
DailyReportService   -> Draft CRUD + action execution (never writes to service_reports)
WorkOrderContextService -> service order / site / workers context
EmployeeResolutionService -> name -> users.id resolution (self/unique/ambiguous/not found)
TravelService        -> origin/destination/overnight/transportation per worker
GoogleRoutesService  -> Google Routes API client (official, with retry) [Phase 3A]
MileageService       -> meters->miles conversion, rounding, reported mileage calc [Phase 3A]
GoogleStaticMapsService -> Google Static Maps API client for evidence map [Phase 3B]
MileageEvidenceService -> Generate auditable Mileage Evidence PNG [Phase 3B]
PhotoDiscoveryService -> Discover original site photos for order+date [Phase 4]
PhotoMetadataService -> Read EXIF capture time + anomaly detection [Phase 4]
"""
from .schemas import (
    AIAction,
    DailyReportDraft,
    WorkerTravel,
    WorkItem,
    PhotoRef,
    PhotoAnalysis,
    MileageEvidence,
    MileageEvidenceRecord,
    EVIDENCE_VERSION,
    ACTION_VERSION,
)
from .action_validator import (
    parse_and_validate,
    ValidationResult,
    generate_action_id,
    is_phase1_implemented,
    ALLOWED_INTENTS,
    PHASE1_IMPLEMENTED_INTENTS,
)
from .intent_service import AIIntentService, IntentServiceError
from .daily_report_service import DailyReportService, DraftVersionConflict, DraftStateError, ConversationTooLongError
from .work_order_context import WorkOrderContextService
from .employee_resolution import EmployeeResolutionService, EmployeeResolutionResult
from .travel_service import TravelService
from .google_routes import GoogleRoutesService, RouteResult
from .mileage_service import MileageService
from .static_maps import GoogleStaticMapsService, StaticMapResult
from .mileage_evidence import MileageEvidenceService
from .photo_discovery import PhotoDiscoveryService, PhotoDiscoveryError
from .photo_metadata import PhotoMetadataService
from .vision_provider import (
    VisionClassificationService, DeepSeekVisionProvider,
    VISION_ANALYSIS_VERSION, VisionProviderError, PHOTO_ANALYSIS_VERSION,
    prepare_vision_image,
)
from .photo_classification import PhotoClassificationService
from .perceptual_hash import PerceptualHashService, compute_dhash, is_near_duplicate
from .preview_aggregation import PreviewAggregationService
from .validation_engine import (
    ValidationEngine,
    ValidationContext,
    ValidationContextBuilder,
    ValidationResult,
    ValidationIssue,
    ENGINE_VERSION as VALIDATION_ENGINE_VERSION,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SEVERITY_INFO,
    compute_draft_data_hash,
    compute_context_hash,
    compute_issue_fingerprint,
    make_issue_key,
    get_acknowledgements,
    add_acknowledgement,
    is_acknowledgement_valid,
    normalize_address,
    addresses_equal,
)
from .prompts import SYSTEM_PROMPT, build_user_prompt

__all__ = [
    "AIAction",
    "DailyReportDraft",
    "WorkerTravel",
    "WorkItem",
    "PhotoRef",
    "PhotoAnalysis",
    "MileageEvidence",
    "MileageEvidenceRecord",
    "EVIDENCE_VERSION",
    "ACTION_VERSION",
    "parse_and_validate",
    "ValidationResult",
    "generate_action_id",
    "is_phase1_implemented",
    "ALLOWED_INTENTS",
    "PHASE1_IMPLEMENTED_INTENTS",
    "AIIntentService",
    "IntentServiceError",
    "DailyReportService",
    "DraftVersionConflict",
    "DraftStateError",
    "ConversationTooLongError",
    "WorkOrderContextService",
    "EmployeeResolutionService",
    "EmployeeResolutionResult",
    "TravelService",
    "GoogleRoutesService",
    "RouteResult",
    "MileageService",
    "GoogleStaticMapsService",
    "StaticMapResult",
    "MileageEvidenceService",
    "PhotoDiscoveryService",
    "PhotoDiscoveryError",
    "PhotoMetadataService",
    "VisionClassificationService",
    "DeepSeekVisionProvider",
    "VISION_ANALYSIS_VERSION",
    "PHOTO_ANALYSIS_VERSION",
    "VisionProviderError",
    "prepare_vision_image",
    "PhotoClassificationService",
    "PerceptualHashService",
    "compute_dhash",
    "is_near_duplicate",
    "PreviewAggregationService",
    "ValidationEngine",
    "ValidationContext",
    "ValidationContextBuilder",
    "ValidationResult",
    "ValidationIssue",
    "VALIDATION_ENGINE_VERSION",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SEVERITY_INFO",
    "compute_draft_data_hash",
    "compute_context_hash",
    "compute_issue_fingerprint",
    "make_issue_key",
    "get_acknowledgements",
    "add_acknowledgement",
    "is_acknowledgement_valid",
    "normalize_address",
    "addresses_equal",
    "SYSTEM_PROMPT",
    "build_user_prompt",
]
