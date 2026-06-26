from django.db.models import Q
from rest_framework import serializers
from apps.core.exceptions import raise_validation_error
from apps.users.serializers import UserBriefSerializer
from .models import Folder, File, FileShare, FolderPermission


class FolderSerializer(serializers.ModelSerializer):
    children_count = serializers.IntegerField(source='children.count', read_only=True)
    files_count = serializers.IntegerField(source='files.count', read_only=True)
    is_restricted = serializers.SerializerMethodField()
    user_permission = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = [
            'id', 'name', 'scope', 'parent', 'owner',
            'children_count', 'files_count', 'is_restricted', 'user_permission',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']

    def get_is_restricted(self, obj):
        if hasattr(obj, 'perm_exists'):
            return obj.perm_exists
        return obj.permissions.exists()

    def get_user_permission(self, obj):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return None
        user = request.user
        if user.role in ('superadmin', 'company_admin'):
            return None
        is_restricted = obj.perm_exists if hasattr(obj, 'perm_exists') else obj.permissions.exists()
        if not is_restricted:
            return None
        # Use DB annotations when available (avoids N+1 in list views).
        if hasattr(obj, 'user_perm_ann'):
            return obj.user_perm_ann or getattr(obj, 'role_perm_ann', None)
        # Fallback: one query that fetches both user- and role-based records.
        perms = list(obj.permissions.filter(Q(user=user) | Q(role=user.role)))
        user_perm = next((p for p in perms if p.user_id == user.id), None)
        if user_perm:
            return user_perm.permission
        role_perm = next((p for p in perms if p.role == user.role), None)
        return role_perm.permission if role_perm else None


class FileSerializer(serializers.ModelSerializer):
    owner = UserBriefSerializer(read_only=True)
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
            'folder', 'owner', 'company',
            'created_at', 'updated_at',
            'size', 'mime_type', 'download_url', 'folder_id',
        ]
        read_only_fields = ['id', 'owner', 'company', 'file_size', 'content_type', 'created_at', 'updated_at']

    def get_download_url(self, obj):
        request = self.context.get('request')
        if not request:
            return f'/api/v1/storage/files/{obj.id}/download/'
        return request.build_absolute_uri(f'/api/v1/storage/files/{obj.id}/download/')


class FileShareSerializer(serializers.ModelSerializer):
    # `shared_with` accepts a PK integer on write; to_representation nests the user data.
    # `shared_by` is read-only (set by the view via perform_create).
    file_name = serializers.CharField(source='file.name', read_only=True)
    file_owner_id = serializers.IntegerField(source='file.owner_id', read_only=True)
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
            'shared_by',
            'file_name',
            'file_owner_id',
            'permission',
            'comment',
            'created_at',
        ]
        read_only_fields = ['id', 'shared_by', 'created_at']

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if instance.shared_with_id is not None:
            ret['shared_with'] = UserBriefSerializer(instance.shared_with, context=self.context).data
        if instance.shared_by_id is not None:
            ret['shared_by'] = UserBriefSerializer(instance.shared_by, context=self.context).data
        return ret

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
    granted_by = UserBriefSerializer(read_only=True)
    # `user` is a writable PK field on input; to_representation returns nested UserBriefSerializer data.
    # The queryset is set lazily in __init__ to avoid a circular import at module load time.
    user = serializers.PrimaryKeyRelatedField(read_only=True, required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.users.models import User as UserModel
        self.fields['user'] = serializers.PrimaryKeyRelatedField(
            queryset=UserModel.objects.all(),
            required=False,
            allow_null=True,
        )

    class Meta:
        model = FolderPermission
        fields = ['id', 'folder', 'user', 'role',
                  'permission', 'granted_by', 'created_at']
        read_only_fields = ['id', 'folder', 'granted_by', 'created_at']

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if instance.user_id is not None:
            ret['user'] = UserBriefSerializer(instance.user, context=self.context).data
        return ret

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


class StorageBreakdownSerializer(serializers.Serializer):
    document = serializers.IntegerField()
    image = serializers.IntegerField()
    archive = serializers.IntegerField()
    media = serializers.IntegerField()
    other = serializers.IntegerField()


class PersonalStorageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    file_count = serializers.IntegerField()
    limit_bytes = serializers.IntegerField(allow_null=True)
    trash_bytes = serializers.IntegerField()
    breakdown = StorageBreakdownSerializer()


class CompanyStorageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    limit_bytes = serializers.IntegerField()
    file_count = serializers.IntegerField()
    trash_bytes = serializers.IntegerField()
    breakdown = StorageBreakdownSerializer()


class StorageUsageSerializer(serializers.Serializer):
    personal = PersonalStorageSerializer()
    company = CompanyStorageSerializer(allow_null=True)
