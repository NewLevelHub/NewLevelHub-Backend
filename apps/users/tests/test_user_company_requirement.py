"""company_id requirement for employee / company_admin (model + DB constraint)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError

from apps.companies.models import Company
from apps.users.models import User


def _postgres_has_user_company_check():
    if connection.vendor != 'postgresql':
        return False
    with connection.cursor() as c:
        c.execute(
            'SELECT 1 FROM pg_constraint WHERE conname = %s',
            ['users_employee_admin_requires_company'],
        )
        return c.fetchone() is not None


@pytest.mark.django_db
class TestUserCompanyRequirement:
    def test_full_clean_rejects_employee_without_company(self):
        user = User(
            email='orphan_emp@test.com',
            first_name='O',
            last_name='E',
            role='employee',
            company=None,
        )
        with pytest.raises(ValidationError) as exc:
            user.full_clean()
        assert 'company' in exc.value.error_dict

    def test_postgres_enforces_check_constraint_when_present(self, db):
        """
        After ``migrate`` (users.0004_user_company_required_roles), PostgreSQL
        must reject clearing company_id for employee. If this fails, recreate
        the test DB (e.g. pytest --create-db) so migrations apply cleanly.
        """
        if not _postgres_has_user_company_check():
            pytest.skip(
                'Constraint users_employee_admin_requires_company not in DB — '
                'run migrations or pytest --create-db to refresh the test database.'
            )
        company = Company.objects.create(name='CHK Co', plan='basic')
        user = User.objects.create_user(
            email='chk_emp@test.com',
            password='pass12345',
            first_name='C',
            last_name='H',
            role='employee',
            company=company,
        )
        with pytest.raises(IntegrityError):
            with connection.cursor() as c:
                c.execute('UPDATE users SET company_id = NULL WHERE id = %s', (user.pk,))

    def test_create_employee_with_company_succeeds(self, db):
        company = Company.objects.create(name='Co', plan='basic')
        user = User.objects.create_user(
            email='good_emp@test.com',
            password='pass12345',
            first_name='G',
            last_name='D',
            role='employee',
            company=company,
        )
        assert user.company_id == company.id
