"""
Acceptance-criteria wiring tests (DEV-63): create_notification coverage across modules.

Maps to AC:
  Booking: create → booking_confirmed; cancel → booking_cancelled;
           admin-cancel → booking_cancelled with reason
  CRM: assign → task_assigned; move → task_moved; comment → task_comment;
       deadline tomorrow → task_deadline
  Access: validate QR → guest_validated; pass expiring → guest_pass_expiring
  Services: status change → service_request_update; new announcement → announcement
  HR: leave review → leave_review; invitation create → invitation

Also: Celery stubs that were TODO must call create_notification / fan-out logic.
"""
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.notifications.models import Notification
from apps.services.models import Floor
from apps.users.models import User


# ── Helpers / URLs ──────────────────────────────────────────────────────

BOOKINGS_URL = '/api/v1/bookings/reservations/'
TASKS_URL = '/api/v1/crm/tasks/'
ANNOUNCEMENTS_URL = '/api/v1/services/announcements/'
ACCESS_VALIDATE = '/api/v1/access/validate/'
HR_LEAVE_URL = '/api/v1/hr/leaves/'


def _auth(client, user):
    client.force_authenticate(user=user)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='svc-ac-employee@test.local',
        password='pass',
        first_name='Svc',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


def _guest_pass_payload():
    now = timezone.now()
    return {
        'guest_name': 'Walk-in Guest',
        'guest_email': 'guest.walkin@test.local',
        'guest_phone': '+70000000001',
        'purpose': 'Visit',
        'valid_from': now.isoformat(),
        'valid_until': (now + timedelta(hours=23)).isoformat(),
        'is_single_use': True,
    }


