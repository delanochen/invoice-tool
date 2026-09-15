"""AI Daily Report - WorkOrderContextService (Phase 2)

Provides context for Daily Report Draft creation:
- current service_order
- service_order.site_address
- allowed/related workers for the order
- current business date
- current user

Does NOT send full tables to DeepSeek. Only provides minimal context needed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorkOrderContextService:
    """Fetch and normalize work order context for AI daily report."""

    def __init__(self, db_conn, current_user_id: int, current_user_name: str):
        self.db = db_conn
        self.current_user_id = current_user_id
        self.current_user_name = current_user_name

    def get_context(self, service_order_id: int, report_date: str) -> Dict[str, Any]:
        """Get minimal context for a service order.

        Returns dict with: order, site_address, workers (id+name only),
        current_user. Never includes full user records or addresses.
        """
        order = self.db.execute(
            "select id, order_number, client_id, client_name, site_address, status "
            "from service_orders where id = ?",
            (service_order_id,),
        ).fetchone()

        if not order:
            return {"exists": False, "order": None}

        site_address = order["site_address"] or ""

        # Get workers related to this order (assigned technicians + current user)
        # Phase 2: include current user always; other workers from order assignments
        workers = self._get_order_workers(service_order_id)

        # Ensure current user is in the list
        if not any(w["user_id"] == self.current_user_id for w in workers):
            workers.insert(0, {
                "user_id": self.current_user_id,
                "name": self.current_user_name,
            })

        return {
            "exists": True,
            "order": {
                "id": order["id"],
                "order_number": order["order_number"],
                "client_name": order["client_name"],
                "site_address": site_address,
                "status": order["status"],
            },
            "site_address": site_address,
            "site_address_present": bool(site_address.strip()),
            "workers": workers,  # only id+name, no sensitive data
            "current_user": {
                "user_id": self.current_user_id,
                "name": self.current_user_name,
            },
            "report_date": report_date,
        }

    def _get_order_workers(self, service_order_id: int) -> List[Dict[str, Any]]:
        """Get workers assigned to or related to this service order.

        Returns only user_id and name (no email/address/phone).
        Falls back to active internal users if no specific assignment found.
        """
        # Try order-specific assignments first (if such table/column exists)
        workers = []

        # Check if service_orders has assigned_user_id or similar
        cols = [r["name"] for r in self.db.execute(
            "pragma table_info(service_orders)"
        ).fetchall()]

        if "assigned_user_id" in cols:
            row = self.db.execute(
                "select assigned_user_id from service_orders where id = ?",
                (service_order_id,),
            ).fetchone()
            if row and row["assigned_user_id"]:
                user = self.db.execute(
                    "select id, name from users where id = ?",
                    (row["assigned_user_id"],),
                ).fetchone()
                if user:
                    workers.append({"user_id": user["id"], "name": user["name"]})

        # Check for order_technicians junction table
        tables = [r["name"] for r in self.db.execute(
            "select name from sqlite_master where type='table'"
        ).fetchall()]

        if "service_order_technicians" in tables:
            rows = self.db.execute(
                """select u.id, u.name from service_order_technicians ot
                   join users u on u.id = ot.user_id
                   where ot.service_order_id = ?""",
                (service_order_id,),
            ).fetchall()
            for r in rows:
                if not any(w["user_id"] == r["id"] for w in workers):
                    workers.append({"user_id": r["id"], "name": r["name"]})

        # If no specific workers, include all active internal users (id+name only)
        if not workers:
            rows = self.db.execute(
                "select id, name from users where role in ('admin', 'manager', 'user') "
                "and is_active = 1 order by name"
            ).fetchall()
            workers = [{"user_id": r["id"], "name": r["name"]} for r in rows]

        return workers

    def get_site_address(self, service_order_id: int) -> Optional[str]:
        """Get site address for an order. Returns None if order not found."""
        row = self.db.execute(
            "select site_address from service_orders where id = ?",
            (service_order_id,),
        ).fetchone()
        return row["site_address"] if row else None
