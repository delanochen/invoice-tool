"""Knowledge-base routes and storage services."""

from .routes import register_knowledge_routes
from .services import KnowledgeService

__all__ = ["KnowledgeService", "register_knowledge_routes"]
