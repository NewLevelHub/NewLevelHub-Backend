import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Label, Task
from apps.users.models import User

LABELS_URL = '/api/v1/crm/labels/'


def label_url(pk):
    return f'/api/v1/crm/labels/{pk}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Company A', plan='standard', max_boards=5)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Company B', plan='standard', max_boards=5)


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='admin_a@test.com',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='employee_a@test.com',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def admin_b(db, company_b):
    return User.objects.create_user(
        email='admin_b@test.com',
        password='pass',
        first_name='Admin',
        last_name='B',
        role='company_admin',
        company=company_b,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        company=None,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def label_a(db, company_a):
    return Label.objects.create(company=company_a, name='Bug', color='#ff0000')


@pytest.fixture
def label_b(db, company_b):
    return Label.objects.create(company=company_b, name='Feature', color='#00ff00')


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelAuthentication:
    def test_unauthenticated_list_returns_401(self, api_client):
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_create_returns_401(self, api_client):
        response = api_client.post(LABELS_URL, {'name': 'X', 'color': '#aabbcc'}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_list_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_create_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.post(LABELS_URL, {'name': 'X', 'color': '#aabbcc'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# List (GET)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelList:
    def test_employee_sees_own_company_labels(self, api_client, employee_a, label_a, label_b):
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert label_a.id in ids
        assert label_b.id not in ids

    def test_admin_sees_own_company_labels(self, api_client, admin_a, label_a, label_b):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert label_a.id in ids
        assert label_b.id not in ids

    def test_other_company_cannot_see_labels(self, api_client, admin_b, label_a):
        api_client.force_authenticate(user=admin_b)
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert label_a.id not in ids

    def test_superadmin_sees_all_labels(self, api_client, superadmin, label_a, label_b):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LABELS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert label_a.id in ids
        assert label_b.id in ids


# ---------------------------------------------------------------------------
# Create (POST)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelCreate:
    def test_employee_creates_label(self, api_client, employee_a, company_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'Urgent', 'color': '#FF5733'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == 'Urgent'
        assert response.data['color'] == '#ff5733'  # normalized to lowercase
        assert Label.objects.filter(name='Urgent', company=company_a).exists()

    def test_admin_creates_label(self, api_client, admin_a, company_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(LABELS_URL, {'name': 'Priority', 'color': '#00aaff'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert Label.objects.filter(name='Priority', company=company_a).exists()

    def test_create_label_auto_assigns_company(self, api_client, employee_a, company_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'Auto', 'color': '#123456'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        label = Label.objects.get(pk=response.data['id'])
        assert label.company_id == company_a.id

    def test_create_label_with_default_color(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'NoColor'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['color'] == '#6366f1'

    def test_duplicate_name_same_company_returns_400(self, api_client, employee_a, label_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'Bug', 'color': '#000000'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_duplicate_name_case_insensitive(self, api_client, employee_a, label_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'bug', 'color': '#000000'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_same_name_different_company_allowed(self, api_client, admin_b, label_a):
        """Two companies can have labels with the same name."""
        api_client.force_authenticate(user=admin_b)
        response = api_client.post(LABELS_URL, {'name': 'Bug', 'color': '#000000'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_invalid_color_returns_400(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'Bad', 'color': 'red'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_short_hex_color_returns_400(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'name': 'Bad', 'color': '#fff'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_name_returns_400(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(LABELS_URL, {'color': '#aabbcc'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelUpdate:
    def test_admin_updates_label_name(self, api_client, admin_a, label_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(label_url(label_a.id), {'name': 'Critical'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        label_a.refresh_from_db()
        assert label_a.name == 'Critical'

    def test_admin_updates_label_color(self, api_client, admin_a, label_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(label_url(label_a.id), {'color': '#00FF00'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        label_a.refresh_from_db()
        assert label_a.color == '#00ff00'

    def test_employee_cannot_update_label(self, api_client, employee_a, label_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.patch(label_url(label_a.id), {'name': 'Hacked'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_other_company_admin_cannot_update(self, api_client, admin_b, label_a):
        api_client.force_authenticate(user=admin_b)
        response = api_client.patch(label_url(label_a.id), {'name': 'Stolen'}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_name_to_existing_name_returns_400(self, api_client, admin_a, label_a, company_a):
        Label.objects.create(company=company_a, name='Duplicate', color='#111111')
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(label_url(label_a.id), {'name': 'Duplicate'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_same_name_on_self_is_ok(self, api_client, admin_a, label_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(label_url(label_a.id), {'name': 'Bug'}, format='json')
        assert response.status_code == status.HTTP_200_OK

    def test_superadmin_can_update_any_label(self, api_client, superadmin, label_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.patch(label_url(label_a.id), {'name': 'SuperEdit'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        label_a.refresh_from_db()
        assert label_a.name == 'SuperEdit'

    def test_invalid_color_on_update_returns_400(self, api_client, admin_a, label_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(label_url(label_a.id), {'color': 'notahex'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Delete (DELETE)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelDelete:
    def test_admin_deletes_label(self, api_client, admin_a, label_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.delete(label_url(label_a.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Label.objects.filter(pk=label_a.id).exists()

    def test_employee_cannot_delete_label(self, api_client, employee_a, label_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.delete(label_url(label_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Label.objects.filter(pk=label_a.id).exists()

    def test_other_company_admin_cannot_delete(self, api_client, admin_b, label_a):
        api_client.force_authenticate(user=admin_b)
        response = api_client.delete(label_url(label_a.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_label_removes_m2m_from_tasks(self, api_client, admin_a, label_a, company_a):
        board = Board.objects.create(company=company_a, name='B', created_by=admin_a)
        col = Column.objects.create(board=board, name='Col', position=0)
        task = Task.objects.create(column=col, title='Task', created_by=admin_a)
        task.labels.add(label_a)
        assert label_a in task.labels.all()

        api_client.force_authenticate(user=admin_a)
        response = api_client.delete(label_url(label_a.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT

        task.refresh_from_db()
        assert task.labels.count() == 0

    def test_superadmin_can_delete_any_label(self, api_client, superadmin, label_b):
        api_client.force_authenticate(user=superadmin)
        response = api_client.delete(label_url(label_b.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Label.objects.filter(pk=label_b.id).exists()
