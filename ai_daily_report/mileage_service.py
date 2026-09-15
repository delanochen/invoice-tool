"""AI Daily Report - MileageService (Phase 3A)

Centralized mileage calculation and rounding policy.

Meters to miles conversion:
    one_way_miles = distance_meters / 1609.344

Rounding policy:
    Existing project uses REAL (float) for driving_miles, car_mileage_rate=0.5.
    No integer rounding required by existing business rules.
    We preserve at least 2 decimal places via round(x, 2).
    Raw distance_meters is always saved for audit.

Reported mileage:
    overnight_stay == false -> one_way_miles * 2 (round trip)
    overnight_stay == true  -> one_way_miles (one way only)
    overnight_stay == null  -> DO NOT calculate

Route eligibility (ALL must be true):
    transportation == self_drive
    origin != null
    origin_confirmed == true
    destination != null
    overnight_stay != null

DeepSeek CANNOT provide distance/miles/polyline. These only come from
GoogleRoutesService + backend calculation.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from .schemas import WorkerTravel
from .google_routes import GoogleRoutesService, RouteResult

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344
MILEAGE_ROUND_DECIMALS = 2  # Preserve at least 2 decimal places (existing project uses REAL)


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
        if worker.overnight_stay is None:
            return False, "overnight_stay is null"
        return True, None

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
            return worker

        result = self.routes.get_driving_route(worker.origin, worker.destination)

        if not result.success:
            worker.route_status = result.status
            worker.route_error = result.error
            # Clear any stale partial data
            worker.route_distance_meters = None
            worker.one_way_miles = None
            worker.reported_miles = None
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

        # Reported mileage based on overnight
        if worker.overnight_stay is False:
            worker.reported_miles = self.round_miles(raw_one_way * 2)
        else:  # overnight_stay is True
            worker.reported_miles = self.round_miles(raw_one_way)

        worker.mileage_verification_required = False
        return worker

    def calculate_for_all(self, workers: list) -> list:
        """Calculate mileage for all eligible workers."""
        for w in workers:
            self.calculate_for_worker(w)
        return workers
