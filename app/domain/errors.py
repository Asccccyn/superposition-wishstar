"""领域异常：服务层抛出，API 层统一转换成 HTTP 响应。"""


class DomainError(Exception):
    code = "domain_error"
    http_status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    code = "validation_error"


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404


class PermissionDeniedError(DomainError):
    code = "permission_denied"
    http_status = 403


class InvalidStateError(DomainError):
    code = "invalid_state"


class BottleEmptyError(DomainError):
    code = "bottle_empty"


class DuplicateError(DomainError):
    code = "duplicate"
