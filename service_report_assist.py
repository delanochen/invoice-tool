"""工作日报（服务报告）「辅助填写」——AI 智能日报能力的**薄适配层**。

设计红线（勿在本模块里重新实现任何口径）：
- 照片发现  -> ``ai_daily_report.photo_discovery.PhotoDiscoveryService``
- 拍摄时间  -> ``ai_daily_report.photo_metadata.PhotoMetadataService``
- 英里换算 / 交通时长 -> ``ai_daily_report.mileage_service.MileageService``
- 行程类型（往返 / 单程） -> 根级 ``trip_policy``

本模块只做三件事：把服务报告的表单结构翻译成上述服务的入参、把结果翻译成
表单能吃的 JSON、以及在调用 Route API 前做必要的前置判断（随行/飞机不算驾车、
地址缺失、缺少目的地）。

新增页没有 report_id，所以这里永远**只读**：产出的是「建议值」，由前端填入表单，
用户点保存才落库。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from trip_policy import DEFAULT_TRIP_TYPE, normalize_trip_type, trip_multiplier

logger = logging.getLogger(__name__)

# field_photos.photo_type -> 服务报告附件分类（service_report_attachments.category）
PHOTO_TYPE_TO_REPORT_CATEGORY: Dict[str, str] = {
    "arrival": "arrival",
    "departure": "departure",
    "safety": "self_check",
}
# 其余类型（equipment / general / legacy / unknown）都算「现场服务照片」候选。
SITE_PHOTO_LIMIT = 10
CATEGORY_ORDER: Tuple[str, ...] = ("arrival", "departure", "self_check", "site")

# 只有这两类出行走驾车路线；following（随行）/ flight（飞机）不调用 Google。
DRIVING_MODES = frozenset({"self_drive", "rental_drive"})
EVIDENCE_MODES = DRIVING_MODES  # 里程佐证同样只覆盖驾车出行

METERS_PER_MILE = 1609.344


def route_fingerprint_parts(
    origin: str,
    destination: str,
    trip_type: str,
    distance_meters: Optional[float] = None,
    polyline: Optional[str] = None,
) -> str:
    """路线指纹：地址或行程类型变了 -> 指纹变了 -> 佐证需要重新生成。

    字段组合与 ``MileageEvidenceService.compute_route_fingerprint`` 对齐
    （不含 query_time，避免同一路线每次微调都被判成过期）。
    """
    raw = "|".join(
        [
            str(origin or ""),
            str(destination or ""),
            str(round(distance_meters, 2) if distance_meters else ""),
            str(polyline or ""),
            str(normalize_trip_type(trip_type)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ServiceReportAssistService:
    """按工单 + 日期产出一份「辅助填写」建议（只读，不写库）。"""

    def __init__(
        self,
        *,
        photo_discovery: Any,
        photo_metadata: Any,
        routes_service: Any = None,
        employee_address_lookup: Optional[Callable[[int], Optional[str]]] = None,
        photo_url_builder: Optional[Callable[[str], Dict[str, str]]] = None,
    ):
        self.photo_discovery = photo_discovery
        self.photo_metadata = photo_metadata
        self.routes_service = routes_service
        self.employee_address_lookup = employee_address_lookup
        # 由调用方注入：relative_path -> {"thumbnail":..., "preview":...}，
        # 让结果能直接喂给现有的 NAS 照片渲染通道（避免前端二次请求）。
        self.photo_url_builder: Optional[Callable[[str], Dict[str, str]]] = photo_url_builder
        # 单次 plan 内的路线缓存：同起终点不重复调用 Google（省调用次数与费用）
        self._route_cache: Dict[Tuple[str, str], Any] = {}

    # ─── 照片分组 ────────────────────────────────────────────────────

    @staticmethod
    def parse_capture_time(photo: Any) -> Optional[datetime]:
        raw = getattr(photo, "capture_time", None)
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def _photo_sort_key(self, photo: Any) -> Tuple[int, datetime, str]:
        """按拍摄时间升序；取不到时间的排最后（同组内仍按路径稳定排序）。"""
        captured = self.parse_capture_time(photo)
        if captured is None:
            return (1, datetime.max, getattr(photo, "relative_path", "") or "")
        return (0, captured, getattr(photo, "relative_path", "") or "")

    def classify_photos(self, photos: List[Any]) -> Dict[str, List[Any]]:
        buckets: Dict[str, List[Any]] = {name: [] for name in CATEGORY_ORDER}
        for photo in photos:
            photo_type = (
                getattr(photo, "manual_classification", None)
                or getattr(photo, "classification", None)
                or "unknown"
            )
            buckets[PHOTO_TYPE_TO_REPORT_CATEGORY.get(photo_type, "site")].append(photo)
        for items in buckets.values():
            items.sort(key=self._photo_sort_key)
        return buckets

    def _photo_payload(self, photo: Any) -> Dict[str, Any]:
        relative_path = getattr(photo, "relative_path", "") or ""
        payload: Dict[str, Any] = {
            "photo_id": getattr(photo, "photo_id", "") or "",
            "relative_path": relative_path,
            "file_name": relative_path.rpartition("/")[2],
            "capture_time": getattr(photo, "capture_time", None),
            "capture_time_source": getattr(photo, "capture_time_source", None),
        }
        if self.photo_url_builder is not None:
            try:
                payload.update(self.photo_url_builder(relative_path) or {})
            except Exception:
                logger.exception("assist: photo url build failed for %s", relative_path)
        return payload

    # ─── 照片 plan ───────────────────────────────────────────────────

    def build_photo_plan(
        self,
        order_number: str,
        report_date: str,
        photo_type_lookup: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Dict[str, Any]:
        plan: Dict[str, Any] = {
            "status": "no_photos",
            "reason": None,
            "photos": {name: [] for name in CATEGORY_ORDER},
            "site_total": 0,
            "site_selected": 0,
            "site_truncated": False,
            "arrival_time": None,
            "departure_time": None,
            "unknown_time_counts": {name: 0 for name in CATEGORY_ORDER},
            "warnings": [],
        }

        try:
            photos, status, error = self.photo_discovery.discover_photos(
                order_number, report_date, photo_type_lookup
            )
        except Exception:
            logger.exception("assist: photo discovery crashed for %s %s", order_number, report_date)
            plan["status"] = "failed"
            plan["reason"] = "discovery_error"
            return plan

        if status != "discovered" or not photos:
            plan["status"] = "no_photos" if status != "failed" else "failed"
            plan["reason"] = error
            return plan

        try:
            photos = self.photo_metadata.enrich_all_photos(photos, report_date)
        except Exception:
            logger.exception("assist: photo time enrichment failed")
            plan["warnings"].append("photo_time_enrichment_failed")

        buckets = self.classify_photos(photos)
        for name in ("arrival", "departure", "self_check"):
            plan["photos"][name] = [self._photo_payload(p) for p in buckets[name]]
            plan["unknown_time_counts"][name] = sum(
                1 for p in buckets[name] if self.parse_capture_time(p) is None
            )

        site_photos = buckets["site"]
        plan["site_total"] = len(site_photos)
        selected = site_photos[:SITE_PHOTO_LIMIT]
        plan["site_selected"] = len(selected)
        plan["site_truncated"] = len(site_photos) > len(selected)
        plan["photos"]["site"] = [self._photo_payload(p) for p in selected]
        plan["unknown_time_counts"]["site"] = sum(
            1 for p in selected if self.parse_capture_time(p) is None
        )

        arrival = self._earliest_with_time(buckets["arrival"])
        departure = self._latest_with_time(buckets["departure"])
        plan["arrival_time"] = self._hhmm(arrival)
        plan["departure_time"] = self._hhmm(departure)
        if buckets["arrival"] and arrival is None:
            plan["warnings"].append("arrival_time_unknown")
        if buckets["departure"] and departure is None:
            plan["warnings"].append("departure_time_unknown")

        plan["status"] = "discovered"
        return plan

    def _earliest_with_time(self, photos: List[Any]) -> Optional[datetime]:
        times = [self.parse_capture_time(p) for p in photos]
        times = [t for t in times if t is not None]
        return min(times) if times else None

    def _latest_with_time(self, photos: List[Any]) -> Optional[datetime]:
        times = [self.parse_capture_time(p) for p in photos]
        times = [t for t in times if t is not None]
        return max(times) if times else None

    @staticmethod
    def _hhmm(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return f"{value.hour:02d}:{value.minute:02d}"

    # ─── 员工出发地 / 里程 / 交通时长 ─────────────────────────────────

    def resolve_origin(self, worker: Dict[str, Any], fallback_origin: str = "") -> Tuple[str, str]:
        """出发地取值链：表单已填 > 员工主数据地址 > 日报级出发地址。

        返回 (地址, 来源)，来源用于预览提示：user_input / employee_default /
        report_fallback / none。
        """
        current = (worker.get("current_origin") or "").strip()
        if current:
            return current, "user_input"
        if self.employee_address_lookup is not None:
            try:
                address = self.employee_address_lookup(worker.get("user_id"))
            except Exception:
                logger.exception("assist: employee address lookup failed")
                address = None
            if address and address.strip():
                return address.strip(), "employee_default"
        if (fallback_origin or "").strip():
            return fallback_origin.strip(), "report_fallback"
        return "", "none"

    def get_route(self, origin: str, destination: str) -> Any:
        cache_key = (origin.strip().lower(), destination.strip().lower())
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        result = self.routes_service.get_driving_route(origin, destination)
        self._route_cache[cache_key] = result
        return result

    def plan_worker(
        self,
        worker: Dict[str, Any],
        destination: str,
        fallback_origin: str = "",
        compute_route: bool = True,
    ) -> Dict[str, Any]:
        mode = worker.get("travel_mode") or "self_drive"
        trip_type = normalize_trip_type(worker.get("trip_type"))
        plan: Dict[str, Any] = {
            "user_id": worker.get("user_id"),
            "name": worker.get("name") or "",
            "travel_mode": mode,
            "trip_type": trip_type,
            "origin": "",
            "origin_source": "none",
            "distance_meters": None,
            "duration_seconds": None,
            "one_way_miles": None,
            "reported_miles": None,
            "travel_hours": None,
            "route_status": "not_applicable",
            "reason": "",
            "route_fingerprint": None,
        }

        origin, origin_source = self.resolve_origin(worker, fallback_origin)
        plan["origin"] = origin
        plan["origin_source"] = origin_source

        if mode not in DRIVING_MODES:
            plan["route_status"] = "not_applicable"
            plan["reason"] = "following_or_flight"
            return plan
        if not origin:
            plan["route_status"] = "blocked"
            plan["reason"] = "missing_origin"
            return plan
        if not (destination or "").strip():
            plan["route_status"] = "blocked"
            plan["reason"] = "missing_destination"
            return plan
        if self.routes_service is None:
            plan["route_status"] = "blocked"
            plan["reason"] = "routes_unavailable"
            return plan
        if not compute_route:
            plan["route_status"] = "skipped"
            return plan

        result = self.get_route(origin, destination)
        if not getattr(result, "success", False):
            plan["route_status"] = "failed"
            plan["reason"] = getattr(result, "status", None) or "route_failed"
            return plan

        distance_meters = result.distance_meters or 0
        duration_seconds = result.duration_seconds or 0
        one_way_miles = distance_meters / METERS_PER_MILE
        plan.update(
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            one_way_miles=round(one_way_miles, 2),
            reported_miles=round(one_way_miles * trip_multiplier(trip_type), 2),
            travel_hours=self.duration_to_travel_hours(duration_seconds, trip_type),
            route_status="success",
            route_fingerprint=route_fingerprint_parts(
                getattr(result, "origin_normalized", None) or origin,
                getattr(result, "destination_normalized", None) or destination,
                trip_type,
                distance_meters,
                getattr(result, "encoded_polyline", None),
            ),
        )
        return plan

    @staticmethod
    def duration_to_travel_hours(duration_seconds: Optional[int], trip_type: str) -> float:
        """与 AI 智能日报完全一致的交通时长口径（含 1.15 冗余 + 0.25h 向上取整）。"""
        from ai_daily_report.mileage_service import MileageService

        return MileageService.duration_to_travel_hours(duration_seconds, trip_type)

    # ─── 汇总入口 ────────────────────────────────────────────────────

    def build_plan(
        self,
        *,
        order_number: str,
        report_date: str,
        workers: List[Dict[str, Any]],
        destination: str = "",
        fallback_origin: str = "",
        photo_type_lookup: Optional[Callable[[str], Optional[str]]] = None,
        include_routes: bool = True,
    ) -> Dict[str, Any]:
        photo_plan = self.build_photo_plan(order_number, report_date, photo_type_lookup)
        worker_plans = [
            self.plan_worker(worker, destination, fallback_origin, include_routes)
            for worker in workers
        ]
        warnings = list(photo_plan["warnings"])
        if any(item["reason"] == "missing_origin" for item in worker_plans):
            warnings.append("worker_origin_missing")
        if any(item["reason"] == "missing_destination" for item in worker_plans):
            warnings.append("destination_missing")
        if any(item["route_status"] == "failed" for item in worker_plans):
            warnings.append("route_failed")
        return {
            "ok": True,
            "report_date": report_date,
            "destination": destination,
            "default_trip_type": DEFAULT_TRIP_TYPE,
            "photo_plan": photo_plan,
            "workers": worker_plans,
            "warnings": warnings,
        }


# 服务报告的出行方式 -> AI 日报 WorkerTravel.transportation 的合法取值
REPORT_MODE_TO_TRANSPORTATION: Dict[str, str] = {
    "self_drive": "self_drive",
    "rental_drive": "rental_car",
    "following": "passenger",
    "flight": "flight",
}


class ServiceReportEvidenceService:
    """工作日报里程佐证：生成 + 按线路指纹判断是否过期（地址变了自动重生成）。

    复用 ``MileageEvidenceService`` 合成静态地图佐证，不另写一套合成逻辑。
    服务报告侧把 generate_evidence 的产物挂到附件分类 ``mileage_proof`` 上，
    并用 ``service_report_mileage_evidence`` 记指纹，用于「地址是否变动」判断。
    """

    ATTACHMENT_CATEGORY = "mileage_proof"

    def __init__(
        self,
        *,
        routes_service: Any,
        evidence_service: Any,
        attachment_saver: Callable[..., int],
        evidence_root: str,
        uploaded_by: int,
        now_fn: Callable[[], str],
    ):
        self.routes_service = routes_service
        self.evidence_service = evidence_service
        self.attachment_saver = attachment_saver
        self.evidence_root = evidence_root
        self.uploaded_by = uploaded_by
        self.now_fn = now_fn

    def build_worker_travel(
        self,
        worker: Dict[str, Any],
        route: Any,
        destination: str,
    ) -> Any:
        """把服务报告的员工行翻译成 AI 日报的 WorkerTravel（唯一口径转换点）。"""
        from ai_daily_report.schemas import WorkerTravel
        from ai_daily_report.mileage_service import MileageService

        mode = worker.get("travel_mode") or "self_drive"
        trip_type = normalize_trip_type(worker.get("trip_type"))
        distance_meters = getattr(route, "distance_meters", None) or 0
        duration_seconds = getattr(route, "duration_seconds", None) or 0
        one_way_miles = distance_meters / METERS_PER_MILE
        return WorkerTravel(
            user_id=int(worker.get("user_id")),
            name=worker.get("name") or "",
            transportation=REPORT_MODE_TO_TRANSPORTATION.get(mode, "other"),
            origin=worker.get("origin") or "",
            origin_source="employee_default",
            origin_confirmed=True,
            origin_normalized=getattr(route, "origin_normalized", None),
            destination=destination,
            destination_source="service_order",
            destination_normalized=getattr(route, "destination_normalized", None),
            trip_type=trip_type,
            route_distance_meters=distance_meters,
            one_way_miles=round(one_way_miles, 2),
            reported_miles=round(one_way_miles * trip_multiplier(trip_type), 2),
            route_duration_seconds=duration_seconds,
            route_polyline=getattr(route, "encoded_polyline", None),
            route_provider=getattr(route, "provider", None) or "google_routes",
            route_query_time=getattr(route, "query_time", None),
            route_status="success",
            travel_hours=MileageService.duration_to_travel_hours(duration_seconds, trip_type),
        )

    def evidence_absolute_path(self, report_id: int, relative_path: str) -> str:
        from pathlib import Path

        full = Path(self.evidence_root) / str(report_id) / "mileage" / relative_path
        return str(full)

    def generate(
        self,
        *,
        report_id: int,
        order_id: int,
        report_date: str,
        workers: List[Dict[str, Any]],
        destination: str,
        existing_by_user: Optional[Dict[int, Dict[str, Any]]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        existing_by_user = existing_by_user or {}
        summary: Dict[str, Any] = {"generated": 0, "reused": 0, "failed": 0, "results": []}
        for worker in workers:
            outcome = self._generate_one(
                worker=worker,
                report_id=report_id,
                order_id=order_id,
                report_date=report_date,
                destination=destination,
                existing=existing_by_user.get(int(worker.get("user_id") or 0)),
                force=force,
            )
            summary[outcome["outcome"]] += 1
            summary["results"].append(outcome)
        return summary

    def _generate_one(
        self,
        *,
        worker: Dict[str, Any],
        report_id: int,
        order_id: int,
        report_date: str,
        destination: str,
        existing: Optional[Dict[str, Any]],
        force: bool,
    ) -> Dict[str, Any]:
        user_id = int(worker.get("user_id") or 0)
        mode = worker.get("travel_mode") or "self_drive"
        base = {"user_id": user_id, "name": worker.get("name") or "", "travel_mode": mode}

        if REPORT_MODE_TO_TRANSPORTATION.get(mode) not in ("self_drive", "rental_car"):
            # 随行不算驾车里程（与放宽前的口径一致），飞机走公共交通时长。
            return {**base, "outcome": "failed", "reason": "following_or_flight"}
        origin = (worker.get("origin") or "").strip()
        if not origin:
            return {**base, "outcome": "failed", "reason": "missing_origin"}
        if not (destination or "").strip():
            return {**base, "outcome": "failed", "reason": "missing_destination"}
        if self.routes_service is None:
            return {**base, "outcome": "failed", "reason": "routes_unavailable"}

        route = self.routes_service.get_driving_route(origin, destination)
        if not getattr(route, "success", False):
            return {**base, "outcome": "failed", "reason": getattr(route, "status", None) or "route_failed"}

        travel = self.build_worker_travel(worker, route, destination)
        fingerprint = self.evidence_service.compute_route_fingerprint(travel)

        previous = existing or {}
        # 指纹一致且附件还在 -> 无需重生成（地址没变）。force 用于用户明确要求重做。
        if (
            not force
            and previous.get("route_fingerprint") == fingerprint
            and previous.get("attachment_id")
        ):
            return {
                **base,
                "outcome": "reused",
                "reason": "fingerprint_match",
                "reported_miles": travel.reported_miles,
            }

        record = self.evidence_service.generate_evidence(
            travel,
            draft_id=report_id,
            service_order_id=order_id,
            report_date=report_date,
            generated_by=self.uploaded_by,
            # 传入空列表：幂等判断已在上面用指纹 + 附件存在性做过了。
            draft_evidence_records=[],
        )
        if getattr(record, "evidence_status", None) != "ready" or not getattr(
            record, "file_relative_path", None
        ):
            return {
                **base,
                "outcome": "failed",
                "reason": getattr(record, "error", None) or "evidence_generation_failed",
            }

        source_path = self.evidence_absolute_path(report_id, record.file_relative_path)
        original_filename = f"mileage-evidence-{worker.get('name') or user_id}-{report_date}.png"
        try:
            attachment_id = self.attachment_saver(
                report_id,
                source_path,
                original_filename,
                previous.get("attachment_id"),
            )
        except Exception:
            logger.exception("assist: attach evidence failed for worker %s", user_id)
            return {**base, "outcome": "failed", "reason": "attach_failed"}

        return {
            **base,
            "outcome": "generated",
            "attachment_id": attachment_id,
            "route_fingerprint": fingerprint,
            "one_way_miles": travel.one_way_miles,
            "reported_miles": travel.reported_miles,
            "travel_hours": travel.travel_hours,
            "trip_type": travel.trip_type,
        }
