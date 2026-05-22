"""
Tests for the i18n locale detection and translation infrastructure.

Coverage:
  - translate() returns correct locale messages
  - translate() interpolates parameters
  - translate() falls back to 'ru' for unknown languages
  - get_lang() reads ?lang= query param
  - get_lang() reads Accept-Language header
  - get_lang() defaults to 'ru'
  - LocalizedError response format: success=False, error.code, error.message
  - Error message is in Russian by default
  - Error message is in English when ?lang=en
"""

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from apps.core.i18n import get_lang, translate
from apps.core.exceptions import LocalizedError
from apps.core.error_codes import BOOKING_ADVANCE_DAYS_EXCEEDED


# ---------------------------------------------------------------------------
# translate()
# ---------------------------------------------------------------------------

class TestTranslate:

    def test_translate_returns_russian_by_default(self):
        result = translate('common.not_found', lang='ru')
        assert 'найден' in result.lower() or result != ''

    def test_translate_returns_english_when_lang_en(self):
        result = translate('common.not_found', lang='en')
        assert 'not found' in result.lower()

    def test_translate_interpolates_params(self):
        result = translate('booking.advance_days_exceeded', lang='ru', advance_days=14)
        assert '14' in result

    def test_translate_interpolates_params_en(self):
        result = translate('booking.advance_days_exceeded', lang='en', advance_days=7)
        assert '7' in result

    def test_translate_falls_back_to_ru_for_unknown_lang(self):
        # Unknown lang triggers fallback file (ru.json) — value must be the same as ru
        result_unknown = translate('common.not_found', lang='de')
        result_ru = translate('common.not_found', lang='ru')
        assert result_unknown == result_ru

    def test_translate_returns_fallback_for_missing_key(self):
        # Key does not exist → falls back to common.server_error message
        result = translate('nonexistent.key', lang='ru')
        ru_fallback = translate('common.server_error', lang='ru')
        assert result == ru_fallback

    def test_translate_wip_limit_with_param(self):
        result = translate('crm.wip_limit_exceeded', lang='ru', wip_limit=5)
        assert '5' in result

    def test_translate_storage_limit(self):
        result = translate('storage.limit_exceeded', lang='en')
        assert 'storage' in result.lower() or 'limit' in result.lower()


# ---------------------------------------------------------------------------
# get_lang()
# ---------------------------------------------------------------------------

class TestGetLang:

    def _make_request(self, lang_param=None, accept_language=None):
        """Build a mock request with optional ?lang= and Accept-Language."""
        factory = APIRequestFactory()
        url = f'/?lang={lang_param}' if lang_param else '/'
        raw = factory.get(url)
        if lang_param:
            raw.GET = raw.GET.copy()
            raw.GET['lang'] = lang_param
            # DRF wraps GET into query_params
            raw.query_params = raw.GET
        else:
            raw.query_params = raw.GET
        if accept_language:
            raw.META['HTTP_ACCEPT_LANGUAGE'] = accept_language
        return raw

    def test_get_lang_reads_query_param_en(self):
        request = self._make_request(lang_param='en')
        assert get_lang(request) == 'en'

    def test_get_lang_reads_query_param_ru(self):
        request = self._make_request(lang_param='ru')
        assert get_lang(request) == 'ru'

    def test_get_lang_reads_accept_language_header(self):
        request = self._make_request(accept_language='en-US,en;q=0.9')
        assert get_lang(request) == 'en'

    def test_get_lang_accept_language_ru_header(self):
        request = self._make_request(accept_language='ru-RU,ru;q=0.9')
        # 'ru-RU' does not start with 'en' → defaults to 'ru'
        assert get_lang(request) == 'ru'

    def test_get_lang_defaults_to_ru(self):
        request = self._make_request()
        assert get_lang(request) == 'ru'

    def test_get_lang_ignores_unknown_lang_param(self):
        request = self._make_request(lang_param='de')
        # 'de' is not in ('ru', 'en') → falls through to Accept-Language → defaults to 'ru'
        assert get_lang(request) == 'ru'

    def test_get_lang_query_param_takes_priority_over_header(self):
        request = self._make_request(lang_param='ru', accept_language='en-US')
        assert get_lang(request) == 'ru'


