from rest_framework import serializers
from apps.core.exceptions import raise_validation_error
from .models import Folder, File, FileShare, FolderPermission


class FolderSerializer(serializers.ModelSerializer):
    children_count = serializers.IntegerField(source='children.count', read_only=True)
    files_count = serializers.IntegerField(source='files.count', read_only=True)
    is_restricted = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = [
            'id', 'name', 'scope', 'parent', 'owner',
            'children_count', 'files_count', 'is_restricted',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']

    def get_is_restricted(self, obj):
        if hasattr(obj, 'perm_exists'):
            return obj.perm_exists
        return obj.permissions.exists()


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
            'comment',
            'created_at',
        ]
        read_only_fields = ['id', 'shared_by', 'created_at']

    def to_internal_value(self, data):
        normalized_data = data.copy()
        if normalized_data.get('file') in (None, '') and normalized_data.get('file_id') not in (None, ''):
            normalized_data['file'] = normalized_data.get('file_id')
        if (
            normalized_data.get('shared_with') in (None, '')
            and normalized_data.get('shared_with_user_id') not in (None, '')
        ):
            normalized_data['shared_with'] = normalized_data.get('shared_with_user_id')
        return super().to_internal_value(normalized_data)

    def validate(self, attrs):
        request = self.context.get('request')
        if request is None or request.user.is_anonymous:
            return attrs

        file_obj = attrs.get('file') or getattr(self.instance, 'file', None)
        shared_with = attrs.get('shared_with') or getattr(self.instance, 'shared_with', None)

        if file_obj and self.instance is None and file_obj.owner_id != request.user.id:
            raise_validation_error('file', 'storage.share_owner_only')

        if shared_with and shared_with.company_id != request.user.company_id:
            raise_validation_error('shared_with', 'storage.share_wrong_company')

        if file_obj and shared_with:
            # Personal files have company_id=None; use the owner's company for the check.
            file_company_id = file_obj.company_id if file_obj.company_id is not None else request.user.company_id
            if file_company_id != shared_with.company_id:
                raise_validation_error('shared_with', 'storage.share_cross_company')

        return attrs


class FolderPermissionSerializer(serializers.ModelSerializer):
    granted_by_name = serializers.CharField(source='granted_by.full_name', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        model = FolderPermission
        fields = ['id', 'folder', 'user', 'user_name', 'role',
                  'permission', 'granted_by', 'granted_by_name', 'created_at']
        read_only_fields = ['id', 'folder', 'granted_by', 'created_at']

    def to_internal_value(self, data):
        normalized = data.copy()
        if normalized.get('user') in (None, '') and normalized.get('user_id') not in (None, ''):
            normalized['user'] = normalized.get('user_id')
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        user = attrs.get('user')
        role = attrs.get('role') or None
        attrs['role'] = role

        if self.instance is None:
            # CREATE: user or role must be provided, and only one of them.
            if user is None and role is None:
                raise serializers.ValidationError(
                    [{'_i18n': True, 'key': 'storage.permission_user_or_role_required', 'params': {}}]
                )
            if user is not None and role is not None:
                raise serializers.ValidationError(
                    [{'_i18n': True, 'key': 'storage.permission_user_xor_role', 'params': {}}]
                )
        else:
            # UPDATE (PATCH): only validate XOR if both are explicitly supplied.
            if user is not None and role is not None:
                raise serializers.ValidationError(
                    [{'_i18n': True, 'key': 'storage.permission_user_xor_role', 'params': {}}]
                )

        if user is not None:
            folder = self.context.get('folder') or getattr(self.instance, 'folder', None)
            if folder and folder.company_id and user.company_id != folder.company_id:
                raise_validation_error('user', 'storage.permission_wrong_company')
        return attrs


class TrashItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    item_type = serializers.CharField()
    deleted_at = serializers.DateTimeField()
    scope = serializers.CharField()
    file_size = serializers.IntegerField(allow_null=True)
    content_type = serializers.CharField(allow_null=True)
    files_count = serializers.IntegerField(allow_null=True)


class PersonalStorageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    file_count = serializers.IntegerField()
    limit_bytes = serializers.IntegerField(allow_null=True)


class CompanyStorageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    limit_bytes = serializers.IntegerField()
    file_count = serializers.IntegerField()


class StorageUsageSerializer(serializers.Serializer):
    personal = PersonalStorageSerializer()
    company = CompanyStorageSerializer(allow_null=True)
