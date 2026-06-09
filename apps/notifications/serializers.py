from rest_framework import serializers
from django.utils import timezone
from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    """
    Serializes a Notification instance.

    Field mapping (API name → model field):
      type     → notification_type
      message  → body
      link     → url
    """
    type = serializers.CharField(source='notification_type', read_only=True)
    message = serializers.CharField(source='body', read_only=True)
    link = serializers.CharField(source='url', read_only=True, allow_null=True)

    class Meta:
        model = Notification
        fields = ['id', 'type', 'title', 'message', 'link', 'is_read', 'created_at']
        read_only_fields = ['id', 'type', 'title', 'message', 'link', 'created_at']


# Maps each per-type API key → (in_app model field, email model field)
# Types that share a group use the same underlying boolean fields.
NOTIFICATION_TYPE_FIELD_MAP = {
    'booking_confirmed': ('booking_confirmed_in_app', 'booking_confirmed_email'),
    'booking_reminder': ('booking_reminder_in_app', 'booking_reminder_email'),
    'booking_cancelled': ('booking_cancelled_in_app', 'booking_cancelled_email'),
    'booking_completed': ('booking_completed_in_app', 'booking_completed_email'),
    'task_assigned': ('task_assigned_in_app', 'task_assigned_email'),
    'task_moved': ('task_moved_in_app', 'task_moved_email'),
    'task_comment': ('task_comment_in_app', 'task_comment_email'),
    'task_deadline': ('task_deadline_in_app', 'task_deadline_email'),
    'guest_validated': ('guest_validated_in_app', 'guest_validated_email'),
    'guest_pass_expiring': ('guest_pass_expiring_in_app', 'guest_pass_expiring_email'),
    'service_request_update': ('service_request_update_in_app', 'service_request_update_email'),
    'announcement': ('announcement_in_app', 'announcement_email'),
    'invitation': ('invitation_in_app', 'invitation_email'),
    'leave_review': ('leave_review_in_app', 'leave_review_email'),
    'system': ('system_in_app', 'system_email'),
}

# Notification types that actually send email via send_notification_email.
# Only these types expose an email preference toggle in the API response.
# Derived from cross-referencing _PREF_FIELD_MAP in tasks.py with actual
# call sites across the codebase (bookings, crm, hr, access, services).
EMAIL_ENABLED_TYPES = {
    'booking_confirmed',   # apps/bookings/serializers.py
    'booking_completed',   # apps/bookings/tasks.py auto_complete_bookings
    'task_assigned',       # apps/crm/views.py, apps/crm/tasks.py
    'task_deadline',       # apps/crm/tasks.py (also covers task_deadline_overdue via same pref field)
    'leave_review',        # apps/hr/views.py
    'guest_validated',     # apps/access/tasks.py
    'announcement',        # apps/notifications/tasks.py send_bulk_email → announcement_company type
                           # maps to announcement_email pref, which this key controls
}

# Notification types each role is allowed to see and configure.
# '__all__' means all keys from NOTIFICATION_TYPE_FIELD_MAP.
ROLE_NOTIFICATION_TYPES = {
    'superadmin': '__all__',
    'company_admin': [
        'booking_confirmed', 'booking_reminder', 'booking_cancelled', 'booking_completed',
        'task_assigned', 'task_moved', 'task_comment', 'task_deadline',
        'guest_validated', 'guest_pass_expiring',
        'service_request_update',
        'announcement',
        'invitation', 'leave_review',
        'system',
    ],
    'employee': [
        'booking_confirmed', 'booking_reminder', 'booking_cancelled', 'booking_completed',
        'task_assigned', 'task_moved', 'task_comment', 'task_deadline',
        'service_request_update',
        'announcement',
        'system',
    ],
    'guest': [
        'guest_validated',
        'guest_pass_expiring',
        'announcement',
        'system',
    ],
}


def get_allowed_types_for_role(role):
    """Return the set of notification type keys the given role may access."""
    allowed = ROLE_NOTIFICATION_TYPES.get(role, '__all__')
    if allowed == '__all__':
        return set(NOTIFICATION_TYPE_FIELD_MAP.keys())
    # Intersect with the actual map so stale role lists never cause KeyErrors.
    return set(allowed) & set(NOTIFICATION_TYPE_FIELD_MAP.keys())


