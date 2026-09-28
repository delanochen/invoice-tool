"""Base catalog domain registration."""

from .routes import register_catalog_routes
from .services import (
    MRO_CANONICAL_PROJECT_NAME,
    build_catalog_services,
    is_mro_alias_project_name,
    is_mro_project_name,
    normalized_project_name,
    project_name_key,
)

__all__ = [
    "MRO_CANONICAL_PROJECT_NAME",
    "build_catalog_services",
    "is_mro_alias_project_name",
    "is_mro_project_name",
    "normalized_project_name",
    "project_name_key",
    "register_catalog_routes",
]
