"""Employee-domain route registration."""

from .grade_routes import register_employee_grade_routes
from .user_routes import register_employee_user_routes
from .user_services import build_user_services

__all__ = [
    "build_user_services",
    "register_employee_grade_routes",
    "register_employee_user_routes",
]
