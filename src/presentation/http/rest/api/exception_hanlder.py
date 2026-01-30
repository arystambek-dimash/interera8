from fastapi import status, Request
from starlette.responses import JSONResponse

from src.domain.exceptions import BaseError


def register_error_handlers(app):
    @app.exception_handler(BaseError)
    async def not_found_handler(request: Request, exc: BaseError):
        return JSONResponse(
            status_code=status.__getattr__(f"HTTP_{exc.status_code}_NOT_FOUND"),
            content={"message": str(exc.message)},
        )
