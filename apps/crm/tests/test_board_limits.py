import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board
from apps.notifications.models import Notification
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='CRM Co', plan='basic', max_boards=2)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@crm.co',
        password='pass',
        first_name='Admin',
        last_name='CRM',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


def _url():
    return '/api/v1/crm/boards/'


@pytest.mark.django_db
class TestBoardLimit:
    def test_create_board_limit_reached_returns_400(self, api_client, company_admin, company):
        Board.objects.create(company=company, name='B1', created_by=company_admin)
        Board.objects.create(company=company, name='B2', created_by=company_admin)

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(_url(), {'name': 'B3'}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Board limit reached'

    def test_create_board_at_80_percent_creates_admin_notification(self, api_client, company_admin, company):
        company.max_boards = 5
        company.save(update_fields=['max_boards'])
        for idx in range(3):
            Board.objects.create(company=company, name=f'B{idx}', created_by=company_admin)

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(_url(), {'name': 'B4'}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Board usage reached 80% (4/5 boards).',
        ).exists()

    def test_create_board_at_95_percent_creates_admin_notification(self, api_client, company_admin, company):
        company.max_boards = 20
        company.save(update_fields=['max_boards'])
        for idx in range(18):
            Board.objects.create(company=company, name=f'B95-{idx}', created_by=company_admin)

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(_url(), {'name': 'B95-19'}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Board usage reached 95% (19/20 boards).',
        ).exists()
