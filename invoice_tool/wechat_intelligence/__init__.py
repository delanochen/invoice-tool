"""WeChat work-group evidence ingestion for PP Field AI."""

from .routes import register_wechat_intelligence_routes
from .services import WeChatIntelligenceService

__all__ = ["WeChatIntelligenceService", "register_wechat_intelligence_routes"]
