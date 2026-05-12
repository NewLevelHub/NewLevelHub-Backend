"""
Acceptance-criteria tests for the announcements feed (DEV-100).

AC items covered (one section per item):
  1. POST (superadmin) с {title, text, category, image?, is_pinned, company_id (null=БЦ)}
  2. POST (company_admin) — автоматически company_id = своя компания
  3. GET — БЦ (company=null) + своя компания; ordered: is_pinned desc, created_at desc;
     guest видит только БЦ
  4. Cursor-based пагинация для бесконечного скролла
  5. DELETE — автор или суперадмин

Tests use the API endpoint mounted at /api/v1/services/announcements/.
Authentication is forced via APIClient.force_authenticate(), so token issuance is
out of scope for this layer.
"""

import io

import pytest
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.services.models import Announcement
from apps.users.models import User


ANNOUNCEMENTS_URL = '/api/v1/services/announcements/'


def announcement_detail_url(announcement_id):
    return f'/api/v1/services/announcements/{announcement_id}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Acme Corp', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Globex Inc', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='announcement-superadmin@test.local',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='announcement-admin@test.local',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='announcement-employee@test.local',
        password='pass',
        first_name='Reg',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_company_admin(db, other_company):
    return User.objects.create_user(
        email='announcement-admin-other@test.local',
        password='pass',
        first_name='Other',
        last_name='Admin',
        role='company_admin',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def other_company_employee(db, other_company):
    return User.objects.create_user(
        email='announcement-employee-other@test.local',
        password='pass',
        first_name='Other',
        last_name='Employee',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='announcement-guest@test.local',
        password='pass',
        first_name='Visitor',
        last_name='Guest',
        role='guest',
        is_email_verified=True,
    )


def _payload(**overrides):
    base = {
        'title': 'Hello world',
        'text': 'This is the body of the announcement.',
        'category': 'info',
        'is_pinned': False,
    }
    base.update(overrides)
    return base


def _make_announcement(*, author, company=None, title='Demo', text='Body', category='info',
                       is_pinned=False):
    """
    Create an Announcement directly in DB.

    The model still uses ``body`` internally; tests pass ``text`` to mirror the
    AC vocabulary and we map it to body here.
    """
    return Announcement.objects.create(
        title=title,
        body=text,
        category=category,
        is_pinned=is_pinned,
        author=author,
        company=company,
        scope='company' if company else 'building',
    )


def _image_file(name='picture.png'):
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color='red').save(buf, format='PNG')
    buf.seek(0)
    buf.name = name
    return buf


