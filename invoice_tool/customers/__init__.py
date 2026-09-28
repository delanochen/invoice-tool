"""Customer and site domain registration."""

from .routes import register_customer_routes
from .services import build_customer_services

__all__ = ["build_customer_services", "register_customer_routes"]
