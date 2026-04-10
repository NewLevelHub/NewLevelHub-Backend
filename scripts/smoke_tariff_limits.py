"""
smoke_tariff_limits.py
----------------------
Acceptance-criteria smoke test for tariff limits (DEV-62 + DEV-63):
  - InvitationCreateSerializer
  - BoardViewSet.create
  - FileViewSet.create
  - system notifications at 80% and 95% for company_admin

Run with:
    python manage.py shell < scripts/smoke_tariff_limits.py
"""

import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board
from apps.notifications.models import Notification
from apps.storage.models import File
from apps.users.models import User


SEPARATOR = "=" * 72
BYTES_IN_GB = 1024 * 1024 * 1024


def _ok(label):
    print(f"[PASS] {label}")


def _fail(label, details):
    print(f"[FAIL] {label}: {details}")
    raise SystemExit(1)


def _expect(label, condition, details):
    if condition:
        _ok(label)
    else:
        _fail(label, details)


def _extract_error_text(response_data):
    if not isinstance(response_data, dict):
        return str(response_data)

    detail = response_data.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        return str(detail[0])
    if isinstance(detail, dict):
        non_field_errors = detail.get("non_field_errors")
        if isinstance(non_field_errors, list) and non_field_errors:
            return str(non_field_errors[0])
        for value in detail.values():
            if isinstance(value, list) and value:
                return str(value[0])
            if isinstance(value, str):
                return value

    return str(response_data)


def _has_notification_contains(user, body_fragment):
    return Notification.objects.filter(
        user=user,
        notification_type="announcement_company",
        title="System limit warning",
        body__contains=body_fragment,
    ).exists()


def _mk_upload(name, size):
    return SimpleUploadedFile(name, b"a" * size, content_type="text/plain")


def _make_company_admin_client(run_id, suffix, max_employees=20, max_boards=20, storage_limit_gb=1):
    company = Company.objects.create(
        name=f"QA Limits {suffix} {run_id}",
        plan="basic",
        max_employees=max_employees,
        max_boards=max_boards,
        storage_limit_gb=storage_limit_gb,
    )
    admin = User.objects.create_user(
        email=f"qa-admin-{suffix}-{run_id}@nlh.test",
        password="Admin123!",
        first_name="QA",
        last_name="Admin",
        role="company_admin",
        company=company,
        is_email_verified=True,
    )
    client = APIClient(HTTP_HOST="localhost")
    client.force_authenticate(user=admin)
    return company, admin, client


def _check_dev62_defaults(run_id):
    print("\n[AC/DEV-62] Plan defaults")
    expected = Company.PLAN_DEFAULT_LIMITS
    _expect(
        "PLAN_DEFAULT_LIMITS has required plans",
        all(plan in expected for plan in ("basic", "standard", "premium")),
        f"actual keys={list(expected.keys())}",
    )
    _expect(
        "basic defaults",
        expected.get("basic") == {"max_employees": 10, "max_boards": 1, "storage_limit_gb": 5},
        f"actual={expected.get('basic')}",
    )
    _expect(
        "standard defaults",
        expected.get("standard") == {"max_employees": 30, "max_boards": 5, "storage_limit_gb": 20},
        f"actual={expected.get('standard')}",
    )
    _expect(
        "premium defaults",
        expected.get("premium") == {"max_employees": 9999, "max_boards": 9999, "storage_limit_gb": 100},
        f"actual={expected.get('premium')}",
    )

    company_std = Company.objects.create(name=f"QA STD {run_id}", plan="standard")
    company_prm = Company.objects.create(name=f"QA PRM {run_id}", plan="premium")
    _expect(
        "standard company auto-applies defaults on create",
        (
            company_std.max_employees == 30
            and company_std.max_boards == 5
            and company_std.storage_limit_gb == 20
        ),
        (
            "actual="
            f"{company_std.max_employees}/{company_std.max_boards}/{company_std.storage_limit_gb}"
        ),
    )
    _expect(
        "premium company auto-applies defaults on create",
        (
            company_prm.max_employees == 9999
            and company_prm.max_boards == 9999
            and company_prm.storage_limit_gb == 100
        ),
        (
            "actual="
            f"{company_prm.max_employees}/{company_prm.max_boards}/{company_prm.storage_limit_gb}"
        ),
    )


def _check_invitation_limits(run_id):
    print("\n[AC] InvitationCreateSerializer (employees)")
    company, admin, client = _make_company_admin_client(
        run_id=run_id,
        suffix="invites",
        max_employees=20,
    )

    # members include company_admin, so for 80% request:
    # active_members + 1 == 16 -> active_members == 15 -> employees == 14
    for idx in range(14):
        User.objects.create_user(
            email=f"qa-emp-inv-{run_id}-{idx}@nlh.test",
            password="Admin123!",
            first_name="Emp",
            last_name=str(idx),
            role="employee",
            company=company,
            is_active=True,
        )

    invite_url = f"/api/v1/companies/{company.id}/invitations/"
    r = client.post(invite_url, {"email": f"invite80-{run_id}@nlh.test", "role": "employee"}, format="json")
    _expect("Invite at 80% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Invite at 80% creates system notification",
        _has_notification_contains(admin, "Employee usage reached 80%"),
        "Missing 80% employee notification",
    )

    # Move from active_members=15 to active_members=18 for 95% request.
    for idx in range(3):
        User.objects.create_user(
            email=f"qa-emp-inv-mid-{run_id}-{idx}@nlh.test",
            password="Admin123!",
            first_name="Emp",
            last_name=f"Mid{idx}",
            role="employee",
            company=company,
            is_active=True,
        )

    r = client.post(invite_url, {"email": f"invite95-{run_id}@nlh.test", "role": "employee"}, format="json")
    _expect("Invite at 95% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Invite at 95% creates system notification",
        _has_notification_contains(admin, "Employee usage reached 95%"),
        "Missing 95% employee notification",
    )

    # At/over limit must return 400 with exact message.
    for idx in range(2):
        User.objects.create_user(
            email=f"qa-emp-inv-max-{run_id}-{idx}@nlh.test",
            password="Admin123!",
            first_name="Emp",
            last_name=f"Max{idx}",
            role="employee",
            company=company,
            is_active=True,
        )
    r = client.post(
        invite_url,
        {"email": f"invite-blocked-{run_id}@nlh.test", "role": "employee"},
        format="json",
    )
    _expect(
        "Invite at limit returns 400 'Employee limit reached'",
        r.status_code == 400 and _extract_error_text(r.data) == "Employee limit reached",
        f"status={r.status_code}, data={r.data}",
    )


