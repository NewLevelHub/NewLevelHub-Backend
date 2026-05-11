from django.db import migrations


def backfill_archived_consistency(apps, schema_editor):
    cursor = schema_editor.connection.cursor()

    # Case 1: soft-deleted but not archived — set is_archived=TRUE to match the
    # current invariant (archive action sets both flags).
    cursor.execute(
        "UPDATE crm_tasks SET is_archived = TRUE WHERE is_deleted = TRUE AND is_archived = FALSE"
    )

    # Case 2: archived but not soft-deleted — these rows were written via the old
    # PATCH serializer path before is_archived became read-only.  Stamp them with
    # is_deleted=TRUE and deleted_at=now() so SoftDeleteManager filters them out.
    cursor.execute(
        "UPDATE crm_tasks SET is_deleted = TRUE, deleted_at = NOW() "
        "WHERE is_archived = TRUE AND is_deleted = FALSE"
    )


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0004_add_mime_type_storage_file_to_attachment'),
    ]

    operations = [
        migrations.RunPython(backfill_archived_consistency, reverse_code=reverse_backfill),
    ]
