from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('folders', views.FolderViewSet, basename='folder')
router.register('files', views.FileViewSet, basename='file')
router.register('shares', views.FileShareViewSet, basename='file-share')
router.register('folder-permissions', views.FolderPermissionViewSet, basename='folder-permission')

urlpatterns = [
    path('', include(router.urls)),
    path('usage/', views.storage_usage, name='storage-usage'),
    path('trash/', views.TrashListView.as_view(), name='storage-trash'),
    path('trash/restore/', views.TrashBulkRestoreView.as_view(), name='storage-trash-bulk-restore'),
    path('trash/delete/', views.TrashBulkDeleteView.as_view(), name='storage-trash-bulk-delete'),
]