# ---------------------------------------------------------------------------
# AC #1 — POST (superadmin)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSuperadminCreateAC:
    """AC: POST (superadmin) с {title, text, category, image?, is_pinned, company_id (null=БЦ)}"""

    def test_unauthenticated_post_is_rejected(self, api_client):
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_superadmin_creates_building_announcement_with_null_company_id(
        self, api_client, superadmin
    ):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(company_id=None),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data.get('company') in (None, '')
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id is None
        assert announcement.author_id == superadmin.id

    def test_superadmin_can_omit_company_id_for_building_post(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id is None

    def test_superadmin_creates_company_announcement_with_company_id(
        self, api_client, superadmin, company
    ):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(company_id=company.id),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id == company.id

    def test_text_field_is_persisted(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(text='Custom AC body content'),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        # Backend stores in ``body`` field; serializer accepts ``text`` per AC.
        assert announcement.body == 'Custom AC body content'

    def test_response_exposes_text_field(self, api_client, superadmin):
        """Response payload should reflect the AC vocabulary (``text``)."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(text='Visible body'),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data.get('text') == 'Visible body'

    def test_is_pinned_flag_respected(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(is_pinned=True),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data.get('is_pinned') is True

    def test_image_is_optional(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_image_upload_supported(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        payload = _payload()
        payload['image'] = _image_file()
        response = api_client.post(ANNOUNCEMENTS_URL, payload, format='multipart')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.image  # ImageField truthy

    @pytest.mark.parametrize('category', ['info', 'important', 'event'])
    def test_category_choices_supported(self, api_client, superadmin, category):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(category=category),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data['category'] == category

    def test_invalid_category_rejected(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(category='not_a_real_category'),
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC #2 — POST (company_admin)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyAdminCreateAC:
    """AC: POST (company_admin) — автоматически company_id = своя компания"""

    def test_company_admin_creates_for_own_company_implicitly(
        self, api_client, company_admin, company
    ):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id == company.id
        assert announcement.author_id == company_admin.id

    def test_company_admin_cannot_post_to_other_company(
        self, api_client, company_admin, other_company, company
    ):
        """
        Even if they pass another company_id explicitly, the backend must
        force the announcement onto their own company.
        """
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(company_id=other_company.id),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id == company.id

    def test_company_admin_cannot_post_building_announcement(
        self, api_client, company_admin, company
    ):
        """
        Building-wide announcements require superadmin (company_id=null is a
        platform-level action).  Even if a company_admin sends company_id=null,
        the announcement is forced onto their company.
        """
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            ANNOUNCEMENTS_URL,
            _payload(company_id=None),
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        announcement = Announcement.objects.get(pk=response.data['id'])
        assert announcement.company_id == company.id

    def test_employee_cannot_create(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_create(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.post(ANNOUNCEMENTS_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# AC #3 — GET (visibility + ordering)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAnnouncementListAC:
    """
    AC: GET — БЦ (company=null) + своя компания; ordered: is_pinned desc,
    created_at desc; guest видит только БЦ.
    """

    def test_unauthenticated_list_is_rejected(self, api_client):
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_sees_building_and_own_company_only(
        self,
        api_client,
        superadmin,
        company_admin,
        other_company_admin,
        employee,
    ):
        building = _make_announcement(author=superadmin, company=None, title='BC-news')
        own = _make_announcement(author=company_admin, company=employee.company, title='Own-news')
        foreign = _make_announcement(
            author=other_company_admin,
            company=other_company_admin.company,
            title='Foreign-news',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data['results']}
        assert building.id in ids
        assert own.id in ids
        assert foreign.id not in ids

    def test_company_admin_sees_building_and_own_company_only(
        self,
        api_client,
        superadmin,
        company_admin,
        other_company_admin,
    ):
        building = _make_announcement(author=superadmin, company=None)
        own = _make_announcement(author=company_admin, company=company_admin.company)
        foreign = _make_announcement(
            author=other_company_admin, company=other_company_admin.company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data['results']}
        assert {building.id, own.id} <= ids
        assert foreign.id not in ids

    def test_superadmin_sees_all_announcements(
        self, api_client, superadmin, company_admin, other_company_admin,
    ):
        building = _make_announcement(author=superadmin, company=None)
        own = _make_announcement(author=company_admin, company=company_admin.company)
        foreign = _make_announcement(
            author=other_company_admin, company=other_company_admin.company,
        )

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data['results']}
        assert {building.id, own.id, foreign.id} <= ids

    def test_guest_sees_building_only(
        self,
        api_client,
        superadmin,
        company_admin,
        other_company_admin,
        guest_user,
    ):
        """AC: guest видит только БЦ (company=null)."""
        building = _make_announcement(author=superadmin, company=None)
        company_only = _make_announcement(
            author=company_admin, company=company_admin.company,
        )
        another_company_only = _make_announcement(
            author=other_company_admin, company=other_company_admin.company,
        )

        api_client.force_authenticate(user=guest_user)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data['results']}
        assert building.id in ids
        assert company_only.id not in ids
        assert another_company_only.id not in ids

    def test_pinned_first_then_created_desc(self, api_client, superadmin):
        """AC ordering: is_pinned desc, then created_at desc."""
        oldest = _make_announcement(author=superadmin, company=None, title='oldest')
        middle_pinned = _make_announcement(
            author=superadmin, company=None, title='middle-pinned', is_pinned=True,
        )
        newest = _make_announcement(author=superadmin, company=None, title='newest')

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        ordered_ids = [row['id'] for row in results]
        relevant = [aid for aid in ordered_ids if aid in {oldest.id, middle_pinned.id, newest.id}]
        # Pinned must appear strictly before any non-pinned of the same scope.
        assert relevant[0] == middle_pinned.id
        # Among non-pinned, newest must precede oldest.
        non_pinned_in_order = [aid for aid in relevant if aid != middle_pinned.id]
        assert non_pinned_in_order == [newest.id, oldest.id]


# ---------------------------------------------------------------------------
# AC #4 — Cursor-based pagination
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAnnouncementCursorPaginationAC:
    """AC: cursor-based пагинация для бесконечного скролла."""

    def _create_many(self, *, author, count, base_title='news'):
        return [
            _make_announcement(author=author, company=None, title=f'{base_title}-{i}')
            for i in range(count)
        ]

    def test_response_envelope_has_cursor_fields(self, api_client, superadmin):
        self._create_many(author=superadmin, count=3)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        # Cursor pagination must surface next/previous cursor URLs (or null).
        assert 'next' in response.data
        assert 'previous' in response.data
        assert 'results' in response.data

    def test_response_envelope_has_no_count_field(self, api_client, superadmin):
        """
        Cursor pagination intentionally omits ``count`` (the dataset is treated
        as a stream — DRF's CursorPagination does not compute totals).
        ``count`` would only be present with PageNumberPagination, which is
        unsuitable for infinite scroll over a feed that grows over time.
        """
        self._create_many(author=superadmin, count=3)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert 'count' not in response.data

    def test_next_link_uses_cursor_query_param(self, api_client, superadmin):
        """
        AC: cursor-based.  The opaque cursor token must travel in a ``cursor``
        query parameter (the canonical CursorPagination signature), not as a
        page number.
        """
        self._create_many(author=superadmin, count=5)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL, {'page_size': 2})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['next'] is not None
        assert 'cursor=' in response.data['next']

    def test_uses_cursor_pagination_class(self):
        """
        Hard assertion on the class itself — CursorPagination or a subclass
        must be wired onto the AnnouncementViewSet to satisfy the AC.
        """
        from rest_framework.pagination import CursorPagination
        from apps.services.views import AnnouncementViewSet
        pagination_class = AnnouncementViewSet.pagination_class
        assert pagination_class is not None
        assert issubclass(pagination_class, CursorPagination)

    def test_page_size_query_param_supported(self, api_client, superadmin):
        self._create_many(author=superadmin, count=5)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL, {'page_size': 2})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 2
        assert response.data['next']  # next cursor must be present

    def test_following_cursor_returns_remaining_pages(self, api_client, superadmin):
        self._create_many(author=superadmin, count=5)
        api_client.force_authenticate(user=superadmin)

        first = api_client.get(ANNOUNCEMENTS_URL, {'page_size': 2})
        assert first.status_code == status.HTTP_200_OK
        assert first.data['next']
        second = api_client.get(first.data['next'])
        assert second.status_code == status.HTTP_200_OK
        first_ids = [row['id'] for row in first.data['results']]
        second_ids = [row['id'] for row in second.data['results']]
        assert set(first_ids).isdisjoint(set(second_ids))


# ---------------------------------------------------------------------------
# AC #5 — DELETE: author or superadmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAnnouncementDeleteAC:
    """AC: DELETE — автор или суперадмин."""

    def test_unauthenticated_delete_is_rejected(self, api_client, superadmin):
        announcement = _make_announcement(author=superadmin, company=None)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_author_company_admin_can_delete_own(
        self, api_client, company_admin, company,
    ):
        announcement = _make_announcement(author=company_admin, company=company)
        api_client.force_authenticate(user=company_admin)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Announcement.objects.filter(pk=announcement.id).exists()

    def test_superadmin_can_delete_any_announcement(
        self, api_client, superadmin, company_admin, company,
    ):
        announcement = _make_announcement(author=company_admin, company=company)
        api_client.force_authenticate(user=superadmin)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Announcement.objects.filter(pk=announcement.id).exists()

    def test_other_company_admin_cannot_delete(
        self, api_client, company_admin, company, other_company_admin,
    ):
        announcement = _make_announcement(author=company_admin, company=company)
        api_client.force_authenticate(user=other_company_admin)
        response = api_client.delete(announcement_detail_url(announcement.id))
        # Either filtered out (404) or rejected (403) — both satisfy the AC.
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        assert Announcement.objects.filter(pk=announcement.id).exists()

    def test_non_author_company_admin_in_same_company_cannot_delete(
        self, api_client, company,
    ):
        """Even another company_admin in the same company is not the author."""
        author = User.objects.create_user(
            email='author-admin@test.local',
            password='pass',
            first_name='Author',
            last_name='Admin',
            role='company_admin',
            company=company,
            is_email_verified=True,
        )
        non_author_admin = User.objects.create_user(
            email='non-author-admin@test.local',
            password='pass',
            first_name='NonAuthor',
            last_name='Admin',
            role='company_admin',
            company=company,
            is_email_verified=True,
        )
        announcement = _make_announcement(author=author, company=company)
        api_client.force_authenticate(user=non_author_admin)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Announcement.objects.filter(pk=announcement.id).exists()

    def test_employee_cannot_delete_someone_elses_announcement(
        self, api_client, company_admin, employee, company,
    ):
        announcement = _make_announcement(author=company_admin, company=company)
        api_client.force_authenticate(user=employee)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Announcement.objects.filter(pk=announcement.id).exists()

    def test_guest_cannot_delete(self, api_client, superadmin, guest_user):
        announcement = _make_announcement(author=superadmin, company=None)
        api_client.force_authenticate(user=guest_user)
        response = api_client.delete(announcement_detail_url(announcement.id))
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        assert Announcement.objects.filter(pk=announcement.id).exists()


# ---------------------------------------------------------------------------
# AC #2 (DEV-110) — read_count visibility
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestReadCountVisibilityAC:
    """
    AC DEV-110 #2: read_count is an integer only for the announcement author,
    company_admin, and superadmin.  A plain employee who is NOT the author
    receives null instead.
    """

    def _mark_read(self, announcement, user):
        from apps.services.models import AnnouncementRead
        AnnouncementRead.objects.get_or_create(announcement=announcement, user=user)

    def test_read_count_visible_to_author(self, api_client, employee, company):
        """Employee who IS the author sees read_count as an integer."""
        announcement = _make_announcement(author=employee, company=company)
        self._mark_read(announcement, employee)

        api_client.force_authenticate(user=employee)
        response = api_client.get(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data['read_count'], int)
        assert response.data['read_count'] == 1

    def test_read_count_null_for_non_author_employee(
        self, api_client, company_admin, employee, company
    ):
        """Employee who is NOT the author sees read_count as null."""
        announcement = _make_announcement(author=company_admin, company=company)
        self._mark_read(announcement, employee)

        api_client.force_authenticate(user=employee)
        response = api_client.get(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['read_count'] is None

    def test_read_count_visible_to_company_admin(
        self, api_client, company_admin, employee, company
    ):
        """company_admin sees read_count as an integer regardless of authorship."""
        announcement = _make_announcement(author=employee, company=company)
        self._mark_read(announcement, employee)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data['read_count'], int)
        assert response.data['read_count'] == 1

    def test_read_count_visible_to_superadmin(
        self, api_client, superadmin, company_admin, company
    ):
        """superadmin sees read_count as an integer."""
        announcement = _make_announcement(author=company_admin, company=company)
        self._mark_read(announcement, company_admin)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(announcement_detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data['read_count'], int)
        assert response.data['read_count'] == 1
