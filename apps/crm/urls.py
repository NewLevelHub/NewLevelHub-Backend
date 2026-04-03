from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('boards', views.BoardViewSet, basename='board')
router.register('tasks', views.TaskViewSet, basename='task')
router.register('labels', views.LabelViewSet, basename='label')

urlpatterns = [
    path('', include(router.urls)),
    path(
        'boards/<int:board_pk>/columns/',
        views.ColumnViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='board-columns',
    ),
    path(
        'boards/<int:board_pk>/columns/<int:pk>/',
        views.ColumnViewSet.as_view({'patch': 'partial_update', 'delete': 'destroy'}),
        name='board-column-detail',
    ),
    path(
        'tasks/<int:task_pk>/comments/',
        views.CommentViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='task-comments',
    ),
    path(
        'tasks/<int:task_pk>/comments/<int:pk>/',
        views.CommentViewSet.as_view({'delete': 'destroy'}),
        name='task-comment-detail',
    ),
]
