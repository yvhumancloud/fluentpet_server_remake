class ApiError(Exception):
    status = 400
    code = "bad_request"

    def __init__(
        self, message: str | None = None, *, code: str | None = None, status: int | None = None
    ):
        self.message = message or self.code.replace("_", " ")
        if code:
            self.code = code
        if status:
            self.status = status


class NotFound(ApiError):
    status = 404
    code = "not_found"


class Unauthenticated(ApiError):
    status = 401
    code = "unauthenticated"


class Forbidden(ApiError):
    status = 403
    code = "forbidden"


class Conflict(ApiError):
    status = 409
    code = "conflict"
