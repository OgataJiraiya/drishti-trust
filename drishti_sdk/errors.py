"""Bounded SDK error types; request bodies and credentials are never included."""


class DrishtiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message[:512])
        self.status_code = status_code


class AuthenticationError(DrishtiError):
    pass


class ConflictError(DrishtiError):
    pass


class ValidationError(DrishtiError):
    pass


class AssessmentStateError(ConflictError):
    pass
