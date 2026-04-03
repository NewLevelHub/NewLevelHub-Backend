# Manual Test Plan — NewLevelHub Authentication & Permissions

**Version:** 1.0
**Date:** 2026-04-03
**Author:** QA / API Tester
**Scope:** Login flow, permission enforcement, edge cases, UI validation

---

## Table of Contents

1. [Environment](#1-environment)
2. [Prerequisites](#2-prerequisites)
3. [Positive Scenario — Successful Login](#3-positive-scenario--successful-login)
4. [Negative Scenario — Wrong Password](#4-negative-scenario--wrong-password)
5. [Edge Case — Account Lockout](#5-edge-case--account-lockout)
6. [UI Validation — Empty Fields](#6-ui-validation--empty-fields)
7. [API Permission Matrix](#7-api-permission-matrix)
8. [Running the Automated Tests](#8-running-the-automated-tests)
9. [Pass / Fail Criteria](#9-pass--fail-criteria)

---

## 1. Environment

| Parameter | Value |
|---|---|
| Staging URL | `https://staging.myapp.com` |
| Local URL | `http://localhost:8000` |
| API base path | `/api/v1` |
| Test account (basic) | `test@example.com` / `Password123` |
| Superadmin seed | `admin@nlh.test` / `Admin123!` |
| Company Admin seed | `ca@nlh.test` / `Admin123!` |
| Employee seed | `emp@nlh.test` / `Admin123!` |
| Guest seed | `guest@nlh.test` / `Admin123!` |
| Browser | Chrome 124+ (or Firefox 125+) |
| API client | curl / Postman / Bruno |

> Before running any test case, ensure the server is running and test users exist.
> Seed users by executing: `python manage.py shell < scripts/create_test_users.py`

---

## 2. Prerequisites

- [ ] Backend server is running and reachable at the target URL
- [ ] Database migrations are applied (`python manage.py migrate`)
- [ ] Test users have been seeded (`scripts/create_test_users.py`)
- [ ] `requests` library is installed (`pip install requests`)

---

## 3. Positive Scenario — Successful Login

**Goal:** Verify that a user with correct credentials is authenticated and redirected.

| Step | Action | Expected Result |
|---|---|---|
| 3.1 | Open `/login` in the browser | Login page renders with email and password fields |
| 3.2 | Enter `test@example.com` in the email field | Field accepts input |
| 3.3 | Enter `Password123` in the password field | Field accepts input and masks characters |
| 3.4 | Click the "Войти" button | Form submits |
| 3.5 | Observe the response | Browser redirects to `/dashboard`; JWT token is stored in an httpOnly cookie; no token visible in `localStorage` or URL |
| 3.6 | Inspect Network tab — `POST /api/v1/auth/login/` | Response status `200 OK`; body contains `{ "user": {...}, "tokens": { "access": "...", "refresh": "..." } }` |
| 3.7 | Navigate to `/api/v1/auth/me/` with the access token | Response `200 OK` with the logged-in user's profile data |

**Pass criteria:** Redirect to `/dashboard` occurs, token is set, `/auth/me/` returns the correct user.

---

## 4. Negative Scenario — Wrong Password

**Goal:** Verify that incorrect credentials are rejected with a clear error message.

| Step | Action | Expected Result |
|---|---|---|
| 4.1 | Open `/login` | Login page renders |
| 4.2 | Enter `test@example.com` in the email field | Field accepts input |
| 4.3 | Enter `WrongPassword!` in the password field | Field accepts input |
| 4.4 | Click "Войти" | Form submits |
| 4.5 | Observe the UI | Error message appears: "Неверный email или пароль"; browser stays on `/login`; no redirect |
| 4.6 | Inspect Network tab — `POST /api/v1/auth/login/` | Response status `400 Bad Request` or `401 Unauthorized` |

**Pass criteria:** No redirect occurs; error message is visible on the login page.

---

## 5. Edge Case — Account Lockout

**Goal:** Verify that repeated failed login attempts trigger a lockout mechanism.

> Continue from step 4 (one failed attempt already recorded).

| Step | Action | Expected Result |
|---|---|---|
| 5.1 | Repeat step 4 (wrong password) 4 more times | Each attempt shows "Неверный email или пароль" |
| 5.2 | On the 5th failed attempt total | UI displays a lockout message (e.g., "Аккаунт временно заблокирован") along with a countdown timer showing 15 minutes |
| 5.3 | Inspect Network tab on the 5th attempt | Response status `429 Too Many Requests` |
| 5.4 | Try to log in with the CORRECT password immediately | Login still rejected; lockout timer is active |
| 5.5 | Wait 15 minutes and retry with correct credentials | Login succeeds; user is redirected to `/dashboard` |

**Pass criteria:** Lockout triggers after 5 failed attempts; timer shows 15 minutes; correct credentials are also blocked during lockout.

---

## 6. UI Validation — Empty Fields

**Goal:** Verify the login form disables submission when fields are empty.

| Step | Action | Expected Result |
|---|---|---|
| 6.1 | Open `/login` with a fresh session | Login page renders; both fields are empty |
| 6.2 | Do not fill any field | "Войти" button is disabled (greyed out, `disabled` attribute present in DOM) |
| 6.3 | Fill only the email field | "Войти" button remains disabled |
| 6.4 | Clear the email field and fill only the password field | "Войти" button remains disabled |
| 6.5 | Fill both fields with any non-empty values | "Войти" button becomes active and clickable |
| 6.6 | Clear both fields again | "Войти" button returns to disabled state |

**Pass criteria:** Button is disabled whenever either field is empty; button is enabled when both fields have content.

---

## 7. API Permission Matrix

The table below shows the expected HTTP status code for each role and endpoint combination.

> Roles are assigned at user creation. The `unauth` column represents requests sent without any Authorization header.

### 7.1 Endpoint Definitions

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/v1/auth/me/` | GET | Retrieve current user's profile |
| `GET /api/v1/companies/` | GET | List companies |
| `POST /api/v1/companies/` | POST | Create a new company |
| `GET /api/v1/auth/users/` | GET | List all platform users |

### 7.2 Permission Matrix

| Endpoint | Method | superadmin | company_admin | employee | guest | unauth |
|---|---|:---:|:---:|:---:|:---:|:---:|
| `/auth/me/` | GET | 200 | 200 | 200 | 200 | 401 |
| `/companies/` | GET | 200 | 200 | 200 | 200 | 401 |
| `/companies/` | POST | 201 | 403 | 403 | 403 | 401 |
| `/auth/users/` | GET | 200 | 403 | 403 | 403 | 401 |

> **Important notes on POST /companies/:**
> The `create` action is protected by `IsSuperAdmin`. Only `superadmin` can create companies.
> `company_admin` receives `403 Forbidden`, not `201`.

> **Important notes on GET /auth/users/:**
> The `UserListView` is protected by `IsSuperAdmin`. Only `superadmin` can list all users.
> `company_admin` receives `403 Forbidden`, not `200`.

### 7.3 Manual Verification Steps

For each cell in the matrix above, perform the following:

1. Obtain a JWT access token for the target role by calling `POST /api/v1/auth/login/`.
2. Make the request with `Authorization: Bearer <token>` (or without any header for `unauth`).
3. Verify the response status code matches the value in the table.
4. For `403` responses, confirm the response body contains `{"detail": "You do not have permission to perform this action."}`.
5. For `401` responses, confirm the response body contains `{"detail": "Authentication credentials were not provided."}`.

**Example curl commands:**

```bash
# Get token for superadmin
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@nlh.test","password":"Admin123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['tokens']['access'])")

# GET /auth/me/ — expect 200
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/auth/me/

# POST /companies/ as superadmin — expect 201
curl -s -o /dev/null -w "%{http_code}" -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Manual Test Company"}' \
  http://localhost:8000/api/v1/companies/

# GET /auth/users/ without token — expect 401
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8000/api/v1/auth/users/
```

---

## 8. Running the Automated Tests

The script `scripts/test_permissions_api.py` covers the entire permission matrix automatically.

### 8.1 Prerequisites

```bash
# Install the requests library if not already present
pip install requests

# Ensure the server is running
python manage.py runserver

# Seed test users (idempotent — safe to run multiple times)
python manage.py shell < scripts/create_test_users.py
```

### 8.2 Run Against Local Server

```bash
# From the project root directory
python scripts/test_permissions_api.py
```

### 8.3 Run Against a Different Server

```bash
python scripts/test_permissions_api.py --base-url https://staging.myapp.com/api/v1
```

### 8.4 Interpreting Output

```
NewLevelHub — API Permission Matrix Test
Target: http://localhost:8000/api/v1
----------------------------------------------------------------------

Authenticating test users...
  [OK] superadmin       admin@nlh.test
  [OK] company_admin    ca@nlh.test
  [OK] employee         emp@nlh.test
  [OK] guest            guest@nlh.test

GET /auth/me/
  PASS  superadmin -> GET /auth/me/          expected=200  got=200
  PASS  company_admin -> GET /auth/me/       expected=200  got=200
  ...

----------------------------------------------------------------------
Results: 20 passed, 0 failed / 20 total
----------------------------------------------------------------------
```

- `PASS` — the actual HTTP status matches the expected status.
- `FAIL` — the actual HTTP status does not match; the actual code is printed in red.
- Exit code `0` means all tests passed; exit code `1` means at least one test failed (suitable for CI integration).

### 8.5 CI Integration Example

```yaml
# .github/workflows/api-permissions.yml (example)
- name: Run permission matrix tests
  run: python scripts/test_permissions_api.py --base-url ${{ env.STAGING_URL }}
```

---

## 9. Pass / Fail Criteria

| Criteria | Pass | Fail |
|---|---|---|
| Correct credentials grant access | Redirect to `/dashboard`, token issued | No redirect, no token |
| Wrong credentials are rejected | HTTP 400/401, error message shown | HTTP 200, unexpected access |
| Lockout after 5 bad attempts | HTTP 429 on 5th attempt, 15-min timer | No lockout, unlimited attempts |
| Empty-field form validation | "Войти" button disabled | Button enabled with empty fields |
| All permission matrix cells | Status matches the matrix table | Any cell returns a different status |
| Automated script | All tests PASS, exit code 0 | Any FAIL, exit code 1 |

---

*This plan should be re-executed after any change to authentication logic, permission classes, or role definitions.*