# ── Booking AC ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ac_booking_create_emits_booking_confirmed(api_client):
    company = Company.objects.create(name='Booking AC Co', plan='basic', max_employees=50)
    employee = User.objects.create_user(
        email='booking-ac-emp@test.local',
        password='pass',
        first_name='Book',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    from apps.bookings.models import Resource

    Resource.objects.create(
        assigned_company=company,
        name='Room AC',
        resource_type='meeting_room',
        capacity=4,
        is_active=True,
        available_from='00:00',
        available_until='23:59',
        available_days=list(range(7)),
    )
    resource = Resource.objects.get(name='Room AC')
    # Pin to noon local time: prevents start+1h from crossing midnight and
    # triggering same_day_only validation when test runs between 23:00-23:59.
    local_tz = timezone.get_current_timezone()
    start = (timezone.now().astimezone(local_tz) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=1)
    _auth(api_client, employee)
    resp = api_client.post(
        BOOKINGS_URL,
        {
            'resource': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'description': 'ac',
        },
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert Notification.objects.filter(
        user=employee,
        notification_type='booking_confirmed',
    ).exists()


@pytest.mark.django_db
def test_ac_booking_admin_cancel_includes_reason_in_notification(api_client):
    company = Company.objects.create(name='Booking AC2', plan='basic', max_employees=50)
    admin = User.objects.create_user(
        email='booking-ac-admin@test.local',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )
    employee = User.objects.create_user(
        email='booking-ac-emp2@test.local',
        password='pass',
        first_name='Emp',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    from apps.bookings.models import Booking, Resource

    resource = Resource.objects.create(
        assigned_company=company,
        name='Room AC2',
        resource_type='desk',
        capacity=1,
        is_active=True,
    )
    start = timezone.now() + timedelta(days=2)
    booking = Booking.objects.create(
        user=employee,
        company=company,
        resource=resource,
        start_time=start,
        end_time=start + timedelta(hours=1),
        status='confirmed',
    )
    _auth(api_client, admin)
    reason = 'Closed for maintenance AC test'
    resp = api_client.post(
        f'{BOOKINGS_URL}{booking.id}/admin-cancel/',
        {'reason': reason},
        format='json',
    )
    assert resp.status_code == status.HTTP_200_OK
    notif = Notification.objects.filter(
        user=employee,
        notification_type='booking_cancelled',
    ).first()
    assert notif is not None
    assert reason in (notif.body or '')


# ── CRM AC ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ac_crm_task_move_emits_task_moved(api_client, admin_a, board_a, employee_a):
    """Move (drag) to another column → task_moved for watchers."""
    col_todo = Column.objects.create(board=board_a, name='К выполнению', position=1)
    col_done = Column.objects.create(board=board_a, name='Готово', position=3)
    task = Task.objects.create(
        column=col_todo,
        title='Move me AC',
        created_by=admin_a,
        assignee=employee_a,
        position=1,
    )
    _auth(api_client, admin_a)
    resp = api_client.post(
        f'{TASKS_URL}{task.pk}/move/',
        {'column_id': col_done.pk},
        format='json',
    )
    assert resp.status_code == status.HTTP_200_OK
    assert Notification.objects.filter(
        user=employee_a,
        notification_type='task_moved',
    ).exists()


@pytest.mark.django_db
def test_ac_crm_deadline_tomorrow_emits_task_deadline(admin_a, board_a, employee_a):
    """Periodic helper: tasks due tomorrow → task_deadline."""
    col = Column.objects.create(board=board_a, name='В работе', position=2)
    tomorrow_date = timezone.localdate() + timedelta(days=1)
    naive = datetime.combine(tomorrow_date, time(12, 0))
    deadline = timezone.make_aware(naive, timezone.get_current_timezone())
    task = Task.objects.create(
        column=col,
        title='Due tomorrow AC',
        created_by=admin_a,
        assignee=employee_a,
        position=1,
        deadline=deadline,
    )
    from apps.crm.tasks import notify_deadline_approaching

    notify_deadline_approaching()
    assert Notification.objects.filter(
        user=employee_a,
        notification_type='task_deadline',
        url=f'/crm/tasks/{task.pk}/',
    ).exists()


@pytest.mark.django_db
def test_ac_crm_task_comment_emits_task_comment_to_assignee_and_creator(api_client, admin_a, board_a, employee_a):
    """POST task comment → task_comment for assignee and creator (not author), correct title/body/url."""
    col = Column.objects.create(board=board_a, name='В работе', position=1)
    task = Task.objects.create(
        column=col,
        title='Комментарий AC',
        created_by=admin_a,
        assignee=employee_a,
        position=1,
    )
    commenter = User.objects.create_user(
        email='crm-ac-commenter@test.local',
        password='pass',
        first_name='Co',
        last_name='Mment',
        role='employee',
        company=admin_a.company,
        is_email_verified=True,
    )
    _auth(api_client, commenter)
    resp = api_client.post(
        f'{TASKS_URL}{task.pk}/comments/',
        {'text': 'Привет'},
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    exp_url = f'/crm/tasks/{task.pk}/'
    for user in (admin_a, employee_a):
        n = Notification.objects.filter(user=user, notification_type='task_comment').first()
        assert n is not None, f'missing task_comment for {user.email}'
        assert n.title == 'Новый комментарий к задаче'
        assert n.body == task.title
        assert n.url == exp_url


@pytest.mark.django_db
def test_ac_crm_task_comment_dedupes_when_assignee_is_creator(api_client, admin_a, board_a, employee_a):
    """If assignee == creator, only one in-app task_comment row for that user."""
    col = Column.objects.create(board=board_a, name='Колонка', position=1)
    task = Task.objects.create(
        column=col,
        title='Один получатель',
        created_by=admin_a,
        assignee=admin_a,
        position=1,
    )
    _auth(api_client, employee_a)
    resp = api_client.post(
        f'{TASKS_URL}{task.pk}/comments/',
        {'text': 'Один комментарий'},
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert Notification.objects.filter(user=admin_a, notification_type='task_comment').count() == 1


@pytest.mark.django_db
def test_ac_crm_task_comment_author_excluded_when_assignee(api_client, admin_a, board_a, employee_a):
    """Comment author does not receive task_comment when they are the assignee."""
    col = Column.objects.create(board=board_a, name='Todo', position=1)
    task = Task.objects.create(
        column=col,
        title='Свой комментарий',
        created_by=admin_a,
        assignee=employee_a,
        position=1,
    )
    _auth(api_client, employee_a)
    resp = api_client.post(
        f'{TASKS_URL}{task.pk}/comments/',
        {'text': 'Сам себе'},
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert not Notification.objects.filter(user=employee_a, notification_type='task_comment').exists()
    assert Notification.objects.filter(user=admin_a, notification_type='task_comment').exists()


# ── Access AC ───────────────────────────────────────────────────────────


@pytest.mark.django_db
@patch('apps.access.views.notify_pass_creator_on_entry.delay')
def test_ac_access_validate_qr_emits_guest_validated_in_app(mock_delay, api_client, company, company_admin):
    _auth(api_client, company_admin)
    create_resp = api_client.post('/api/v1/access/passes/', _guest_pass_payload(), format='json')
    assert create_resp.status_code == status.HTTP_201_CREATED
    gp = GuestPass.objects.get(pk=create_resp.data['id'])
    reception = User.objects.create_user(
        email='reception-ac@test.local',
        password='pass',
        first_name='Rec',
        last_name='Eption',
        role='reception',
        company=company,
        is_email_verified=True,
    )
    _auth(api_client, reception)
    validate_resp = api_client.post(ACCESS_VALIDATE, {'qr_code': str(gp.qr_code)}, format='json')
    assert validate_resp.status_code == status.HTTP_200_OK
    assert validate_resp.data['valid'] is True
    assert Notification.objects.filter(
        user=company_admin,
        notification_type='guest_validated',
    ).exists()


@pytest.mark.django_db
def test_ac_access_guest_pass_expiring_emits_notification(company_admin, company):
    soon = timezone.now() + timedelta(hours=6)
    GuestPass.objects.create(
        company=company,
        created_by=company_admin,
        guest_name='Soon Guest',
        guest_email='soon@test.local',
        visit_purpose='AC',
        valid_from=timezone.now() - timedelta(hours=1),
        valid_until=soon,
        status='active',
        qr_code=uuid.uuid4(),
        usage_type='multi',
    )
    from apps.access.tasks import notify_guest_passes_expiring_soon

    notify_guest_passes_expiring_soon()
    assert Notification.objects.filter(
        user=company_admin,
        notification_type='guest_pass_expiring',
    ).exists()


# ── Services AC ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ac_new_announcement_emits_announcement_notifications(superadmin):
    company = Company.objects.create(name='Ann AC Co', plan='basic', max_employees=20)
    member = User.objects.create_user(
        email='ann-member@test.local',
        password='pass',
        first_name='Mem',
        last_name='Ber',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    client = APIClient()
    _auth(client, superadmin)
    resp = client.post(
        ANNOUNCEMENTS_URL,
        {
            'title': 'AC Title',
            'text': 'AC Body',
            'category': 'info',
            'company_id': company.id,
            'notify_email': False,
        },
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    notif = Notification.objects.get(user=member, notification_type='announcement')
    assert notif.title == 'Новое объявление'
    assert 'AC Title' in notif.body
    assert 'AC Body' in notif.body


# ── HR / Invites AC ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ac_leave_submit_emits_leave_review_via_helper(api_client, company):
    admin = User.objects.create_user(
        email='hr-ac-admin@test.local',
        password='pass',
        first_name='HR',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )
    employee = User.objects.create_user(
        email='hr-ac-emp@test.local',
        password='pass',
        first_name='HR',
        last_name='Emp',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    from datetime import date

    _auth(api_client, employee)
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=2)
    resp = api_client.post(
        HR_LEAVE_URL,
        {
            'leave_type': 'vacation',
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'comment': 'AC vacation',
        },
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert Notification.objects.filter(
        user=admin,
        notification_type='leave_review',
    ).exists()


@pytest.mark.django_db
def test_ac_invitation_create_emits_invitation_notification(api_client, company):
    admin = User.objects.create_user(
        email='inv-ac-admin@test.local',
        password='pass',
        first_name='Inv',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )
    url = f'/api/v1/companies/{company.id}/invitations/'
    _auth(api_client, admin)
    resp = api_client.post(
        url,
        {'email': 'invitee-ac-fresh@test.local', 'role': 'employee'},
        format='json',
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert Notification.objects.filter(
        user=admin,
        notification_type='invitation',
    ).exists()


@pytest.mark.django_db
def test_ac_no_notification_todo_pass_stubs_remain():
    """Celery modules must not leave empty notification TODO bodies."""
    import inspect

    from apps.bookings import tasks as booking_tasks
    from apps.hr import tasks as hr_tasks
    from apps.services import tasks as services_tasks

    for mod in (booking_tasks, hr_tasks, services_tasks):
        src = inspect.getsource(mod)
        assert '# TODO: получить' not in src, f'Stale notification TODO in {mod.__name__}'


# ── Fixtures (CRM move / deadline tests) ────────────────────────────────


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='crm-ac-admin@test.local',
        password='pass',
        first_name='Ad',
        last_name='Min',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='crm-ac-emp@test.local',
        password='pass',
        first_name='Em',
        last_name='Pl',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='CRM AC Co', plan='basic', max_boards=10)


@pytest.fixture
def board_a(db, company_a, admin_a):
    return Board.objects.create(company=company_a, name='AC Board', created_by=admin_a)


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='ann-ac-super@test.local',
        password='pass',
        first_name='Su',
        last_name='Per',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def floor(db):
    return Floor.objects.create(number=8801, name='AC Floor')


@pytest.fixture
def company(db):
    return Company.objects.create(name='Access AC Base', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='access-ac-admin@test.local',
        password='pass',
        first_name='Co',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )
