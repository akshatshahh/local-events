class AppError(Exception):
    """Base class for errors we deliberately surface to the client."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProviderUnavailable(AppError):
    """A provider failed in a way the user can only retry (5xx, timeout, network)."""

    status_code = 503
    code = "provider_unavailable"


class ProviderAuthError(AppError):
    """Our credentials for a provider are missing or rejected. An operator must fix this."""

    status_code = 502
    code = "provider_auth_error"


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class LocationNotFound(AppError):
    status_code = 404
    code = "location_not_found"


class CapacityExceeded(AppError):
    """Pending plus confirmed spots would pass the per-event cap."""

    status_code = 409
    code = "capacity_exceeded"


class PaymentsNotConfigured(AppError):
    status_code = 503
    code = "payments_not_configured"


class CheckoutFailed(AppError):
    """Stripe did not open a session. The pending hold was released."""

    status_code = 502
    code = "checkout_failed"


class AmbiguousLocation(AppError):
    """JamBase returned several cities; we do not pick one by event volume."""

    status_code = 409
    code = "ambiguous_location"

    def __init__(self, message: str, candidates: list):
        super().__init__(message)
        self.candidates = candidates