class NotificationPreferenceDictSerializer(serializers.Serializer):
    """
    Returns/accepts notification preferences as a dict keyed by notification type.

    GET response shape:
        {
            "booking_confirmed": {"in_app": true, "email": true},
            ...
        }

    PATCH request shape (partial — only included keys are updated):
        {
            "booking_reminder": {"email": false},
            "task_assigned":    {"in_app": false, "email": false}
        }
    """

    def to_representation(self, instance):
        request = self.context.get('request')
        role = request.user.role if request and hasattr(request, 'user') else None
        allowed = get_allowed_types_for_role(role) if role else set(NOTIFICATION_TYPE_FIELD_MAP.keys())

        result = {
            'dnd_enabled': instance.dnd_enabled,
            'dnd_until': instance.dnd_until,
        }
        for ntype, (in_app_field, email_field) in NOTIFICATION_TYPE_FIELD_MAP.items():
            if ntype not in allowed:
                continue
            entry = {'in_app': getattr(instance, in_app_field)}
            if ntype in EMAIL_ENABLED_TYPES:
                entry['email'] = getattr(instance, email_field)
            result[ntype] = entry
        return result

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                {'non_field_errors': ['Expected a dict keyed by notification type.']}
            )

        # dnd_enabled / dnd_until are read-only here; use /do-not-disturb/ to change them.
        read_only_keys = {'dnd_enabled', 'dnd_until'}
        submitted_read_only = read_only_keys & set(data.keys())
        if submitted_read_only:
            raise serializers.ValidationError(
                {'non_field_errors': [
                    f"Fields {sorted(submitted_read_only)} are read-only here. "
                    "Use POST /api/v1/notifications/do-not-disturb/ to change DND settings."
                ]}
            )

        unknown_keys = set(data.keys()) - set(NOTIFICATION_TYPE_FIELD_MAP.keys())
        if unknown_keys:
            raise serializers.ValidationError(
                {'non_field_errors': [
                    f"Unknown notification type(s): {sorted(unknown_keys)}. "
                    f"Valid types: {sorted(NOTIFICATION_TYPE_FIELD_MAP.keys())}"
                ]}
            )

        # Role-based access: silently skip types the user's role cannot configure.
        request = self.context.get('request')
        role = request.user.role if request and hasattr(request, 'user') else None
        if role:
            allowed = get_allowed_types_for_role(role)
            data = {k: v for k, v in data.items() if k in allowed}

        errors = {}
        validated = {}
        for ntype, prefs in data.items():
            if not isinstance(prefs, dict):
                errors[ntype] = ['Expected a dict with "in_app" and/or "email" keys.']
                continue
            invalid_keys = set(prefs.keys()) - {'in_app', 'email'}
            if invalid_keys:
                errors[ntype] = [
                    f'Unknown keys: {sorted(invalid_keys)}. Only "in_app" and "email" are allowed.'
                ]
                continue
            entry = {}
            type_errors = {}
            for key in ('in_app', 'email'):
                if key == 'email' and ntype not in EMAIL_ENABLED_TYPES:
                    continue  # email preference has no effect for this type; skip silently
                if key in prefs:
                    val = prefs[key]
                    if not isinstance(val, bool):
                        type_errors[key] = ['Must be a boolean.']
                    else:
                        entry[key] = val
            if type_errors:
                errors[ntype] = type_errors
            else:
                validated[ntype] = entry

        if errors:
            raise serializers.ValidationError(errors)

        return validated

    def update(self, instance, validated_data):
        """
        Apply validated per-type preferences to the grouped model fields.
        Because multiple types may share a group field, we track which
        model fields to update and apply each change.
        """
        fields_to_save = set()
        for ntype, prefs in validated_data.items():
            in_app_field, email_field = NOTIFICATION_TYPE_FIELD_MAP[ntype]
            if 'in_app' in prefs:
                setattr(instance, in_app_field, prefs['in_app'])
                fields_to_save.add(in_app_field)
            if 'email' in prefs:
                setattr(instance, email_field, prefs['email'])
                fields_to_save.add(email_field)

        if fields_to_save:
            instance.save(update_fields=list(fields_to_save))
        return instance


class DNDSerializer(serializers.Serializer):
    """Serializer for Do-Not-Disturb settings."""
    enabled = serializers.BooleanField()
    until = serializers.DateTimeField(allow_null=True, required=False, default=None)

    def validate(self, attrs):
        enabled = attrs.get('enabled')
        until = attrs.get('until')

        if until is not None:
            if not enabled:
                # When disabling DND, ignore/clear any provided until value.
                attrs['until'] = None
            elif until <= timezone.now():
                raise serializers.ValidationError(
                    {'dnd_until': 'Must be a future datetime.'}
                )

        return attrs

    def update(self, instance, validated_data):
        instance.dnd_enabled = validated_data['enabled']
        instance.dnd_until = validated_data.get('until')
        instance.save(update_fields=['dnd_enabled', 'dnd_until'])
        return instance


class DNDResponseSerializer(serializers.Serializer):
    """Response shape for DND endpoint."""
    dnd_enabled = serializers.BooleanField()
    dnd_until = serializers.DateTimeField(allow_null=True)


class UnreadCountSerializer(serializers.Serializer):
    count = serializers.IntegerField()


# Legacy serializer kept for backward compatibility (used nowhere externally,
# but referenced in older migrations/tests if any).
class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'booking_confirmed_in_app', 'booking_confirmed_email',
            'booking_reminder_in_app', 'booking_reminder_email',
            'booking_cancelled_in_app', 'booking_cancelled_email',
            'task_assigned_in_app', 'task_assigned_email',
            'task_moved_in_app', 'task_moved_email',
            'task_comment_in_app', 'task_comment_email',
            'task_deadline_in_app', 'task_deadline_email',
            'guest_validated_in_app', 'guest_validated_email',
            'guest_pass_expiring_in_app', 'guest_pass_expiring_email',
            'service_request_update_in_app', 'service_request_update_email',
            'announcement_in_app', 'announcement_email',
            'invitation_in_app', 'invitation_email',
            'leave_review_in_app', 'leave_review_email',
            'system_in_app', 'system_email',
            'do_not_disturb',
            'dnd_enabled', 'dnd_until',
        ]
