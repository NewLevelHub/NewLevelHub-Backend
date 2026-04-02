import logging

from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        payload = {
            'error': True,
            'status_code': response.status_code,
            'detail': response.data,
        }
        response.data = payload
    else:
        logger.exception('Unhandled exception', exc_info=exc)
        response = Response(
            {
                'error': True,
                'status_code': status.HTTP_500_INTERNAL_SERVER_ERROR,
                'detail': 'Internal server error',
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response
