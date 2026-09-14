class ValidationError(ValueError):
    """Raised when page input fails business validation."""


class NotFoundError(LookupError):
    """Raised when an entity does not exist."""
