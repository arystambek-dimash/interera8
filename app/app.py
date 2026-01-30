from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.di import AppContainer
from src.presentation.http.rest.api.exception_hanlder import register_error_handlers
from src.presentation.http.rest.api.v1 import interera


class AppFactory:
    def __init__(self):
        self.container = AppContainer()
        self.app = FastAPI()

        self._configure_container()
        self._setup_middlewares()
        self._configure_exception_handlers()
        self._configure_app_state()
        self._configure_routes()

    def _configure_container(self):
        self.container.wire(modules=[interera])
        self.settings = self.container.settings()

    def _setup_middlewares(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _configure_routes(self):
        self.app.include_router(
            interera.router,
            prefix="/api/v1/interera",
            tags=["Interera"],
        )

    def _configure_app_state(self):
        self.app.state.settings = self.settings

    def _configure_exception_handlers(self):
        register_error_handlers(self.app)


app: FastAPI = AppFactory().app
