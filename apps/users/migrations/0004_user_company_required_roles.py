from django.db import migrations, models


def assert_no_employee_or_admin_without_company(apps, schema_editor):
    """
    Block schema upgrade until DB is consistent.

    Audit query (PostgreSQL):
        SELECT id, email, role FROM users
        WHERE role IN ('employee', 'company_admin') AND company_id IS NULL;
    """
    User = apps.get_model('users', 'User')
    rows = list(
        User.objects.filter(role__in=('employee', 'company_admin'), company_id__isnull=True).values_list(
            'id', 'email', 'role'
        )
    )
    if not rows:
        return
    lines = '\n'.join(f'  id={pk} email={email} role={role}' for pk, email, role in rows)
    raise RuntimeError(
        'Cannot apply migration: employee/company_admin users exist without company_id.\n'
        'Assign a company (or change role) for each row, then run migrate again.\n'
        'Rows:\n'
        f'{lines}\n'
        "SQL: SELECT id, email, role FROM users "
        "WHERE role IN ('employee', 'company_admin') AND company_id IS NULL;"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_add_reception_role'),
    ]

    operations = [
        migrations.RunPython(assert_no_employee_or_admin_without_company, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(role__in=['employee', 'company_admin'])
                    | models.Q(company_id__isnull=False)
                ),
                name='users_employee_admin_requires_company',
            ),
        ),
    ]
