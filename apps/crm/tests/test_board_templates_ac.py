"""
Acceptance tests for CRM board templates feature.

AC1: GET /crm/board-templates/ → 200, list of 4 templates
AC2: each template contains id, name, columns (non-empty list)
AC3: POST /crm/boards/ without template_id → 3 columns created (basic)
AC4: POST /crm/boards/ with template_id=sales → 4 columns
AC5: POST /crm/boards/ with template_id=recruitment → 5 columns
AC6: POST /crm/boards/ with template_id=project → 4 columns
AC7: POST /crm/boards/ with template_id=unknown → fallback to basic (3 columns), NOT 400
AC8: GET /crm/board-templates/ with Accept-Language: en → names in English
AC9: guest → GET /crm/board-templates/ → 403
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Column
from apps.users.models import User

BOARDS_URL = '/api/v1/crm/boards/'
TEMPLATES_URL = '/api/v1/crm/board-templates/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Test Company', plan='standard', max_boards=10)


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Admin',
        last_name='Test',
        role='company_admin',
        company=company,
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
        is_email_verified=True,
    )


@pytest.fixture
def admin_client(api_client, admin):
    api_client.force_authenticate(user=admin)
    return api_client


@pytest.fixture
def guest_client(api_client, guest_user):
    api_client.force_authenticate(user=guest_user)
    return api_client


# ---------------------------------------------------------------------------
# AC1 & AC2: GET /crm/board-templates/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac1_list_returns_200_and_four_templates(admin_client):
    """AC1: authenticated company member gets 200 with exactly 4 templates."""
    response = admin_client.get(TEMPLATES_URL)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 4


@pytest.mark.django_db
def test_ac2_each_template_has_required_fields(admin_client):
    """AC2: each template entry has id, name, and a non-empty columns list."""
    response = admin_client.get(TEMPLATES_URL)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    ids_seen = set()
    for template in data:
        assert 'id' in template, "Missing 'id' field"
        assert 'name' in template, "Missing 'name' field"
        assert 'columns' in template, "Missing 'columns' field"
        assert isinstance(template['columns'], list), "'columns' must be a list"
        assert len(template['columns']) > 0, "columns must be non-empty"
        assert template['id'] not in ids_seen, f"Duplicate template id: {template['id']}"
        ids_seen.add(template['id'])
    # Verify all four expected IDs are present
    assert ids_seen == {'basic', 'sales', 'recruitment', 'project'}


# ---------------------------------------------------------------------------
# AC3–AC7: POST /crm/boards/ with various template_id values
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac3_no_template_id_creates_basic_three_columns(admin_client):
    """AC3: omitting template_id falls back to basic → 3 columns."""
    response = admin_client.post(BOARDS_URL, {'name': 'Board AC3'}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    board_id = response.json()['id']
    assert Column.objects.filter(board_id=board_id).count() == 3


@pytest.mark.django_db
def test_ac4_sales_template_creates_four_columns(admin_client):
    """AC4: template_id=sales → 4 columns."""
    response = admin_client.post(
        BOARDS_URL,
        {'name': 'Board AC4', 'template_id': 'sales'},
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    board_id = response.json()['id']
    assert Column.objects.filter(board_id=board_id).count() == 4


@pytest.mark.django_db
def test_ac5_recruitment_template_creates_five_columns(admin_client):
    """AC5: template_id=recruitment → 5 columns."""
    response = admin_client.post(
        BOARDS_URL,
        {'name': 'Board AC5', 'template_id': 'recruitment'},
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    board_id = response.json()['id']
    assert Column.objects.filter(board_id=board_id).count() == 5


@pytest.mark.django_db
def test_ac6_project_template_creates_four_columns(admin_client):
    """AC6: template_id=project → 4 columns."""
    response = admin_client.post(
        BOARDS_URL,
        {'name': 'Board AC6', 'template_id': 'project'},
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    board_id = response.json()['id']
    assert Column.objects.filter(board_id=board_id).count() == 4


@pytest.mark.django_db
def test_ac7_unknown_template_id_falls_back_to_basic(admin_client):
    """AC7: unknown template_id falls back to basic (3 columns), returns 201 not 400."""
    response = admin_client.post(
        BOARDS_URL,
        {'name': 'Board AC7', 'template_id': 'nonexistent_template'},
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    board_id = response.json()['id']
    assert Column.objects.filter(board_id=board_id).count() == 3


# ---------------------------------------------------------------------------
# AC8: language negotiation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac8_english_language_header_returns_english_names(admin_client):
    """AC8: Accept-Language: en returns template names in English."""
    response = admin_client.get(TEMPLATES_URL, HTTP_ACCEPT_LANGUAGE='en')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    by_id = {t['id']: t for t in data}
    assert by_id['basic']['name'] == 'Basic'
    assert by_id['sales']['name'] == 'Sales'
    assert by_id['recruitment']['name'] == 'Recruitment'
    assert by_id['project']['name'] == 'Project Management'
    # Check a sample column name
    sales_columns = by_id['sales']['columns']
    assert 'New Leads' in sales_columns


# ---------------------------------------------------------------------------
# AC9: guest is blocked from board-templates list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac9_guest_cannot_access_board_templates(guest_client):
    """AC9: guest role → 403 on GET /crm/board-templates/."""
    response = guest_client.get(TEMPLATES_URL)
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_unauthenticated_cannot_access_board_templates(api_client):
    """Unauthenticated requests → 401."""
    response = api_client.get(TEMPLATES_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