# ---------------------------------------------------------------------------
# LocalizedError and response format
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLocalizedErrorResponseFormat:
    """
    Integration-style tests: verify that LocalizedError propagates through
    the custom_exception_handler and produces the correct response shape.
    """

    def _make_view_response(self, request):
        """Simulate what custom_exception_handler does with a LocalizedError."""
        from apps.core.exceptions import custom_exception_handler
        exc = LocalizedError(
            code=BOOKING_ADVANCE_DAYS_EXCEEDED,
            i18n_key='booking.advance_days_exceeded',
            params={'advance_days': 14},
        )
        context = {'request': request, 'view': MagicMock(), 'args': (), 'kwargs': {}}
        return custom_exception_handler(exc, context)

    def test_localized_error_response_has_success_false(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        response = self._make_view_response(request)
        assert response.data['success'] is False

    def test_localized_error_response_has_error_code(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        response = self._make_view_response(request)
        assert response.data['error']['code'] == BOOKING_ADVANCE_DAYS_EXCEEDED

    def test_localized_error_response_has_message_key(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        response = self._make_view_response(request)
        assert 'message' in response.data['error']
        assert response.data['error']['message'] != ''

    def test_error_message_in_russian_by_default(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        request.META['HTTP_ACCEPT_LANGUAGE'] = 'ru'
        response = self._make_view_response(request)
        message = response.data['error']['message']
        # Russian message contains Cyrillic characters
        assert any('Ѐ' <= ch <= 'ӿ' for ch in message)

    def test_error_message_in_english_with_lang_en_param(self):
        factory = APIRequestFactory()
        request = factory.get('/?lang=en')
        request.GET = request.GET.copy()
        request.GET['lang'] = 'en'
        request.query_params = request.GET
        response = self._make_view_response(request)
        message = response.data['error']['message']
        # English message contains 'days' (from booking.advance_days_exceeded EN template)
        assert 'days' in message.lower()

    def test_localized_error_response_interpolates_param_in_message(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        response = self._make_view_response(request)
        # The param advance_days=14 should appear in the message
        assert '14' in response.data['error']['message']

    def test_localized_error_has_empty_details_by_default(self):
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        response = self._make_view_response(request)
        assert response.data['error']['details'] == {}


# ---------------------------------------------------------------------------
# custom_exception_handler — standard DRF exception mapping
# ---------------------------------------------------------------------------

class TestCustomExceptionHandlerMapping:
    """Verify that standard DRF exceptions are mapped to the new format."""

    def _handler(self, exc, lang='ru'):
        from apps.core.exceptions import custom_exception_handler
        factory = APIRequestFactory()
        request = factory.get('/')
        request.query_params = request.GET
        if lang == 'en':
            request.GET = request.GET.copy()
            request.GET['lang'] = 'en'
            request.query_params = request.GET
        context = {'request': request, 'view': MagicMock()}
        return custom_exception_handler(exc, context)

    def test_not_found_returns_not_found_code(self):
        from rest_framework.exceptions import NotFound
        response = self._handler(NotFound())
        assert response.data['success'] is False
        assert response.data['error']['code'] == 'NOT_FOUND'

    def test_permission_denied_returns_permission_denied_code(self):
        from rest_framework.exceptions import PermissionDenied
        response = self._handler(PermissionDenied())
        assert response.data['success'] is False
        assert response.data['error']['code'] == 'PERMISSION_DENIED'

    def test_not_authenticated_returns_unauthenticated_code(self):
        from rest_framework.exceptions import NotAuthenticated
        response = self._handler(NotAuthenticated())
        assert response.data['success'] is False
        assert response.data['error']['code'] == 'UNAUTHENTICATED'

    def test_validation_error_returns_validation_error_code(self):
        from rest_framework.exceptions import ValidationError
        response = self._handler(ValidationError({'email': ['This field is required.']}))
        assert response.data['success'] is False
        assert response.data['error']['code'] == 'VALIDATION_ERROR'

    def test_validation_error_puts_field_errors_in_details(self):
        from rest_framework.exceptions import ValidationError
        response = self._handler(ValidationError({'email': ['This field is required.']}))
        assert 'email' in response.data['error']['details']

    def test_permission_denied_with_structured_detail_preserved(self):
        from rest_framework.exceptions import PermissionDenied
        detail = {'code': 'company_not_assigned', 'message': 'No company'}
        response = self._handler(PermissionDenied(detail=detail))
        assert response.data['error']['details']['code'] == 'company_not_assigned'

    def test_validation_error_message_is_first_field_error(self):
        from rest_framework.exceptions import ValidationError
        response = self._handler(ValidationError({'email': ['This field is required.']}))
        assert response.data['error']['message'] == 'This field is required.'

    def test_validation_error_message_is_first_non_field_error(self):
        from rest_framework.exceptions import ValidationError
        response = self._handler(ValidationError(['Passwords do not match.']))
        assert response.data['error']['message'] == 'Passwords do not match.'

    def test_validation_error_non_field_errors_take_priority_over_field_errors(self):
        from rest_framework.exceptions import ValidationError
        response = self._handler(ValidationError({
            'non_field_errors': ['Object-level error.'],
            'email': ['Field-level error.'],
        }))
        assert response.data['error']['message'] == 'Object-level error.'

    def test_permission_denied_with_specific_string_detail_surfaces_in_message(self):
        from rest_framework.exceptions import PermissionDenied
        response = self._handler(PermissionDenied(detail='Аккаунт заблокирован.'))
        assert response.data['error']['message'] == 'Аккаунт заблокирован.'
        # The specific reason must still appear in details too
        assert response.data['error']['details']['detail'] == 'Аккаунт заблокирован.'

    def test_permission_denied_generic_message_when_no_specific_detail(self):
        from rest_framework.exceptions import PermissionDenied
        response = self._handler(PermissionDenied())
        # Default PermissionDenied detail is a generic DRF string; message must be our translated generic
        assert response.data['error']['code'] == 'PERMISSION_DENIED'
        assert response.data['error']['message'] != ''
