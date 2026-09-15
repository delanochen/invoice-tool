"""AI Daily Report - TravelService (Phase 2)

Manages per-worker travel information:
- Origin resolution with priority: user_input > draft_existing > employee_default
- Destination from service_order.site_address (protected, not overwritten by AI)
- Overnight stay per-worker
- Transportation mode (only self_drive enters mileage flow)
- Missing field verification
- Route invalidation when travel inputs change

Does NOT call Google Routes (Phase 3). one_way_miles/reported_miles stay null.

IMPORTANT: Any change to origin/destination/overnight_stay/transportation
MUST invalidate previously computed route data (one_way_miles, reported_miles,
route_polyline, route_duration_seconds, mileage_evidence_path). This prevents
stale route data from being used after inputs change.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .schemas import WorkerTravel

logger = logging.getLogger(__name__)

# Fields that must be cleared when route inputs change
ROUTE_OUTPUT_FIELDS = [
    "route_distance_meters",
    "one_way_miles",
    "reported_miles",
    "route_polyline",
    "route_duration_seconds",
    "route_provider",
    "route_query_time",
    "route_status",
    "route_error",
    "origin_normalized",
    "destination_normalized",
    "mileage_evidence_path",
]


class TravelService:
    """Build and validate per-worker travel data."""

    def __init__(self, db_conn, employee_resolution_service):
        self.db = db_conn
        self.employee_resolution = employee_resolution_service

    @staticmethod
    def invalidate_worker_route(worker: WorkerTravel) -> None:
        """Clear all computed route/mileage fields for a worker.

        Called whenever origin, destination, overnight_stay, or transportation
        changes. Prevents stale route data (Phase 3+).
        Sets route_status to "not_calculated".
        """
        worker.route_distance_meters = None
        worker.one_way_miles = None
        worker.reported_miles = None
        worker.route_polyline = None
        worker.route_duration_seconds = None
        worker.route_provider = None
        worker.route_query_time = None
        worker.route_status = "not_calculated"
        worker.route_error = None
        worker.origin_normalized = None
        worker.destination_normalized = None
        worker.mileage_evidence_path = None

    def build_worker_travel(
        self,
        user_id: int,
        name: str,
        transportation: str = "self_drive",
        user_input_origin: Optional[str] = None,
        draft_existing_origin: Optional[str] = None,
        draft_existing_origin_confirmed: bool = False,
        destination: Optional[str] = None,
        overnight_stay: Optional[bool] = None,
    ) -> WorkerTravel:
        """Build a WorkerTravel with origin resolution.

        Origin priority:
        1. user_input_origin (from current message) -> origin_confirmed=True
        2. draft_existing_origin (already in draft) -> keep existing confirmation
        3. employee_default (users.address) -> origin_confirmed=False, must verify
        """
        origin = None
        origin_source = None
        origin_confirmed = False

        if user_input_origin:
            origin = user_input_origin.strip()
            origin_source = "user_input"
            origin_confirmed = True
        elif draft_existing_origin and draft_existing_origin_confirmed:
            origin = draft_existing_origin
            origin_source = "draft_existing"
            origin_confirmed = True
        elif draft_existing_origin:
            # Existing but unconfirmed - keep it, still needs verification
            origin = draft_existing_origin
            origin_source = "draft_existing"
            origin_confirmed = False
        else:
            # Try employee default address (suggestion only)
            default_addr = self.employee_resolution.get_employee_default_address(user_id)
            if default_addr:
                origin = default_addr
                origin_source = "employee_default"
                origin_confirmed = False

        return WorkerTravel(
            user_id=user_id,
            name=name,
            transportation=transportation,
            origin=origin,
            origin_source=origin_source,
            origin_confirmed=origin_confirmed,
            destination=destination,
            destination_source="service_order" if destination else None,
            overnight_stay=overnight_stay,
        )

    def confirm_employee_default_origin(
        self, workers: List[WorkerTravel], user_id: int
    ) -> bool:
        """User confirms an employee_default origin is correct.

        Sets origin_confirmed=True but KEEPS origin_source=employee_default.
        This preserves audit trail: address came from employee default,
        but was confirmed by user for this day.
        Returns True if worker found.
        """
        for w in workers:
            if w.user_id == user_id and w.origin_source == "employee_default":
                w.origin_confirmed = True
                # Do NOT change origin_source - stays "employee_default"
                return True
        return False

    def set_destination_for_all(
        self, workers: List[WorkerTravel], destination: Optional[str]
    ) -> List[WorkerTravel]:
        """Set destination for all workers from service_order.site_address.

        This is the ONLY allowed way to set destination.
        AI Actions cannot overwrite destination directly.
        """
        for w in workers:
            old_dest = w.destination
            w.destination = destination
            w.destination_source = "service_order" if destination else None
            if old_dest != destination:
                self.invalidate_worker_route(w)
        return workers

    def set_overnight_for_all(
        self, workers: List[WorkerTravel], overnight: bool
    ) -> List[WorkerTravel]:
        """Batch set overnight_stay for all workers. Invalidates routes."""
        for w in workers:
            if w.overnight_stay != overnight:
                w.overnight_stay = overnight
                self.invalidate_worker_route(w)
        return workers

    def set_overnight_for_worker(
        self, workers: List[WorkerTravel], user_id: int, overnight: bool
    ) -> bool:
        """Set overnight_stay for a specific worker by user_id. Invalidates route."""
        for w in workers:
            if w.user_id == user_id:
                if w.overnight_stay != overnight:
                    w.overnight_stay = overnight
                    self.invalidate_worker_route(w)
                return True
        return False

    def set_transportation_for_worker(
        self, workers: List[WorkerTravel], user_id: int, transportation: str
    ) -> bool:
        """Set transportation for a specific worker. Invalidates route if changed."""
        for w in workers:
            if w.user_id == user_id:
                if w.transportation != transportation:
                    w.transportation = transportation
                    self.invalidate_worker_route(w)
                return True
        return False

    def find_worker_by_user_id(
        self, workers: List[WorkerTravel], user_id: int
    ) -> Optional[WorkerTravel]:
        """Find worker by user_id (primary identity)."""
        for w in workers:
            if w.user_id == user_id:
                return w
        return None

    def update_worker_origin(
        self, workers: List[WorkerTravel], user_id: int, origin: str
    ) -> bool:
        """Update a worker's origin from user input (confirmed). Invalidates route."""
        for w in workers:
            if w.user_id == user_id:
                if w.origin != origin.strip():
                    w.origin = origin.strip()
                    w.origin_source = "user_input"
                    w.origin_confirmed = True
                    self.invalidate_worker_route(w)
                return True
        return False

    def verify_travel_fields(
        self, workers: List[WorkerTravel], site_address_present: bool
    ) -> Tuple[List[str], List[str]]:
        """Check for missing travel fields.

        Returns:
            (missing_fields, clarification_messages)
        """
        missing = []
        messages = []

        if not site_address_present:
            missing.append("site_address")
            messages.append("当前工单对应的站点没有地址，请先补充 Site Address。")

        for w in workers:
            if w.transportation == "self_drive":
                if not w.origin:
                    missing.append(f"worker_{w.user_id}_origin")
                    messages.append(f"{w.name} 当天从哪里出发？")
                elif not w.origin_confirmed:
                    missing.append(f"worker_{w.user_id}_origin_unconfirmed")
                    messages.append(f"{w.name} 的出发地址（{w.origin}）是否正确？")

                if w.overnight_stay is None:
                    missing.append(f"worker_{w.user_id}_overnight_stay")
                    # Only add overnight question once if all workers need it
                    if not any("当天是否住宿" in m for m in messages):
                        messages.append("当天是否住宿？")

        return missing, messages

    def needs_mileage_calculation(self, worker: WorkerTravel) -> bool:
        """Check if a worker should enter the mileage calculation flow.

        Only self_drive workers with confirmed origin and destination need mileage.
        """
        return (
            worker.transportation == "self_drive"
            and bool(worker.origin)
            and worker.origin_confirmed
            and bool(worker.destination)
            and worker.overnight_stay is not None
        )
