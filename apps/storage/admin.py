from django.contrib import admin
from .models import Folder, File, FileShare


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ['name', 'scope', 'owner', 'company', 'parent', 'created_at']
    list_filter = ['scope']


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ['name', 'file_size', 'content_type', 'owner', 'company', 'folder', 'created_at']
    search_fields = ['name']


@admin.register(FileShare)
class FileShareAdmin(admin.ModelAdmin):
    list_display = ['file', 'shared_with', 'shared_by', 'permission', 'created_at']
