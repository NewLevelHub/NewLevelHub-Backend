# NewLevelHub Backend — CLAUDE.md

## Project Overview

Multi-tenant SaaS backend for office/workspace management (bookings, CRM, HR, storage, access control).

**Stack:** Django 4.2.10, DRF 3.14.0, PostgreSQL 15, Celery 5.3 + Redis, SimpleJWT 5.3, drf-spectacular 0.27, Docker

---

## Commands

```bash
# First-time setup (copies .env.example, starts Docker, migrates)
./init.sh

# Docker (uses Compose v1 syntax — docker-compose, not docker compose)
docker-compose -f docker-compose.local.yml up -d --build
docker-compose -f docker-compose.local.yml logs -f
docker-compose -f docker-compose.local.yml down

# Migrations (run inside container or with local venv)
python manage.py makemigrations <app>
python manage.py migrate

# Tests
pytest                                          # All tests with coverage
pytest apps/core/tests/test_permissions.py      # Specific file
pytest -k "test_name"                           # Filter by name

# Linting (ignores E203, W503; max line length 120)
flake8 .

# Background tasks
celery -A config.celery worker -l info
celery -A config.celery beat -l info
```

---

## Quality Checks

После каждого изменения кода, перед тем как считать задачу выполненной, обязательно запусти:

```bash
flake8 .                                          # Lint (max line length 120)
python manage.py makemigrations --check           # Нет незафиксированных миграций
pytest --no-header -q                             # Все тесты проходят
```

Если любая из команд падает — исправь и перезапусти до зелёного состояния.

---

## Backend Development

После реализации изменений в API всегда верифицируй:

1. **Миграции** — `python manage.py makemigrations --check && python manage.py migrate` без ошибок
2. **Null-handling** — все новые поля в сериализаторах корректно обрабатывают `None` на существующих данных
3. **Соответствие фронтенду** — формат пагинации (`{count, next, previous, results}`), HTTP методы (GET/POST/PATCH/DELETE), имена полей
4. **Тесты** — интеграционные тесты требуют `@pytest.mark.django_db`; unit-тесты разрешений/миксинов — через `MagicMock` без DB

---

## Architecture

### Settings

| File | Purpose |
|------|---------|
| `config/settings/base.py` | Shared settings (loaded by all envs via `load_dotenv()`) |
| `config/settings/local.py` | Dev overrides — `DEBUG=True`, console email backend |
| `config/settings/production.py` | Prod overrides — HTTPS, SMTP, HSTS |

`DJANGO_SETTINGS_MODULE=config.settings.local` for dev and tests (set in `setup.cfg`).

### Apps (`apps/`)

| App | Key Models | Notes |
|-----|-----------|-------|
| `core` | `TimeStampedModel`, `SoftDeleteModel` | Base models, permissions, mixins, pagination, exceptions |
| `users` | `User`, `EmailVerificationToken`, `PasswordResetToken` | Auth, roles; endpoints at `/api/v1/auth/` |
| `companies` | `Company`, `CompanySettings`, `Invitation` | Multi-tenancy root |
| `bookings` | `Resource`, `Booking`, `RecurringBooking` | Has `filters.py` with `ResourceFilter`, `BookingFilter` |
| `crm` | `Board`, `Column`, `Task`, `Label`, `Checklist`, `Comment`, `TaskAttachment` | Kanban CRM |
| `storage` | `Folder`, `File`, `FileShare` | File management; no `tasks.py` |
| `hr` | `LeaveRequest`, `Onboarding` | HR workflows |
| `access` | `GuestPass`, `AccessLog` | QR-based guest access |
| `services` | `Floor`, `MapPoint`, `ServiceRequest`, `Announcement` | Building services |
| `notifications` | `Notification`, `NotificationPreference` | In-app notifications |
| `analytics` | — | Views/serializers only — no models, no admin, no migrations |

### URL Layout

```
/admin/                          Django admin
/api/v1/                         API root
  health/  ping/                 System (apps.core.urls — no prefix)
  auth/                          apps.users.urls
  companies/                     apps.companies.urls
  bookings/                      apps.bookings.urls
  crm/                           apps.crm.urls
  storage/                       apps.storage.urls
  hr/                            apps.hr.urls
  access/                        apps.access.urls
  services/                      apps.services.urls
  notifications/                 apps.notifications.urls
  analytics/                     apps.analytics.urls
/api/schema/                     Raw OpenAPI schema (SpectacularAPIView)
/api/docs/                       Swagger UI
/api/redoc/                      ReDoc
```

---

## Authentication & Permissions

**Auth:** JWT Bearer tokens. `Authorization: Bearer <access_token>`.
Tokens: access 60 min (env: `ACCESS_TOKEN_LIFETIME_MINUTES`), refresh 7 days (env: `REFRESH_TOKEN_LIFETIME_DAYS`).
Refresh tokens rotate and are blacklisted after rotation (`rest_framework_simplejwt.token_blacklist` must stay in `INSTALLED_APPS`).

