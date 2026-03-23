from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db import connection
from datetime import datetime, timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema


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


@extend_schema(
    tags=['System'],
    summary='Build Info',
    description='Smoke endpoint with static service build information',
    responses={
        200: OpenApiResponse(
            description='Static build information',
            response={
                'type': 'object',
                'properties': {
                    'service': {'type': 'string', 'example': 'newlevelhub-backend'},
                    'version': {'type': 'string', 'example': '1.0.0'},
                    'status': {'type': 'string', 'example': 'ok'}
                }
            }
        )
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def build_info(request):
    """
    Minimal endpoint for smoke checking API availability.
    """
    return Response(
        {
            'service': 'newlevelhub-backend',
            'version': '1.0.0',
            'status': 'ok',
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    tags=['System'],
    summary='Ping',
    description='Minimal endpoint for API smoke checks',
    responses={
        200: OpenApiResponse(
            description='Pong response',
            response={
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'pong'}
                }
            }
        )
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def ping(request):
    """
    Minimal endpoint for smoke checking API availability.
    """
    return Response({'message': 'pong'}, status=status.HTTP_200_OK)


@extend_schema(
    tags=['System'],
    summary='Status Text',
    description='Returns static service status text',
    responses={
        200: OpenApiResponse(
            description='Service status text',
            response={
                'type': 'object',
                'properties': {
                    'status_text': {'type': 'string', 'example': 'service is up'}
                }
            }
        )
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def status_text(request):
    """
    Returns static service status text.
    """
    return Response({'status_text': 'service is up'}, status=status.HTTP_200_OK)


@extend_schema(
    tags=['System'],
    summary='Server Time',
    description='Current server time in UTC ISO 8601 format',
    responses={
        200: OpenApiResponse(
            description='Current UTC server time',
            response={
                'type': 'object',
                'properties': {
                    'utc_time': {'type': 'string', 'example': '2026-03-23T12:34:56.789012Z'}
                }
            }
        )
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def server_time(request):
    """
    Returns current server time in UTC ISO 8601 format with Z suffix.
    """
    utc_time = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return Response({'utc_time': utc_time}, status=status.HTTP_200_OK)


@extend_schema(
    tags=['System'],
    summary='Echo',
    description='Returns provided text query parameter as-is',
    parameters=[
        OpenApiParameter(
            name='text',
            type=str,
            location=OpenApiParameter.QUERY,
            required=False,
            description='Text value to echo back',
        )
    ],
    responses={
        200: OpenApiResponse(
            description='Echo payload',
            response={
                'type': 'object',
                'properties': {
                    'echo': {'type': 'string', 'example': 'hello'}
                }
            }
        )
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def echo(request):
    """
    Returns text query parameter under "echo" key.
    """
    return Response({'echo': request.query_params.get('text', '')}, status=status.HTTP_200_OK)
