from rest_framework import serializers
from .models import Folder, File, FileShare


class FolderSerializer(serializers.ModelSerializer):
    children_count = serializers.IntegerField(source='children.count', read_only=True)
    files_count = serializers.IntegerField(source='files.count', read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'name', 'scope', 'parent', 'children_count', 'files_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class FileSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)

    class Meta:
        model = File
        fields = [
            'id', 'name', 'file', 'file_size', 'content_type',
            'folder', 'owner', 'owner_name', 'company',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'company', 'file_size', 'content_type', 'created_at', 'updated_at']


class FileShareSerializer(serializers.ModelSerializer):
    shared_with_name = serializers.CharField(source='shared_with.full_name', read_only=True)

    class Meta:
        model = FileShare
        fields = ['id', 'file', 'shared_with', 'shared_with_name', 'shared_by', 'permission', 'created_at']
        read_only_fields = ['id', 'shared_by', 'created_at']


class StorageUsageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    limit_bytes = serializers.IntegerField()
    used_percent = serializers.FloatField()
