# NewLevelHub Backend — Тест-кейсы для ручного тестирования (Postman)

## Настройка окружения

```
Base URL: http://localhost:8000
Content-Type: application/json
Authorization: Bearer {{access_token}}
```

### Переменные окружения Postman (создать в Environment)

| Переменная | Описание | Пример значения |
|---|---|---|
| `base_url` | Базовый URL сервера | `http://localhost:8000` |
| `access_token` | JWT access token (обновлять после логина) | `eyJhbGciOiJIUzI1...` |
| `refresh_token` | JWT refresh token | `eyJhbGciOiJIUzI1...` |
| `company_id` | ID текущей компании | `1` |
| `user_id` | ID текущего пользователя | `1` |
| `other_company_id` | ID чужой компании (для проверки изоляции) | `2` |
| `booking_id` | ID бронирования | `1` |
| `resource_id` | ID ресурса (стол, комната) | `1` |
| `board_id` | ID CRM доски | `1` |
| `task_id` | ID задачи | `1` |
| `column_id` | ID колонки | `1` |
| `folder_id` | ID папки в Storage | `1` |
| `file_id` | ID файла в Storage | `1` |
| `leave_id` | ID заявки на отпуск | `1` |
| `pass_id` | ID гостевого пропуска | `1` |
| `notification_id` | ID уведомления | `1` |
| `announcement_id` | ID объявления | `1` |
| `invitation_id` | ID приглашения | `1` |

---

## Порядок первого запуска

Перед тестированием модулей выполни шаги в таком порядке:

1. TC-001 — Зарегистрироваться как `guest`
2. TC-003 — Залогиниться, сохранить `access_token` и `refresh_token`
3. TC-021 — Залогиниться суперадмином (нужен заранее созданный через `python manage.py createsuperuser`)
4. TC-031 — Суперадмин создаёт компанию, сохранить `company_id`
5. TC-044 — Суперадмин создаёт ресурс, сохранить `resource_id`
6. TC-051 — Company admin создаёт бронирование, сохранить `booking_id`
7. TC-071 — Создать CRM доску, сохранить `board_id` и `column_id`
8. TC-081 — Создать задачу, сохранить `task_id`

---

## 1. Системные эндпоинты (Health Check)

### TC-001-SYS: Health Check — позитивный сценарий

**Метод:** GET
**URL:** `{{base_url}}/api/v1/health/`
**Роль:** любой (без токена)
**Ожидаемый результат:** 200

**Headers:** (без авторизации)

**Ожидаемый Response:**
```json
{
  "status": "ok"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `status` равно `"ok"`

---

### TC-002-SYS: Ping

**Метод:** GET
**URL:** `{{base_url}}/api/v1/ping/`
**Роль:** любой (без токена)
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит поле с подтверждением

---

## 2. Аутентификация (Auth)

### TC-003-AUTH: Регистрация — позитивный сценарий

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/register/`
**Роль:** анонимный
**Ожидаемый результат:** 201

**Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "SecurePass123!",
  "first_name": "Иван",
  "last_name": "Тестов"
}
```

**Ожидаемый Response:**
```json
{
  "user": {
    "id": 1,
    "email": "newuser@example.com",
    "first_name": "Иван",
    "last_name": "Тестов",
    "role": "guest",
    "company": null
  },
  "tokens": {
    "access": "eyJ...",
    "refresh": "eyJ..."
  }
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `user.role` равно `"guest"`
- [ ] Поле `user.company` равно `null`
- [ ] Поля `tokens.access` и `tokens.refresh` присутствуют и не пустые
- [ ] Сохранить `tokens.access` в `{{access_token}}`
- [ ] Сохранить `tokens.refresh` в `{{refresh_token}}`

---

### TC-004-AUTH: Регистрация — дублирование email

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/register/`
**Роль:** анонимный
**Ожидаемый результат:** 400

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "SecurePass123!",
  "first_name": "Иван",
  "last_name": "Тестов"
}
```

**Проверить:**
- [ ] Статус код 400
- [ ] Ответ содержит описание ошибки по полю `email`

---

### TC-005-AUTH: Регистрация — пустые обязательные поля

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/register/`
**Роль:** анонимный
**Ожидаемый результат:** 400

**Request Body:**
```json
{
  "email": "",
  "password": "",
  "first_name": "",
  "last_name": ""
}
```

**Проверить:**
- [ ] Статус код 400
- [ ] Ответ содержит ошибки валидации

---

### TC-006-AUTH: Логин — позитивный сценарий

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/login/`
**Роль:** анонимный
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "SecurePass123!"
}
```

**Ожидаемый Response:**
```json
{
  "user": {
    "id": 1,
    "email": "newuser@example.com",
    "role": "guest"
  },
  "tokens": {
    "access": "eyJ...",
    "refresh": "eyJ..."
  }
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поля `tokens.access` и `tokens.refresh` присутствуют
- [ ] Сохранить `tokens.access` → `{{access_token}}`
- [ ] Сохранить `tokens.refresh` → `{{refresh_token}}`

---

### TC-007-AUTH: Логин — неверный пароль

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/login/`
**Роль:** анонимный
**Ожидаемый результат:** 400

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "WrongPassword"
}
```

**Проверить:**
- [ ] Статус код 400
- [ ] Ответ содержит сообщение об ошибке

---

### TC-008-AUTH: Обновление токена (refresh)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/token/refresh/`
**Роль:** анонимный (нужен refresh_token)
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "refresh": "{{refresh_token}}"
}
```

**Ожидаемый Response:**
```json
{
  "access": "eyJ...",
  "refresh": "eyJ..."
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `access` присутствует и не пустое
- [ ] Поле `refresh` присутствует (ротация токена)
- [ ] Обновить `{{access_token}}` новым значением

---

### TC-009-AUTH: Обновление токена — невалидный токен

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/token/refresh/`
**Ожидаемый результат:** 401

**Request Body:**
```json
{
  "refresh": "invalid_token_string"
}
```

**Проверить:**
- [ ] Статус код 401

---

### TC-010-AUTH: Получение профиля (me)

**Метод:** GET
**URL:** `{{base_url}}/api/v1/auth/me/`
**Роль:** любой авторизованный
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "email": "newuser@example.com",
  "first_name": "Иван",
  "last_name": "Тестов",
  "role": "guest",
  "phone": "",
  "position": "",
  "company": null,
  "is_email_verified": false
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `email` соответствует текущему пользователю
- [ ] Сохранить `id` → `{{user_id}}`

---

### TC-011-AUTH: Получение профиля — без токена

**Метод:** GET
**URL:** `{{base_url}}/api/v1/auth/me/`
**Роль:** анонимный
**Ожидаемый результат:** 401

**Headers:** (без Authorization)

**Проверить:**
- [ ] Статус код 401

---

### TC-012-AUTH: Обновление профиля

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/auth/me/update/`
**Роль:** любой авторизованный
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "phone": "+77001234567",
  "position": "Разработчик"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "phone": "+77001234567",
  "position": "Разработчик"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поля `phone` и `position` обновлены

---

### TC-013-AUTH: Смена пароля

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/me/password/`
**Роль:** любой авторизованный
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
```

**Request Body:**
```json
{
  "current_password": "SecurePass123!",
  "new_password": "NewSecurePass456!"
}
```

**Ожидаемый Response:**
```json
{
  "detail": "Password changed"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] После смены — старый токен продолжает работать до истечения
- [ ] Логин старым паролем возвращает 400

---

### TC-014-AUTH: Смена пароля — неверный текущий пароль

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/me/password/`
**Роль:** авторизованный
**Ожидаемый результат:** 400

**Request Body:**
```json
{
  "current_password": "WrongCurrentPassword",
  "new_password": "NewSecurePass456!"
}
```

**Проверить:**
- [ ] Статус код 400

---

### TC-015-AUTH: Выход (logout)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/logout/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
```

**Request Body:**
```json
{
  "refresh": "{{refresh_token}}"
}
```

**Ожидаемый Response:**
```json
{
  "detail": "Logged out"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Повторный запрос `token/refresh/` с тем же refresh token возвращает 401

---

### TC-016-AUTH: Выход — без токена авторизации

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/logout/`
**Роль:** анонимный
**Ожидаемый результат:** 401

**Проверить:**
- [ ] Статус код 401

---

### TC-017-AUTH: Список пользователей — суперадмин

**Метод:** GET
**URL:** `{{base_url}}/api/v1/auth/users/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{superadmin_access_token}}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит список пользователей
- [ ] Доступна фильтрация по `?role=guest`

---

### TC-018-AUTH: Список пользователей — не суперадмин

**Метод:** GET
**URL:** `{{base_url}}/api/v1/auth/users/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-019-AUTH: Запрос сброса пароля

**Метод:** POST
**URL:** `{{base_url}}/api/v1/auth/password/reset/`
**Роль:** анонимный
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "email": "newuser@example.com"
}
```

**Ожидаемый Response:**
```json
{
  "detail": "If an account exists, a reset link has been sent"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Одинаковый ответ для существующего и несуществующего email (защита от перебора)

---

## 3. Компании (Companies)

> Примечание: создание и удаление компаний — только superadmin. Чтение и обновление — superadmin или company_admin своей компании.

### TC-020-CMP: Создание компании — суперадмин

**Метод:** POST
**URL:** `{{base_url}}/api/v1/companies/`
**Роль:** superadmin
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{superadmin_access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "ООО Тест Компания",
  "description": "Тестовая компания для разработчиков",
  "contact_email": "admin@testcompany.kz",
  "contact_phone": "+77071234567",
  "floor": "3",
  "office_number": "301",
  "plan": "standard"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "name": "ООО Тест Компания",
  "plan": "standard",
  "is_active": true,
  "max_employees": 10,
  "storage_limit_gb": 5
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `is_active` равно `true`
- [ ] Сохранить `id` → `{{company_id}}`

---

### TC-021-CMP: Создание компании — не суперадмин

**Метод:** POST
**URL:** `{{base_url}}/api/v1/companies/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Headers:**
```
Authorization: Bearer {{company_admin_token}}
```

**Проверить:**
- [ ] Статус код 403

---

### TC-022-CMP: Список компаний — суперадмин видит все

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Список содержит все компании (не фильтруется по компании)

---

### TC-023-CMP: Список компаний — company_admin видит только свою

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Список содержит только одну запись — свою компанию

---

### TC-024-CMP: Получение деталей компании

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `id` совпадает с `{{company_id}}`

---

### TC-025-CMP: Получение чужой компании — company_admin

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/{{other_company_id}}/`
**Роль:** company_admin
**Ожидаемый результат:** 404

**Проверить:**
- [ ] Статус код 404 (изоляция данных: чужая компания не входит в queryset)

---

### TC-026-CMP: Обновление компании — company_admin

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "description": "Обновлённое описание компании",
  "contact_phone": "+77079999999"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `description` обновлено

---

### TC-027-CMP: Удаление компании — суперадмин

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/`
**Роль:** superadmin
**Ожидаемый результат:** 204

**Проверить:**
- [ ] Статус код 204
- [ ] Повторный GET возвращает 404

---

### TC-028-CMP: Удаление компании — company_admin

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-029-CMP: Настройки компании — получение

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/settings/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "vacation_days_per_year": 24,
  "onboarding_enabled": true,
  "brand_primary_color": "",
  "custom_task_categories": [],
  "custom_labels": []
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит все поля настроек

---

### TC-030-CMP: Настройки компании — обновление

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/settings/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "vacation_days_per_year": 28,
  "brand_primary_color": "#4F46E5",
  "onboarding_enabled": false
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `vacation_days_per_year` равно `28`

---

### TC-031-CMP: Список участников компании

**Метод:** GET
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/members/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Список содержит только активных пользователей данной компании

---

### TC-032-CMP: Деактивация компании — суперадмин

**Метод:** POST
**URL:** `{{base_url}}/api/v1/companies/{{company_id}}/deactivate/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "Company deactivated"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] GET компании возвращает `is_active: false`

---

### TC-033-CMP: Создание приглашения

**Метод:** POST
**URL:** `{{base_url}}/api/v1/companies/invitations/`
**Роль:** company_admin
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "email": "employee@example.com",
  "role": "employee",
  "company": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `token` присутствует
- [ ] Сохранить `id` → `{{invitation_id}}`

---

### TC-034-CMP: Отзыв приглашения

**Метод:** POST
**URL:** `{{base_url}}/api/v1/companies/invitations/{{invitation_id}}/revoke/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "Invitation revoked"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `is_used` в деталях приглашения равно `true`

---

## 4. Бронирования — Ресурсы (Bookings / Resources)

### TC-035-RES: Список ресурсов — авторизованный сотрудник

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/`
**Роль:** employee (company member)
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Список содержит только активные ресурсы (`is_active=true`)

---

### TC-036-RES: Список ресурсов — фильтрация по типу

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/?resource_type=meeting_room`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все записи в ответе имеют `resource_type: "meeting_room"`

---

### TC-037-RES: Список ресурсов — без токена

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/`
**Роль:** анонимный
**Ожидаемый результат:** 401

**Проверить:**
- [ ] Статус код 401

---

### TC-038-RES: Детали ресурса

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/{{resource_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "id": 1,
  "name": "Переговорная A",
  "resource_type": "meeting_room",
  "floor": 2,
  "capacity": 8,
  "has_projector": true,
  "has_video_conf": false,
  "min_duration_minutes": 30,
  "max_duration_minutes": 480
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Все поля ресурса присутствуют

---

### TC-039-RES: Ресурс не существует

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/99999/`
**Роль:** employee
**Ожидаемый результат:** 404

**Проверить:**
- [ ] Статус код 404

---

### TC-040-RES: Создание ресурса — суперадмин

**Метод:** POST
**URL:** `{{base_url}}/api/v1/bookings/resources/`
**Роль:** superadmin
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "name": "Переговорная B",
  "resource_type": "meeting_room",
  "floor": 3,
  "zone": "Блок B",
  "capacity": 10,
  "description": "Большая переговорная",
  "has_projector": true,
  "has_video_conf": true,
  "has_whiteboard": true,
  "min_duration_minutes": 30,
  "max_duration_minutes": 480,
  "available_days": [0, 1, 2, 3, 4],
  "available_from": "08:00",
  "available_until": "20:00"
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Сохранить `id` → `{{resource_id}}`

---

### TC-041-RES: Создание ресурса — company_admin (запрещено)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/bookings/resources/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-042-RES: Расписание ресурса

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/resources/{{resource_id}}/schedule/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит список подтверждённых бронирований

---

---

## 5. Бронирования — Резервации (Bookings / Reservations)

### TC-043-BKG: Создание бронирования

**Метод:** POST
**URL:** `{{base_url}}/api/v1/bookings/reservations/`
**Роль:** employee
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "resource": 1,
  "start_time": "2026-04-10T10:00:00+06:00",
  "end_time": "2026-04-10T11:00:00+06:00",
  "description": "Встреча с командой"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "resource": 1,
  "user": 1,
  "start_time": "2026-04-10T10:00:00+06:00",
  "end_time": "2026-04-10T11:00:00+06:00",
  "status": "confirmed",
  "description": "Встреча с командой"
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `status` равно `"confirmed"`
- [ ] Сохранить `id` → `{{booking_id}}`

---

### TC-044-BKG: Создание бронирования — без токена

**Метод:** POST
**URL:** `{{base_url}}/api/v1/bookings/reservations/`
**Роль:** анонимный
**Ожидаемый результат:** 401

**Проверить:**
- [ ] Статус код 401

---

### TC-045-BKG: Список всех бронирований компании

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все записи принадлежат компании пользователя (изоляция)

---

### TC-046-BKG: Мои бронирования

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/my/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все записи принадлежат текущему пользователю

---

### TC-047-BKG: Детали бронирования

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/{{booking_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `id` совпадает с `{{booking_id}}`

---

### TC-048-BKG: Отмена бронирования

**Метод:** POST
**URL:** `{{base_url}}/api/v1/bookings/reservations/{{booking_id}}/cancel/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "reason": "Перенос встречи"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "status": "cancelled",
  "cancel_reason": "Перенос встречи"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `status` равно `"cancelled"`
- [ ] Поле `cancel_reason` содержит переданную причину

---

### TC-049-BKG: Бронирование не найдено

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/99999/`
**Роль:** employee
**Ожидаемый результат:** 404

**Проверить:**
- [ ] Статус код 404

---

### TC-050-BKG: Изоляция — бронирование другой компании

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/`
**Роль:** employee другой компании
**Ожидаемый результат:** 200 (но список пустой или без записей чужой компании)

**Проверить:**
- [ ] Статус код 200
- [ ] Бронирование `{{booking_id}}` (первой компании) отсутствует в ответе

---

### TC-051-BKG: Фильтрация бронирований по статусу

**Метод:** GET
**URL:** `{{base_url}}/api/v1/bookings/reservations/?status=confirmed`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все записи имеют `status: "confirmed"`

---

## 6. CRM — Доски (Boards)

### TC-052-CRM: Создание доски

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/boards/`
**Роль:** employee
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "Продажи Q2",
  "description": "Доска для отслеживания сделок второго квартала"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "name": "Продажи Q2",
  "description": "Доска для отслеживания сделок второго квартала",
  "is_archived": false,
  "columns": [
    {"id": 1, "name": "К выполнению", "position": 0},
    {"id": 2, "name": "В работе", "position": 1},
    {"id": 3, "name": "Готово", "position": 2}
  ]
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Автоматически созданы 3 колонки: "К выполнению", "В работе", "Готово"
- [ ] Сохранить `id` → `{{board_id}}`
- [ ] Сохранить `columns[0].id` → `{{column_id}}`

---

### TC-053-CRM: Список досок — только своей компании

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/boards/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Архивированные доски не отображаются
- [ ] Все доски принадлежат компании пользователя

---

### TC-054-CRM: Детали доски

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит вложенные колонки с задачами

---

### TC-055-CRM: Обновление доски

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "name": "Продажи Q2 — обновлено"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `name` обновлено

---

### TC-056-CRM: Архивирование доски

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/archive/`
**Роль:** employee
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "Board archived"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Доска больше не отображается в списке `GET /crm/boards/`

---

### TC-057-CRM: Удаление доски

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/`
**Роль:** employee
**Ожидаемый результат:** 204

**Проверить:**
- [ ] Статус код 204

---

### TC-058-CRM: Доска — изоляция (чужая компания)

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/`
**Роль:** employee другой компании
**Ожидаемый результат:** 404

**Проверить:**
- [ ] Статус код 404

---

### TC-059-CRM: Создание колонки в доске

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/columns/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "name": "На ревью",
  "position": 2,
  "wip_limit": 5
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `name` равно "На ревью"

---

### TC-060-CRM: Список колонок доски

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/boards/{{board_id}}/columns/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Колонки отсортированы по `position`

---

---

## 7. CRM — Задачи (Tasks)

### TC-061-CRM: Создание задачи

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/tasks/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "column": 1,
  "title": "Подготовить коммерческое предложение",
  "description": "Включить условия для корпоративных клиентов",
  "priority": "high",
  "position": 0,
  "deadline": "2026-04-30T18:00:00+06:00"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "title": "Подготовить коммерческое предложение",
  "priority": "high",
  "status": "confirmed",
  "column": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `priority` равно `"high"`
- [ ] Сохранить `id` → `{{task_id}}`

---

### TC-062-CRM: Список задач

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/tasks/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все задачи принадлежат доскам компании пользователя

---

### TC-063-CRM: Мои задачи

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/tasks/my/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все задачи назначены на текущего пользователя

---

### TC-064-CRM: Перемещение задачи (drag & drop)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/move/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "column_id": 2,
  "position": 0
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `column` обновлено на `2`
- [ ] История задачи содержит запись о перемещении

---

### TC-065-CRM: История задачи

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/history/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит записи с `action`, `old_value`, `new_value`

---

### TC-066-CRM: Обновление задачи

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "priority": "urgent",
  "assignee": 1
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `priority` обновлено

---

### TC-067-CRM: Добавление комментария к задаче

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/comments/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "text": "Нужно уточнить детали у менеджера"
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `text` совпадает с переданным
- [ ] Поле `author` соответствует текущему пользователю

---

### TC-068-CRM: Список комментариев задачи

**Метод:** GET
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/comments/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Комментарии отсортированы по `created_at` (от старых к новым)

---

### TC-069-CRM: Удаление задачи

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/crm/tasks/{{task_id}}/`
**Роль:** employee
**Ожидаемый результат:** 204

**Проверить:**
- [ ] Статус код 204

---

### TC-070-CRM: Создание метки (label)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/crm/labels/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "name": "Срочно",
  "color": "#EF4444"
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `color` содержит переданное значение

---

---

## 8. Хранилище (Storage)

### TC-071-STR: Создание папки

**Метод:** POST
**URL:** `{{base_url}}/api/v1/storage/folders/`
**Роль:** employee
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "Документы проекта",
  "scope": "company"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "name": "Документы проекта",
  "scope": "company",
  "owner": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `owner` соответствует текущему пользователю
- [ ] Сохранить `id` → `{{folder_id}}`

---

### TC-072-STR: Список папок

**Метод:** GET
**URL:** `{{base_url}}/api/v1/storage/folders/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Список содержит личные папки пользователя и папки компании с `scope="company"`

---

### TC-073-STR: Переименование папки

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/storage/folders/{{folder_id}}/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "name": "Документы проекта 2026"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `name` обновлено

---

### TC-074-STR: Удаление папки

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/storage/folders/{{folder_id}}/`
**Роль:** employee
**Ожидаемый результат:** 204

**Проверить:**
- [ ] Статус код 204

---

### TC-075-STR: Загрузка файла

**Метод:** POST
**URL:** `{{base_url}}/api/v1/storage/files/`
**Роль:** employee
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: multipart/form-data
```

**Request Body (form-data):**
```
file: [выбрать файл в Postman, тип file]
name: Отчёт за апрель
folder: 1
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "name": "Отчёт за апрель",
  "file_size": 4096,
  "content_type": "application/pdf",
  "owner": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `file_size` больше 0
- [ ] Сохранить `id` → `{{file_id}}`

---

### TC-076-STR: Список файлов

**Метод:** GET
**URL:** `{{base_url}}/api/v1/storage/files/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Поиск работает: `?search=Отчёт` возвращает только совпадения

---

### TC-077-STR: Удаление файла

**Метод:** DELETE
**URL:** `{{base_url}}/api/v1/storage/files/{{file_id}}/`
**Роль:** employee
**Ожидаемый результат:** 204

**Проверить:**
- [ ] Статус код 204

---

### TC-078-STR: Использование хранилища

**Метод:** GET
**URL:** `{{base_url}}/api/v1/storage/usage/`
**Роль:** employee
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "used_bytes": 4096,
  "limit_bytes": 5368709120,
  "used_percent": 0.0
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `used_bytes` соответствует суммарному размеру загруженных файлов
- [ ] Поле `limit_bytes` соответствует лимиту компании

---

---

## 9. HR — Заявки на отпуск (Leave Requests)

### TC-079-HR: Создание заявки на отпуск

**Метод:** POST
**URL:** `{{base_url}}/api/v1/hr/leaves/`
**Роль:** employee
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "leave_type": "vacation",
  "start_date": "2026-05-01",
  "end_date": "2026-05-10",
  "comment": "Плановый отпуск"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "leave_type": "vacation",
  "status": "pending",
  "start_date": "2026-05-01",
  "end_date": "2026-05-10",
  "user": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `status` равно `"pending"`
- [ ] Поле `user` соответствует текущему пользователю
- [ ] Сохранить `id` → `{{leave_id}}`

---

### TC-080-HR: Создание заявки — guest (запрещено)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/hr/leaves/`
**Роль:** guest
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-081-HR: Список заявок — employee видит только свои

**Метод:** GET
**URL:** `{{base_url}}/api/v1/hr/leaves/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все заявки принадлежат компании пользователя (изоляция по компании через CompanyIsolationMixin)

---

### TC-082-HR: Фильтрация заявок по статусу

**Метод:** GET
**URL:** `{{base_url}}/api/v1/hr/leaves/?status=pending`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все записи имеют `status: "pending"`

---

### TC-083-HR: Одобрение заявки на отпуск

**Метод:** POST
**URL:** `{{base_url}}/api/v1/hr/leaves/{{leave_id}}/review/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "status": "approved",
  "review_comment": "Согласовано"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "status": "approved",
  "review_comment": "Согласовано",
  "reviewed_by": 2
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `status` равно `"approved"`
- [ ] Поле `reviewed_by` содержит ID company_admin

---

### TC-084-HR: Одобрение заявки — employee (запрещено)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/hr/leaves/{{leave_id}}/review/`
**Роль:** employee
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-085-HR: Отклонение заявки

**Метод:** POST
**URL:** `{{base_url}}/api/v1/hr/leaves/{{leave_id}}/review/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "status": "rejected",
  "review_comment": "В этот период много задач"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `status` равно `"rejected"`

---

### TC-086-HR: Баланс отпуска

**Метод:** GET
**URL:** `{{base_url}}/api/v1/hr/leaves/balance/`
**Роль:** employee
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "total_days": 24,
  "used_days": 0
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `total_days` больше 0

---

---

## 10. Контроль доступа — Гостевые пропуска (Access)

### TC-087-ACC: Создание гостевого пропуска

**Метод:** POST
**URL:** `{{base_url}}/api/v1/access/passes/`
**Роль:** company_admin
**Ожидаемый результат:** 201

**Headers:**
```
Authorization: Bearer {{company_admin_token}}
Content-Type: application/json
```

**Request Body:**
```json
{
  "guest_name": "Алибек Сейткали",
  "guest_email": "alibek@external.kz",
  "guest_phone": "+77071112233",
  "visit_purpose": "Деловая встреча",
  "usage_type": "single",
  "valid_from": "2026-04-07T09:00:00+06:00",
  "valid_until": "2026-04-07T18:00:00+06:00"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "guest_name": "Алибек Сейткали",
  "guest_email": "alibek@external.kz",
  "status": "active",
  "usage_type": "single",
  "qr_code": "uuid-here",
  "valid_from": "2026-04-07T09:00:00+06:00",
  "valid_until": "2026-04-07T18:00:00+06:00"
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `status` равно `"active"`
- [ ] Поле `qr_code` присутствует и является UUID
- [ ] Сохранить `id` → `{{pass_id}}`
- [ ] Сохранить `qr_code` для теста TC-090-ACC

---

### TC-088-ACC: Создание пропуска — employee (запрещено)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/access/passes/`
**Роль:** employee
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-089-ACC: Список пропусков

**Метод:** GET
**URL:** `{{base_url}}/api/v1/access/passes/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все пропуска принадлежат компании администратора
- [ ] Фильтрация по статусу: `?status=active`

---

### TC-090-ACC: Валидация QR-кода

**Метод:** POST
**URL:** `{{base_url}}/api/v1/access/validate/`
**Роль:** авторизованный (рецепция)
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "qr_code": "uuid-из-tc-087"
}
```

**Ожидаемый Response:**
```json
{
  "valid": true,
  "guest_name": "Алибек Сейткали",
  "visit_purpose": "Деловая встреча",
  "created_by": "Имя Фамилия"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `valid` равно `true`
- [ ] После валидации однократного пропуска повторный запрос возвращает `valid: false`

---

### TC-091-ACC: Валидация несуществующего QR-кода

**Метод:** POST
**URL:** `{{base_url}}/api/v1/access/validate/`
**Роль:** авторизованный
**Ожидаемый результат:** 404

**Request Body:**
```json
{
  "qr_code": "00000000-0000-0000-0000-000000000000"
}
```

**Проверить:**
- [ ] Статус код 404
- [ ] Поле `valid` равно `false`

---

### TC-092-ACC: Отзыв пропуска

**Метод:** POST
**URL:** `{{base_url}}/api/v1/access/passes/{{pass_id}}/revoke/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "Pass revoked"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Валидация QR после отзыва возвращает `valid: false`

---

### TC-093-ACC: Лог доступа — суперадмин

**Метод:** GET
**URL:** `{{base_url}}/api/v1/access/logs/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Логи отсортированы по убыванию `created_at`

---

### TC-094-ACC: Лог доступа — company_admin (запрещено)

**Метод:** GET
**URL:** `{{base_url}}/api/v1/access/logs/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

---

## 11. Сервисы здания (Services)

### TC-095-SRV: Список этажей

**Метод:** GET
**URL:** `{{base_url}}/api/v1/services/floors/`
**Роль:** любой авторизованный
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200

---

### TC-096-SRV: Создание этажа — суперадмин

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/floors/`
**Роль:** superadmin
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "number": 3,
  "name": "Третий этаж"
}
```

**Проверить:**
- [ ] Статус код 201

---

### TC-097-SRV: Создание сервисной заявки

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/requests/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "request_type": "cleaning",
  "urgency": "normal",
  "floor": 2,
  "description": "Нужна уборка в переговорной A"
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "request_type": "cleaning",
  "urgency": "normal",
  "status": "new",
  "user": 1
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `user` соответствует текущему пользователю

---

### TC-098-SRV: Быстрая заявка на уборку

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/requests/cleaning/`
**Роль:** employee
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "floor": 2
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `request_type` равно `"cleaning"`

---

### TC-099-SRV: Мои сервисные заявки

**Метод:** GET
**URL:** `{{base_url}}/api/v1/services/requests/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Все заявки принадлежат текущему пользователю

---

### TC-100-SRV: Обновление статуса заявки — суперадмин

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/services/requests/1/update-status/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "status": "in_progress"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `status` обновлено

---

### TC-101-SRV: Оценка сервисной заявки

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/requests/1/rate/`
**Роль:** employee
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "rating": 5
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Оценка вне диапазона 1-5 возвращает 400

---

### TC-102-SRV: Оценка вне диапазона

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/requests/1/rate/`
**Роль:** employee
**Ожидаемый результат:** 400

**Request Body:**
```json
{
  "rating": 10
}
```

**Проверить:**
- [ ] Статус код 400

---

### TC-103-SRV: Создание объявления — company_admin

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/announcements/`
**Роль:** company_admin
**Ожидаемый результат:** 201

**Request Body:**
```json
{
  "title": "Плановое обслуживание",
  "body": "С 10:00 до 12:00 будут проводиться технические работы",
  "scope": "company",
  "category": "maintenance",
  "is_pinned": false
}
```

**Ожидаемый Response:**
```json
{
  "id": 1,
  "title": "Плановое обслуживание",
  "scope": "company",
  "author": 2,
  "is_pinned": false
}
```

**Проверить:**
- [ ] Статус код 201
- [ ] Поле `author` соответствует текущему пользователю
- [ ] Сохранить `id` → `{{announcement_id}}`

---

### TC-104-SRV: Список объявлений

**Метод:** GET
**URL:** `{{base_url}}/api/v1/services/announcements/`
**Роль:** employee
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Видны объявления `scope="building"` и `scope="company"` (своей компании)
- [ ] Объявления чужой компании не видны

---

### TC-105-SRV: Создание объявления — employee (запрещено)

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/announcements/`
**Роль:** employee
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-106-SRV: Отметить объявление как прочитанное

**Метод:** POST
**URL:** `{{base_url}}/api/v1/services/announcements/{{announcement_id}}/read/`
**Роль:** employee
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "Marked as read"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Повторный вызов также возвращает 200 (идемпотентность через get_or_create)

---

---

## 12. Уведомления (Notifications)

### TC-107-NTF: Список уведомлений

**Метод:** GET
**URL:** `{{base_url}}/api/v1/notifications/`
**Роль:** любой авторизованный
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{access_token}}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Все уведомления принадлежат текущему пользователю

---

### TC-108-NTF: Количество непрочитанных

**Метод:** GET
**URL:** `{{base_url}}/api/v1/notifications/unread-count/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "count": 3
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `count` является числом >= 0

---

### TC-109-NTF: Отметить уведомление как прочитанное

**Метод:** POST
**URL:** `{{base_url}}/api/v1/notifications/{{notification_id}}/read/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "id": 1,
  "is_read": true,
  "read_at": "2026-04-06T10:00:00+06:00"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `is_read` равно `true`
- [ ] Поле `read_at` не пустое

---

### TC-110-NTF: Отметить все уведомления прочитанными

**Метод:** POST
**URL:** `{{base_url}}/api/v1/notifications/read-all/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "detail": "All marked as read"
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] После запроса `unread-count` возвращает `{"count": 0}`

---

### TC-111-NTF: Настройки уведомлений — получение

**Метод:** GET
**URL:** `{{base_url}}/api/v1/notifications/preferences/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Настройки создаются автоматически если их нет (get_or_create)

---

### TC-112-NTF: Настройки уведомлений — обновление

**Метод:** PATCH
**URL:** `{{base_url}}/api/v1/notifications/preferences/`
**Роль:** авторизованный
**Ожидаемый результат:** 200

**Request Body:**
```json
{
  "email_notifications": false,
  "push_notifications": true
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Поле `email_notifications` обновлено

---

### TC-113-NTF: Уведомления без токена

**Метод:** GET
**URL:** `{{base_url}}/api/v1/notifications/`
**Роль:** анонимный
**Ожидаемый результат:** 401

**Проверить:**
- [ ] Статус код 401

---

---

## 13. Аналитика (Analytics)

### TC-114-ANL: Дашборд суперадмина

**Метод:** GET
**URL:** `{{base_url}}/api/v1/analytics/superadmin/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Headers:**
```
Authorization: Bearer {{superadmin_access_token}}
```

**Ожидаемый Response:**
```json
{
  "total_companies": 5,
  "active_companies": 4,
  "total_users": 42,
  "active_users_7d": 15,
  "bookings_today": 8,
  "guests_today": 2,
  "open_service_requests": 3
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Все поля присутствуют в ответе
- [ ] Значения являются числами

---

### TC-115-ANL: Дашборд суперадмина — company_admin (запрещено)

**Метод:** GET
**URL:** `{{base_url}}/api/v1/analytics/superadmin/`
**Роль:** company_admin
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-116-ANL: Дашборд company_admin

**Метод:** GET
**URL:** `{{base_url}}/api/v1/analytics/company/`
**Роль:** company_admin
**Ожидаемый результат:** 200

**Ожидаемый Response:**
```json
{
  "employee_count": 10,
  "active_employees_7d": 7,
  "bookings_this_month": 25,
  "storage_used_bytes": 1073741824,
  "storage_limit_bytes": 5368709120,
  "active_tasks": 12,
  "guest_visits_this_month": 4
}
```

**Проверить:**
- [ ] Статус код 200
- [ ] Все поля присутствуют и содержат числа
- [ ] `storage_used_bytes` <= `storage_limit_bytes`

---

### TC-117-ANL: Дашборд company_admin — employee (запрещено)

**Метод:** GET
**URL:** `{{base_url}}/api/v1/analytics/company/`
**Роль:** employee
**Ожидаемый результат:** 403

**Проверить:**
- [ ] Статус код 403

---

### TC-118-ANL: Статистика использования ресурсов — суперадмин

**Метод:** GET
**URL:** `{{base_url}}/api/v1/analytics/resources/`
**Роль:** superadmin
**Ожидаемый результат:** 200

**Проверить:**
- [ ] Статус код 200
- [ ] Ответ содержит сгруппированную статистику по типу ресурса

---

---

## 14. Порядок тестирования сценария "с нуля"

Следующий сценарий позволяет протестировать полный рабочий процесс последовательно:

### Этап 1 — Инициализация

1. **TC-001-SYS** — Проверить Health Check (сервер работает)
2. Залогиниться суперадмином через `python manage.py createsuperuser` или Django shell
3. **TC-006-AUTH** — Войти суперадмином → сохранить `superadmin_access_token`
4. **TC-020-CMP** — Создать тестовую компанию → сохранить `company_id`
5. **TC-003-AUTH** — Зарегистрировать нового пользователя `company_admin@test.kz`
6. **TC-017-AUTH** — Найти его в списке пользователей и назначить роль `company_admin` через Django Admin
7. **TC-006-AUTH** — Войти как company_admin → сохранить `company_admin_token`
8. **TC-033-CMP** — Создать приглашение для `employee@test.kz`
9. **TC-003-AUTH** — Зарегистрировать `employee@test.kz` через `/register/invite/`
10. **TC-006-AUTH** — Войти как employee → сохранить `access_token`

### Этап 2 — Ресурсы и бронирования

11. **TC-040-RES** — Суперадмин создаёт ресурс (переговорная) → сохранить `resource_id`
12. **TC-035-RES** — Employee проверяет список ресурсов
13. **TC-038-RES** — Employee получает детали ресурса
14. **TC-043-BKG** — Employee создаёт бронирование → сохранить `booking_id`
15. **TC-046-BKG** — Проверить "Мои бронирования"
16. **TC-048-BKG** — Отменить бронирование

### Этап 3 — CRM

17. **TC-052-CRM** — Employee создаёт доску → сохранить `board_id` и `column_id`
18. **TC-061-CRM** — Employee создаёт задачу → сохранить `task_id`
19. **TC-064-CRM** — Переместить задачу в другую колонку
20. **TC-067-CRM** — Добавить комментарий к задаче
21. **TC-065-CRM** — Проверить историю задачи

### Этап 4 — HR

22. **TC-079-HR** — Employee создаёт заявку на отпуск → сохранить `leave_id`
23. **TC-086-HR** — Проверить баланс отпуска
24. **TC-083-HR** — Company_admin одобряет заявку

### Этап 5 — Гости и доступ

25. **TC-087-ACC** — Company_admin создаёт гостевой пропуск → сохранить `qr_code`
26. **TC-090-ACC** — Валидировать QR-код
27. **TC-092-ACC** — Отозвать пропуск
28. **TC-091-ACC** — Убедиться что QR больше не валиден

### Этап 6 — Сервисы и объявления

29. **TC-097-SRV** — Employee создаёт сервисную заявку
30. **TC-100-SRV** — Суперадмин меняет статус заявки
31. **TC-103-SRV** — Company_admin создаёт объявление → сохранить `announcement_id`
32. **TC-106-SRV** — Employee отмечает объявление как прочитанное

### Этап 7 — Хранилище

33. **TC-071-STR** — Employee создаёт папку → сохранить `folder_id`
34. **TC-075-STR** — Загрузить файл в папку → сохранить `file_id`
35. **TC-078-STR** — Проверить статистику использования хранилища

### Этап 8 — Аналитика

36. **TC-116-ANL** — Company_admin смотрит свой дашборд
37. **TC-114-ANL** — Суперадмин смотрит общий дашборд

### Этап 9 — Проверка безопасности

38. **TC-025-CMP** — Убедиться что company_admin не видит чужую компанию (404)
39. **TC-050-BKG** — Убедиться что employee не видит бронирования другой компании
40. **TC-058-CRM** — Убедиться что доска другой компании недоступна (404)
41. **TC-011-AUTH** — Убедиться что без токена все защищённые эндпоинты возвращают 401
42. **TC-015-AUTH** — Выйти из системы, убедиться что refresh token заблокирован

---

## Краткая таблица ожидаемых кодов по ролям

| Эндпоинт | superadmin | company_admin | employee | guest | анонимный |
|---|---|---|---|---|---|
| POST `/auth/register/` | 201 | 201 | 201 | 201 | 201 |
| GET `/auth/me/` | 200 | 200 | 200 | 200 | 401 |
| GET `/auth/users/` | 200 | 403 | 403 | 403 | 401 |
| POST `/companies/` | 201 | 403 | 403 | 403 | 401 |
| GET `/companies/` | 200 (все) | 200 (своя) | 403 | 403 | 401 |
| GET `/bookings/resources/` | 200 | 200 | 200 | 403 | 401 |
| POST `/bookings/resources/` | 201 | 403 | 403 | 403 | 401 |
| POST `/bookings/reservations/` | 201 | 201 | 201 | 403 | 401 |
| GET `/crm/boards/` | 200 | 200 | 200 | 403 | 401 |
| GET `/hr/leaves/` | 200 | 200 | 200 | 403 | 401 |
| POST `/access/passes/` | 201 | 201 | 403 | 403 | 401 |
| GET `/access/logs/` | 200 | 403 | 403 | 403 | 401 |
| POST `/services/announcements/` | 201 | 201 | 403 | 403 | 401 |
| GET `/analytics/superadmin/` | 200 | 403 | 403 | 403 | 401 |
| GET `/analytics/company/` | 200 | 200 | 403 | 403 | 401 |
