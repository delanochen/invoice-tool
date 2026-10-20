"""WhatsApp channel adapter for PP Field AI."""

from .routes import register_whatsapp_routes
from .services import WhatsAppService

__all__ = ["WhatsAppService", "register_whatsapp_routes"]