**Roles:** `superadmin` > `company_admin` > `employee` > `guest`

### Permission classes — always import from `apps.core.permissions`

| Class | Who passes `has_permission` |
|-------|----------------------------|
| `IsSuperAdmin` | `superadmin` only |
| `IsCompanyAdmin` | `superadmin`, `company_admin` |
| `IsCompanyMember` | `superadmin`; `company_admin`/`employee` with a non-null `company_id` |
| `IsCompanyAdminOrReadOnly` | Safe methods (GET/HEAD/OPTIONS): any authenticated user. Unsafe methods: `superadmin`, `company_admin` only. |
| `IsOwnerOrAdmin` | Any authenticated user passes `has_permission`; object-level gate in `has_object_permission`. |

**Key nuances:**
- All permission classes inherit `_AuthenticatedPermission` — unauthenticated → `False` (DRF issues 401 via SimpleJWT's `WWW-Authenticate` header); authenticated-but-unauthorised → `False` (DRF issues 403).
- `IsCompanyAdminOrReadOnly` allows any authenticated user (including `guest`) to read. Stack with `IsCompanyMember` if guests must also be blocked from reads.
- `IsOwnerOrAdmin.owner_field` defaults to `'user'`. Override on the view class if the FK is named differently (e.g. `owner_field = 'created_by'`).
- Default DRF permission class is `IsAuthenticated` (set in `REST_FRAMEWORK` in `base.py`).

### Multi-tenancy mixins — import from `apps.core.mixins`

```python
class MyViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, ModelViewSet):
    permission_classes = [IsCompanyMember]
    queryset = MyModel.objects.all()
```

- **`CompanyIsolationMixin`** — scopes `get_queryset()` to `request.user.company_id`; superadmin sees all; users without a company get `qs.none()`. Override `company_field = 'company'` if the FK has a different name. For models without a direct company FK, set `company_lookup = 'parent__company'` (ORM traversal — Django appends `_id` automatically). When `company_lookup` is set it takes priority over `company_field`.
- **`SetCompanyOnCreateMixin`** — calls `serializer.save(company=request.user.company)` in `perform_create`.
- **`CompanyQuerySetMixin`** — backward-compat alias for `CompanyIsolationMixin`; do not use in new code.

**Company isolation decision tree for new ViewSets:**
1. Direct company FK → `CompanyIsolationMixin` (default, `company_field = 'company'`)
2. FK chain to company, no manager switching → `CompanyIsolationMixin` + `company_lookup = 'a__b__company'`
3. FK chain + manager switching (soft-delete) → manual `get_queryset` + `company_lookup_filter` class attribute
4. Nullable/dual-scope company → manual `get_queryset` + comment explaining why
5. Nested parent resource (scoped by URL kwarg) → `_get_parent_or_403()` guard method + `company_lookup_filter` attribute

---

## Models

### Base models (`apps/core/models.py`)

**`TimeStampedModel`** (abstract) — adds `created_at` (auto, indexed), `updated_at` (auto). Default ordering: `['-created_at']`.

**`SoftDeleteModel`** (abstract) — adds `is_deleted` (indexed), `deleted_at`. Two managers:
- `objects` — `SoftDeleteManager`: filters out `is_deleted=True`. Extra queryset methods: `.all_with_deleted()`, `.deleted_only()`.
- `all_objects` — plain `Manager`: returns everything including deleted rows.
- Instance methods: `.soft_delete()`, `.restore()`.

### User model (`apps/users/models.py`)

- `AUTH_USER_MODEL = 'users.User'`; `USERNAME_FIELD = 'email'`; `REQUIRED_FIELDS = ['first_name', 'last_name']`
- Fields: `email`, `phone`, `first_name`, `last_name`, `role`, `company` (FK → `companies.Company`, nullable), `position`, `avatar`, `is_active`, `is_staff`, `is_email_verified`, `date_joined`
- DB table: `users`; indexes on `(email, is_active)`, `(role, is_active)`, `(company, is_active)`
- Helper methods (not permission checks — use permission classes for those): `is_superadmin()`, `is_company_admin()`, `is_company_member()`, `full_name` (property)

---

## REST Framework Configuration

- **Pagination:** `apps.core.pagination.StandardPagination` — page size 20, max 100, query param `page_size`.
- **Filters:** `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter` applied globally.
- **Exception handler:** `apps.core.exceptions.custom_exception_handler` — wraps all error responses as `{"error": true, "status_code": <int>, "detail": <...>}`.
- **Renderer:** JSON only (no browsable API in production).
- **Parsers:** JSON, FormParser, MultiPartParser (supports file uploads).
- **Schema:** `drf_spectacular.openapi.AutoSchema` — decorate views with `@extend_schema` / `@extend_schema_view`.

---

## Database

- PostgreSQL 15; `ATOMIC_REQUESTS = True` (every request wrapped in a transaction).
- `TIME_ZONE = 'Asia/Almaty'`, `USE_TZ = True`.
- File uploads: max 10 MB (`FILE_UPLOAD_MAX_MEMORY_SIZE`).

**Required env vars:**
```
SECRET_KEY
POSTGRES_DB          (default: newlevelhub_db)
POSTGRES_USER        (default: nlh_user)
POSTGRES_PASSWORD    (default: nlh_password)
POSTGRES_HOST        (default: localhost)
POSTGRES_PORT        (default: 5432)
CELERY_BROKER_URL    (default: redis://localhost:6379/0)
CELERY_RESULT_BACKEND(default: redis://localhost:6379/0)
CORS_ALLOWED_ORIGINS (comma-separated, default: http://localhost:3000)
EMAIL_BACKEND
DEFAULT_FROM_EMAIL
FRONTEND_URL         (used in email links, default: http://localhost:3000)
ACCESS_TOKEN_LIFETIME_MINUTES  (default: 60)
REFRESH_TOKEN_LIFETIME_DAYS    (default: 7)
# MinIO / S3 object storage (set USE_MINIO=True to activate)
USE_MINIO                (default: False)
MINIO_ENDPOINT           (default: http://minio:9000; use http://localhost:9000 outside Docker)
MINIO_ACCESS_KEY         (default: minioadmin)
MINIO_SECRET_KEY         (default: minioadmin)
MINIO_BUCKET_NAME        (default: nlh-media)
```

---

## Testing

- **Framework:** pytest + pytest-django; config in `setup.cfg` (`[tool:pytest]`)
- **Settings module:** `config.settings.local` (set in `setup.cfg`)
- **Test DB:** hardcoded in `conftest.py` — `test_newlevelhub` on `localhost:5432`, user `test_user`, password `test_password`
- **Coverage:** `apps/` directory; excludes migrations, test files, `__init__.py`, `admin.py`, `apps.py`
- **Coverage reports:** terminal (missing lines) + HTML in `htmlcov/`

Existing tests: `apps/core/tests/test_permissions.py` — uses `unittest.mock` + `APIRequestFactory`; no DB access required (all mocked).

**Test writing conventions:**
- Permission/mixin tests: use `MagicMock` users and `APIRequestFactory` — no `@pytest.mark.django_db` needed.
- Integration tests: use `@pytest.mark.django_db` and DRF's `APIClient`.
- Always verify: unauthenticated → 401, guest → 403, wrong company → 403 or empty queryset.

---

## Code Conventions

1. **ViewSets** in `apps/<app>/views.py` — use `ModelViewSet` for CRUD; function-based `@api_view` for one-off endpoints.
2. **Serializers** in `apps/<app>/serializers.py` — keep thin; no business logic; use `read_only_fields` on `ModelSerializer`.
3. **URLs** in `apps/<app>/urls.py` registered via DRF routers; included in `config/urls.py`.
4. **Filters** in `apps/<app>/filters.py` using `django-filter` `FilterSet` (only `bookings` has this so far).
5. **Celery tasks** in `apps/<app>/tasks.py` (many are TODO stubs).
6. **New models** must extend `TimeStampedModel`; add `SoftDeleteModel` when soft-delete is needed.
7. **Company FK** on all tenant-scoped models: `company = models.ForeignKey('companies.Company', ...)`.
8. **OpenAPI** — annotate all views with `@extend_schema` (function views) or `@extend_schema_view` (class views); always set `tags=[...]`.
9. **Inline permission logic is forbidden** — always use classes from `apps.core.permissions`.

---

## CI/CD

- **CI** (`.github/workflows/ci.yml`): on PR to `main` — flake8, pytest + coverage, Docker build & push (tagged with ticket key + `latest`), Slack notify.
- **CD** (`.github/workflows/cd.yml`): on push to `main` — SSH to prod, Docker pull, `docker compose up -d --force-recreate`, migrations auto-run.

---

## Known TODOs

- Email verification flow (endpoint stub exists; `EmailVerificationToken` model exists)
- Password reset flow (endpoint stub exists; `PasswordResetToken` model exists)
- `InviteRegistrationSerializer` — link user to company from invite token, set `role='employee'`
- QR code generation for guest passes (`qrcode` package installed)
- Booking conflict validation
- Tariff/plan limit enforcement (employees per company, boards, storage)
- Analytics dashboards (views exist but return placeholder data)
- Celery tasks: email notifications, reminders, auto-cancel bookings, deactivate company members
- WIP limits in CRM columns
- Recurring bookings logic
- `CompanyViewSet.deactivate` action — deactivate all company members on company deactivation
