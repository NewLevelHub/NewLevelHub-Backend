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
    uploaded_by = serializers.CharField(source='owner.full_name', read_only=True)
    size = serializers.IntegerField(source='file_size', read_only=True)
    mime_type = serializers.CharField(source='content_type', read_only=True)
    download_url = serializers.SerializerMethodField()
    folder_id = serializers.PrimaryKeyRelatedField(
        source='folder',
        queryset=Folder.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = File
        fields = [
            'id', 'name', 'file', 'file_size', 'content_type',
            'folder', 'owner', 'owner_name', 'company',
            'created_at', 'updated_at',
            'size', 'mime_type', 'download_url', 'uploaded_by', 'folder_id',
        ]
        read_only_fields = ['id', 'owner', 'company', 'file_size', 'content_type', 'created_at', 'updated_at']

    def get_download_url(self, obj):
        request = self.context.get('request')
        if not request:
            return f'/api/v1/storage/files/{obj.id}/download/'
        return request.build_absolute_uri(f'/api/v1/storage/files/{obj.id}/download/')


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
