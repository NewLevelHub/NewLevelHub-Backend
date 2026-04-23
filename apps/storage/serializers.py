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
    shared_by_name = serializers.CharField(source='shared_by.full_name', read_only=True)
    file_name = serializers.CharField(source='file.name', read_only=True)
    file_owner_id = serializers.IntegerField(source='file.owner_id', read_only=True)
    file_owner_name = serializers.CharField(source='file.owner.full_name', read_only=True)
    file_id = serializers.IntegerField(read_only=True)
    shared_with_user_id = serializers.IntegerField(source='shared_with_id', read_only=True)

    class Meta:
        model = FileShare
        fields = [
            'id',
            'file',
            'file_id',
            'shared_with',
            'shared_with_user_id',
            'shared_with_name',
            'shared_by',
            'shared_by_name',
            'file_name',
            'file_owner_id',
            'file_owner_name',
            'permission',
            'created_at',
        ]
        read_only_fields = ['id', 'shared_by', 'created_at']

    def to_internal_value(self, data):
        normalized_data = data.copy()
        if normalized_data.get('file') in (None, '') and normalized_data.get('file_id') not in (None, ''):
            normalized_data['file'] = normalized_data.get('file_id')
        if normalized_data.get('shared_with') in (None, '') and normalized_data.get('shared_with_user_id') not in (None, ''):
            normalized_data['shared_with'] = normalized_data.get('shared_with_user_id')
        return super().to_internal_value(normalized_data)

    def validate(self, attrs):
        request = self.context.get('request')
        if request is None or request.user.is_anonymous:
            return attrs

        file_obj = attrs.get('file') or getattr(self.instance, 'file', None)
        shared_with = attrs.get('shared_with') or getattr(self.instance, 'shared_with', None)

        if file_obj and self.instance is None and file_obj.owner_id != request.user.id:
            raise serializers.ValidationError({'file': 'Only the file owner can share this file.'})

        if shared_with and shared_with.company_id != request.user.company_id:
            raise serializers.ValidationError({'shared_with': 'User must belong to your company.'})

        if file_obj and shared_with and file_obj.company_id != shared_with.company_id:
            raise serializers.ValidationError({'shared_with': 'User must belong to the same company as file owner.'})

        return attrs


class StorageUsageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    limit_bytes = serializers.IntegerField()
    used_percent = serializers.FloatField()
