"""AI Daily Report - MileageService (Phase 3A)

Centralized mileage calculation and rounding policy.

Meters to miles conversion:
    one_way_miles = distance_meters / 1609.344

Rounding policy:
    Existing project uses REAL (float) for driving_miles, car_mileage_rate=0.5.
    No integer rounding required by existing business rules.
    We preserve at least 2 decimal places via round(x, 2).
    Raw distance_meters is always saved for audit.

Reported mileage (v0.1.273: driven by explicit trip_type, no longer by 住宿):
    trip_type == round_trip（默认） -> one_way_miles * 2
    trip_type == one_way            -> one_way_miles

Route eligibility (ALL must be true):
    transportation == self_drive
    origin != null
    origin_confirmed == true
    destination != null

DeepSeek CANNOT provide distance/miles/polyline. These only come from
GoogleRoutesService + backend calculation.
"""
from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

from .schemas import WorkerTravel
from .google_routes import GoogleRoutesService, RouteResult
from trip_policy import trip_multiplier

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344
MILEAGE_ROUND_DECIMALS = 2  # Preserve at least 2 decimal places (existing project uses REAL)

# v0.1.243 (user decision): Google route durations are consistently optimistic
# (too low). Apply an uplift factor and round UP to the nearest 15 minutes.
TRAVEL_HOURS_UPLIFT = 1.15


class MileageService:
    """Calculate and apply mileage for workers."""

    def __init__(self, routes_service: GoogleRoutesService):
        self.routes = routes_service

    @staticmethod
    def meters_to_miles(meters: float) -> float:
        """Convert meters to miles. No rounding here - raw value."""
        return meters / METERS_PER_MILE

    @staticmethod
    def round_miles(miles: float) -> float:
        """Apply unified rounding policy: 2 decimal places."""
        return round(miles, MILEAGE_ROUND_DECIMALS)

    @staticmethod
    def is_eligible(worker: WorkerTravel) -> Tuple[bool, Optional[str]]:
        """Check if worker is eligible for route calculation.

        Returns:
            (eligible, reason_if_not)
        """
        if worker.transportation != "self_drive":
            return False, f"transportation={worker.transportation}, not self_drive"
        if not worker.origin:
            return False, "origin is null"
        if not worker.origin_confirmed:
            return False, "origin not confirmed (employee_default)"
        if not worker.destination:
            return False, "destination is null"
        return True, None

    @staticmethod
    def duration_to_travel_hours(duration_seconds: Optional[int], trip_type: Optional[str] = None) -> float:
        """Convert one-way route duration into per-worker traffic hours.

        Same round-trip semantics as reported_miles: round_trip doubles the
        one-way duration, one_way keeps it. Uplift factor compensates for
        Google's optimistic estimates; result rounds UP to the nearest 0.25 h.
        """
        if not duration_seconds or duration_seconds <= 0:
            return 0.0
        trips = trip_multiplier(trip_type)
        minutes = (duration_seconds / 60.0) * trips * TRAVEL_HOURS_UPLIFT
        rounded_minutes = math.ceil(minutes / 15) * 15
        return round(rounded_minutes / 60, 2)

    def ensure_travel_hours(self, worker: WorkerTravel) -> None:
        """Auto-fill travel_hours from the route duration (v0.1.243).

        Never overwrites a manually entered value (travel_hours_source ==
        'user_input'). Re-derives on route recalculation while the value is
        still auto_route so changed origins/destinations stay in sync.
        """
        if worker.travel_hours_source == "user_input":
            return
        if worker.route_status == "success" and worker.route_duration_seconds:
            worker.travel_hours = self.duration_to_travel_hours(
                worker.route_duration_seconds, worker.trip_type
            )
            worker.travel_hours_source = "auto_route"

    def calculate_for_worker(self, worker: WorkerTravel) -> WorkerTravel:
        """Calculate mileage for a single worker.

        Calls Google Routes if eligible. Updates worker in place.
        Returns the worker for convenience.
        """
        eligible, reason = self.is_eligible(worker)
        if not eligible:
            worker.route_status = "verification_required"
            worker.route_error = reason
            return worker

        # Already has successful route - don't re-fetch (cache by data)
        if worker.route_status == "success" and worker.route_distance_meters:
            self.ensure_travel_hours(worker)
            return worker

        result = self.routes.get_driving_route(worker.origin, worker.destination)

        if not result.success:
            worker.route_status = result.status
            worker.route_error = result.error
            # Clear any stale partial data
            worker.route_distance_meters = None
            worker.one_way_miles = None
            worker.reported_miles = None
            if worker.travel_hours_source != "user_input":
                worker.travel_hours = None
                worker.travel_hours_source = None
            return worker

        # Save raw meters first (audit)
        worker.route_distance_meters = result.distance_meters
        worker.route_duration_seconds = result.duration_seconds
        worker.route_polyline = result.encoded_polyline
        worker.origin_normalized = result.origin_normalized
        worker.destination_normalized = result.destination_normalized
        worker.route_provider = result.provider
        worker.route_query_time = result.query_time
        worker.route_status = "success"
        worker.route_error = None

        # Calculate miles
        raw_one_way = self.meters_to_miles(result.distance_meters)
        worker.one_way_miles = self.round_miles(raw_one_way)

        # Reported mileage based on explicit trip type (默认往返 → ×2)
        worker.reported_miles = self.round_miles(raw_one_way * trip_multiplier(worker.trip_type))

        worker.mileage_verification_required = False
        self.ensure_travel_hours(worker)
        return worker

    def calculate_for_all(self, workers: list) -> list:
        """Calculate mileage for all eligible workers."""
        for w in workers:
            self.calculate_for_worker(w)
        return workers
