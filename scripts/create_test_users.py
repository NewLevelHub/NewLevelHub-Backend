"""
create_test_users.py
--------------------
Idempotent seed script for local/staging development.

Run with:
    python manage.py shell < scripts/create_test_users.py

Creates:
    superadmin   : admin@nlh.test  / Admin123!
    company_admin: ca@nlh.test     / Admin123!  (linked to "Test Company")
    employee     : emp@nlh.test    / Admin123!  (linked to "Test Company")
    guest        : guest@nlh.test  / Admin123!  (no company)

All operations use get_or_create so the script is safe to re-run.
"""

from apps.companies.models import Company
from apps.users.models import User

SEPARATOR = "-" * 60


def create_company(name):
    company, created = Company.objects.get_or_create(name=name)
    status = "CREATED" if created else "EXISTS"
    print(f"  [{status}] Company: {company.name}  (id={company.id})")
    return company


def create_user(email, password, role, company=None, first_name="Test", last_name="User"):
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "role": role,
            "is_active": True,
        },
    )

    if created:
        # set_password hashes the password; must call save() after get_or_create
        # because create_user is not called here (we use get_or_create for idempotency)
        user.set_password(password)

        if role == "superadmin":
            user.is_staff = True
            user.is_superuser = True

        if company:
            user.company = company

        user.save()
        status = "CREATED"
    else:
        status = "EXISTS "
        # Keep company assignment in sync even on subsequent runs
        if company and user.company != company:
            user.company = company
            user.save(update_fields=["company"])

    return user, status


def run():
    print(SEPARATOR)
    print("NewLevelHub — Test User Seed Script")
    print(SEPARATOR)

    # 1. Company
    print("\n[1/5] Companies")
    company = create_company("Test Company")

    # 2. Users
    print("\n[2/5] Users")

    users_to_create = [
        dict(email="admin@nlh.test", password="Admin123!", role="superadmin",
             first_name="Super", last_name="Admin", company=None),
        dict(email="ca@nlh.test", password="Admin123!", role="company_admin",
             first_name="Company", last_name="Admin", company=company),
        dict(email="emp@nlh.test", password="Admin123!", role="employee",
             first_name="Test", last_name="Employee", company=company),
        dict(email="guest@nlh.test", password="Admin123!", role="guest",
             first_name="Test", last_name="Guest", company=None),
    ]

    created_users = []
    for kwargs in users_to_create:
        user, status = create_user(**kwargs)
        created_users.append((user, status))
        print(f"  [{status}] {user.email:<30}  role={user.role:<14}  company={user.company or '—'}")

    # 3. Summary
    print(f"\n{SEPARATOR}")
    print("Summary")
    print(SEPARATOR)
    print(f"{'Email':<30} {'Role':<14} {'Company':<20} {'Status'}")
    print("-" * 80)
    for user, status in created_users:
        company_name = user.company.name if user.company else "—"
        print(f"{user.email:<30} {user.role:<14} {company_name:<20} {status}")

    print(SEPARATOR)
    print("Done. All test users are ready.")
    print(SEPARATOR)


run()
