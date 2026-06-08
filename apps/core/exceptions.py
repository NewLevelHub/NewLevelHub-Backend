import logging

from django.utils.encoding import force_str
from rest_framework import status
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    NotFound,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.core.error_codes import (
    SERVER_ERROR,
    NOT_FOUND,
    PERMISSION_DENIED,
    SESSION_IDLE_TIMEOUT,
    UNAUTHENTICATED,
    VALIDATION_ERROR,
)
from apps.users.authentication import SessionIdleTimeout
from apps.core.i18n import get_lang, translate

logger = logging.getLogger(__name__)


def raise_validation_error(field: str, i18n_key: str, params: dict = None):
    """Raise a field-level ValidationError with an i18n marker instead of a raw string.

    Usage::

        raise_validation_error('parent_id', 'storage.parent_folder_not_found')
        raise_validation_error('period', 'analytics.invalid_period', {'choices': '7d, 30d, 90d'})

    The custom_exception_handler will translate the marker into the correct locale.
    """
    raise DRFValidationError({field: [{'_i18n': True, 'key': i18n_key, 'params': params or {}}]})


class LocalizedError(DRFValidationError):
    """
    Raise this instead of plain ValidationError when you have an i18n key.

    Usage::

        raise LocalizedError(
            code='BOOKING_ADVANCE_DAYS_EXCEEDED',
            i18n_key='booking.advance_days_exceeded',
            params={'advance_days': 14},
        )

    The custom_exception_handler will pick up ``code`` + ``i18n_key`` and
    render the localized message automatically.
    """

    def __init__(self, code: str, i18n_key: str, params: dict = None, http_status: int = 400):
        self.error_code = code
        self.i18n_key = i18n_key
        self.i18n_params = params or {}
        self.status_code = http_status
        super().__init__(
            detail={'_i18n': True, 'code': code, 'key': i18n_key, 'params': params or {}}
        )


def _build_error_response(code: str, message: str, details=None, http_status: int = 400) -> Response:
    """Build the unified error response envelope."""
    payload = {
        'success': False,
        'error': {
            'code': code,
            'message': message,
            'details': details if details is not None else {},
        },
    }
    return Response(payload, status=http_status)


