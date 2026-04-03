from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db import connection
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
import rest_framework.fields as fields


@extend_schema(
    tags=['System'],
    summary='Health check',
    responses={
        200: inline_serializer(
            name='HealthCheckResponse',
            fields={
                'status': fields.CharField(),
                'database': fields.CharField(),
            },
        ),
        503: OpenApiResponse(description='Service unavailable — database unreachable'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    healthy = db_ok
    return Response(
        {
            'status': 'healthy' if healthy else 'unhealthy',
            'database': 'connected' if db_ok else 'unavailable',
        },
        status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@extend_schema(
    tags=['System'],
    summary='Ping',
    responses={
        200: inline_serializer(
            name='PingResponse',
            fields={'message': fields.CharField()},
        ),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def ping(request):
    return Response({'message': 'pong'})
