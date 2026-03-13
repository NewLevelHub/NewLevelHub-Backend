from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db import connection
from drf_spectacular.utils import extend_schema, OpenApiResponse


@extend_schema(
    tags=['Health'],
    summary='Health Check',
    description='Проверка состояния сервера и подключения к базе данных',
    responses={
        200: OpenApiResponse(
            description='Server is healthy',
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'healthy'},
                    'database': {'type': 'string', 'example': 'connected'},
                    'message': {'type': 'string', 'example': 'New Level Hub Backend is running'}
                }
            }
        ),
        503: OpenApiResponse(description='Service unavailable')
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint для проверки статуса сервера.
    Доступен без авторизации.
    """
    try:
        # Check database connection
        connection.ensure_connection()
        db_status = 'connected'
        response_status = status.HTTP_200_OK
    except Exception as e:
        db_status = f'error: {str(e)}'
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE

    return Response({
        'status': 'healthy' if response_status == 200 else 'unhealthy',
        'database': db_status,
        'message': 'New Level Hub Backend is running'
    }, status=response_status)
