"""Schema post-processing helpers for drf-spectacular."""

from __future__ import annotations

from typing import Any


_TAG_ALIASES = {
    'access': 'Access',
    'analytics': 'Analytics',
    'auth': 'Auth',
    'bookings': 'Bookings',
    'companies': 'Companies',
    'crm': 'CRM',
    'hr': 'HR',
    'notifications': 'Notifications',
    'resources': 'Resources',
    'services': 'Services',
    'storage': 'Storage',
    'system': 'System',
    'users': 'Users',
}


def normalize_operation_tags(result: dict[str, Any], generator: Any, request: Any, public: bool) -> dict[str, Any]:
    """Map auto-generated lowercase tags to canonical names."""
    del generator, request, public

    for path_item in result.get('paths', {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue

            operation_tags = operation.get('tags')
            if not operation_tags:
                continue

            normalized_tags = []
            for tag in operation_tags:
                normalized_tag = _TAG_ALIASES.get(tag, tag)
                if normalized_tag not in normalized_tags:
                    normalized_tags.append(normalized_tag)
            operation['tags'] = normalized_tags

    return result