def custom_exception_handler(exc, context):  # noqa: C901
    response = exception_handler(exc, context)

    request = context.get('request')
    lang = get_lang(request) if request is not None else 'ru'

    # ── LocalizedError — developer-raised with explicit i18n key ──────────
    if isinstance(exc, LocalizedError):
        i18n_key = exc.i18n_key
        params = exc.i18n_params or {}
        message = translate(i18n_key, lang, **params)
        http_status = getattr(exc, 'status_code', status.HTTP_400_BAD_REQUEST)
        return _build_error_response(exc.error_code, message, http_status=http_status)

    if response is None:
        logger.exception('Unhandled exception', exc_info=exc)
        message = translate('common.server_error', lang)
        return _build_error_response(SERVER_ERROR, message, http_status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    http_status = response.status_code

    # ── 401 Unauthenticated ───────────────────────────────────────────────
    if isinstance(exc, SessionIdleTimeout):
        message = translate('auth.session_idle_timeout', lang)
        return _build_error_response(SESSION_IDLE_TIMEOUT, message, http_status=http_status)

    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        message = translate('common.unauthenticated', lang)
        return _build_error_response(UNAUTHENTICATED, message, http_status=http_status)

    # ── 403 Permission Denied ─────────────────────────────────────────────
    if isinstance(exc, PermissionDenied):
        generic_message = translate('common.permission_denied', lang)
        # Preserve structured detail (e.g. company_not_assigned code) in details
        detail = exc.detail
        details = {}
        if isinstance(detail, dict):
            details = detail
        elif isinstance(detail, str):
            details = {'detail': detail}
        # Surface a specific reason string as message only when it was explicitly set
        # by application code — not when it's the DRF default permission-denied text.
        specific_message = None
        if isinstance(detail, str) and str(detail) != force_str(PermissionDenied.default_detail):
            specific_message = str(detail)
        message = specific_message if specific_message else generic_message
        return _build_error_response(PERMISSION_DENIED, message, details=details, http_status=http_status)

    # ── 404 Not Found ─────────────────────────────────────────────────────
    if isinstance(exc, NotFound):
        message = translate('common.not_found', lang)
        return _build_error_response(NOT_FOUND, message, http_status=http_status)

    # ── 400 Validation Error ──────────────────────────────────────────────
    if isinstance(exc, DRFValidationError):
        detail = exc.detail

        # LocalizedError already handled above; this handles plain ValidationError.
        # When LocalizedError is raised inside a serializer's validate(), DRF re-wraps
        # its dict detail into field-keyed lists: {'_i18n': [ErrorDetail('True')], ...}.
        # We detect both the raw dict form and the re-wrapped list form.
        if isinstance(detail, dict) and detail.get('_i18n'):
            # Unwrap list values produced by DRF's re-wrapping (e.g. ErrorDetail → str)
            def _unwrap(v):
                if isinstance(v, list):
                    return str(v[0]) if v else ''
                return v

            i18n_key = _unwrap(detail.get('key', 'common.validation_error'))
            raw_params = detail.get('params') or {}
            params = (
                {k: str(v) for k, v in raw_params.items()}
                if isinstance(raw_params, dict)
                else {}
            )
            message = translate(i18n_key or 'common.validation_error', lang, **params)
            code = _unwrap(detail.get('code', VALIDATION_ERROR)) or VALIDATION_ERROR
            return _build_error_response(code, message, http_status=http_status)

        # Normalise detail to a serialisable dict/list for the ``details`` field
        if isinstance(detail, dict):
            details = _serialise_detail(detail, lang)
        elif isinstance(detail, list):
            details = {'non_field_errors': _serialise_list(detail, lang)}
        else:
            details = {'detail': str(detail)}

        # Surface the most relevant message: first error from the first field
        # (or first non_field_error), rather than a generic fallback.
        message = _extract_first_message(details) or translate('common.validation_error', lang)

        return _build_error_response(VALIDATION_ERROR, message, details=details, http_status=http_status)

    # ── Fallback for any other DRF exception ─────────────────────────────
    message = translate('common.server_error', lang)
    detail = response.data
    details = {}
    if isinstance(detail, dict):
        details = _serialise_detail(detail, lang)
    elif isinstance(detail, str):
        details = {'detail': detail}

    return _build_error_response(SERVER_ERROR, message, details=details, http_status=http_status)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_first_message(details: dict) -> str:
    """Return the first human-readable error string from a serialised details dict.

    Priority: ``non_field_errors`` first (if present), then the first field key
    alphabetically.  Returns an empty string if nothing useful is found.
    """
    if not isinstance(details, dict):
        return ''
    # Prefer non_field_errors as they describe the overall object, not a single field
    for key in ('non_field_errors', *[k for k in details if k != 'non_field_errors']):
        value = details.get(key)
        if value is None:
            continue
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first
            if isinstance(first, list) and first:
                return str(first[0])
        elif isinstance(value, str) and value:
            return value
    return ''


def _serialise_list(items, lang: str = 'ru') -> list:
    """Recursively convert a list of ErrorDetail / nested structures to plain strings.

    Items that are dicts with ``_i18n: True`` are translated using the supplied locale.
    """
    result = []
    for item in items:
        if isinstance(item, list):
            result.append(_serialise_list(item, lang))
        elif isinstance(item, dict):
            if item.get('_i18n'):
                result.append(translate(item.get('key', 'common.validation_error'), lang, **item.get('params', {})))
            else:
                result.append(_serialise_detail(item, lang))
        else:
            result.append(str(item))
    return result


def _serialise_detail(detail: dict, lang: str = 'ru') -> dict:
    """Recursively convert ErrorDetail values to plain strings.

    Dict values with ``_i18n: True`` are translated using the supplied locale.
    """
    result = {}
    for key, value in detail.items():
        if isinstance(value, list):
            result[key] = _serialise_list(value, lang)
        elif isinstance(value, dict):
            if value.get('_i18n'):
                result[key] = translate(value.get('key', 'common.validation_error'), lang, **value.get('params', {}))
            else:
                result[key] = _serialise_detail(value, lang)
        else:
            result[key] = str(value)
    return result
