import json
import os

from django.conf import settings

_LOCALE_CACHE = {}


def _load_locale(lang: str) -> dict:
    if lang not in _LOCALE_CACHE:
        path = os.path.join(settings.BASE_DIR, 'locale', f'{lang}.json')
        if not os.path.exists(path):
            path = os.path.join(settings.BASE_DIR, 'locale', 'ru.json')
        with open(path, 'r', encoding='utf-8') as f:
            _LOCALE_CACHE[lang] = json.load(f)
    return _LOCALE_CACHE[lang]


def get_lang(request) -> str:
    """Detect language: ?lang= → Accept-Language header → default 'ru'."""
    lang = None
    if hasattr(request, 'query_params'):
        lang = request.query_params.get('lang')
    if not lang:
        lang = request.GET.get('lang') if hasattr(request, 'GET') else None
    if lang in ('ru', 'en'):
        return lang
    accept = request.META.get('HTTP_ACCEPT_LANGUAGE', '') if hasattr(request, 'META') else ''
    if accept.startswith('en'):
        return 'en'
    return 'ru'


def translate(key: str, lang: str = 'ru', **params) -> str:
    """Return localized message for key, with optional parameter interpolation."""
    messages = _load_locale(lang)
    template = messages.get(key, messages.get('common.server_error', key))
    if params:
        try:
            return template.format(**params)
        except KeyError:
            return template
    return template
