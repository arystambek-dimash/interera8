from dependency_injector import containers, providers

from app.conf import Settings
from src.infrastructure.integrations.gemini_service import GeminiService


class AppContainer(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings)

    gemini_service = providers.Factory(
        GeminiService,
        api_key=settings.provided.GEMINI_API_TOKEN,
    )