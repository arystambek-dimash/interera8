class BaseError(Exception):
    message: str = ""
    status_code: int

    def __init__(self, message: str) -> None:
        self.message = message


class NotFoundException(BaseError):
    message = "Not found"
    status_code = 404


class UnauthorizedException(BaseError):
    message = "Unauthorized"
    status_code = 401


class ForbiddenException(BaseError):
    message = "Forbidden"
    status_code = 403


class InternalServerException(BaseError):
    message = "Internal Server Error"
    status_code = 500


class BadRequestException(BaseError):
    message = "Bad Request"
    status_code = 400


class InsufficientFundsError(BaseError):
    message = "Insufficient Funds"
    status_code = 403