def _check_board_limits(run_id):
    print("\n[AC] BoardViewSet.create (boards)")
    company, admin, client = _make_company_admin_client(
        run_id=run_id,
        suffix="boards",
        max_boards=20,
    )
    for idx in range(15):
        Board.objects.create(company=company, name=f"B80-{run_id}-{idx}", created_by=admin)

    r = client.post("/api/v1/crm/boards/", {"name": f"Board80-{run_id}"}, format="json")
    _expect("Board at 80% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Board at 80% creates system notification",
        _has_notification_contains(admin, "Board usage reached 80%"),
        "Missing 80% board notification",
    )

    for idx in range(2):
        Board.objects.create(company=company, name=f"B95-pre-{run_id}-{idx}", created_by=admin)
    r = client.post("/api/v1/crm/boards/", {"name": f"Board95-{run_id}"}, format="json")
    _expect("Board at 95% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Board at 95% creates system notification",
        _has_notification_contains(admin, "Board usage reached 95%"),
        "Missing 95% board notification",
    )

    Board.objects.create(company=company, name=f"B-max-{run_id}", created_by=admin)
    r = client.post("/api/v1/crm/boards/", {"name": f"BoardBlocked-{run_id}"}, format="json")
    _expect(
        "Board at limit returns 400 'Board limit reached'",
        r.status_code == 400 and _extract_error_text(r.data) == "Board limit reached",
        f"status={r.status_code}, data={r.data}",
    )


def _check_storage_limits(run_id):
    print("\n[AC] FileViewSet.create (storage)")
    company, admin, client = _make_company_admin_client(
        run_id=run_id,
        suffix="storage",
        storage_limit_gb=1,
    )

    # ~0.79 GB + 20 MB => >=80%
    File.objects.create(
        name=f"existing80-{run_id}.txt",
        file=_mk_upload(f"existing80-{run_id}.txt", 10),
        file_size=int(0.79 * BYTES_IN_GB),
        content_type="text/plain",
        owner=admin,
        company=company,
    )
    r = client.post(
        "/api/v1/storage/files/",
        {"name": f"new80-{run_id}.txt", "file": _mk_upload(f"new80-{run_id}.txt", 20 * 1024 * 1024)},
        format="multipart",
    )
    _expect("Storage at 80% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Storage at 80% creates system notification",
        _has_notification_contains(admin, "Storage usage reached 80%"),
        "Missing 80% storage notification",
    )

    # Bring usage close to 95%, then upload once more.
    File.objects.create(
        name=f"existing95-{run_id}.txt",
        file=_mk_upload(f"existing95-{run_id}.txt", 10),
        file_size=int(0.13 * BYTES_IN_GB),
        content_type="text/plain",
        owner=admin,
        company=company,
    )
    r = client.post(
        "/api/v1/storage/files/",
        {"name": f"new95-{run_id}.txt", "file": _mk_upload(f"new95-{run_id}.txt", 30 * 1024 * 1024)},
        format="multipart",
    )
    _expect("Storage at 95% returns 201", r.status_code == 201, f"status={r.status_code}, data={r.data}")
    _expect(
        "Storage at 95% creates system notification",
        _has_notification_contains(admin, "Storage usage reached 95%"),
        "Missing 95% storage notification",
    )

    # Force used >= limit, next upload must fail.
    File.objects.create(
        name=f"existing-max-{run_id}.txt",
        file=_mk_upload(f"existing-max-{run_id}.txt", 10),
        file_size=100 * 1024 * 1024,
        content_type="text/plain",
        owner=admin,
        company=company,
    )
    r = client.post(
        "/api/v1/storage/files/",
        {"name": f"blocked-{run_id}.txt", "file": _mk_upload(f"blocked-{run_id}.txt", 1024)},
        format="multipart",
    )
    _expect(
        "Storage at limit returns 400 'Storage limit reached'",
        r.status_code == 400 and _extract_error_text(r.data) == "Storage limit reached",
        f"status={r.status_code}, data={r.data}",
    )


def run():
    run_id = uuid.uuid4().hex[:8]
    print(SEPARATOR)
    print(f"Tariff limits AC smoke test | run={run_id}")
    print(SEPARATOR)

    _check_dev62_defaults(run_id)
    _check_invitation_limits(run_id)
    _check_board_limits(run_id)
    _check_storage_limits(run_id)

    print("\n" + SEPARATOR)
    print("All acceptance criteria checks passed.")
    print(SEPARATOR)


run()
