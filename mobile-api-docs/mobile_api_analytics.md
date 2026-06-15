# Analytics — Аналитика

> Базовый префикс: `/api/v1/analytics/`  
> Подключение: [← Основная документация](mobile_api.md)

Дашборды и отчёты для `company_admin` и `superadmin`. Обычным сотрудникам недоступно.

> Для главного экрана employee используйте `GET /api/v1/dashboard/` ([Core](mobile_api_core.md)), не этот блок.

---

## Company Admin Dashboard

### ✅ GET /api/v1/analytics/company/

> Сводка по компании: сотрудники, брони, storage, CRM, HR, гости.

🔒 `company_admin`, `superadmin` (с привязкой к компании)

**Response 200**

```json
{
  "total_employees": 24,
  "active_7d": 18,
  "bookings_month": 156,
  "storage": {
    "used": 2147483648,
    "limit": 10737418240
  },
  "active_crm_tasks": {
    "total": 42,
    "todo": 15,
    "in_progress": 20,
    "done": 5,
    "other": 2,
    "by_column": [
      {
        "column_id": 10,
        "name": "В работе",
        "board_name": "Маркетинг",
        "count": 12
      }
    ]
  },
  "guest_visits_month": 8,
  "employee_activity": [
    {
      "user_id": 42,
      "full_name": "Иван Петров",
      "booking_count_30d": 12,
      "task_count_active": 3,
      "last_login": "2024-06-14T09:00:00Z"
    }
  ],
  "pending_approvals": {
    "leaves": [
      {
        "id": 15,
        "employee_name": "Анна Сидорова",
        "leave_type": "vacation",
        "start_date": "2024-07-01",
        "end_date": "2024-07-05",
        "created_at": "2024-06-10T10:00:00Z"
      }
    ],
    "guest_passes": [
      {
        "id": 12,
        "guest_name": "John Visitor",
        "host_name": "Иван Петров",
        "visit_date": "2024-06-20T09:00:00Z",
        "created_at": "2024-06-15T11:00:00Z"
      }
    ]
  }
}
```

| Поле | Описание |
|------|----------|
| `active_7d` | Сотрудники с `last_login` за 7 дней |
| `bookings_month` | Брони с начала текущего месяца |
| `storage.used` | Байты файлов компании в Storage |
| `active_crm_tasks` | Задачи на неархивных досках; buckets по имени колонки |
| `guest_visits_month` | Созданные guest passes за месяц |
| `pending_approvals` | `leave` status=pending, `guest_pass` status=active |

**Ошибки**

| Код | Когда |
|-----|-------|
| 400 | У пользователя нет компании |
| 403 | Не admin |

---

### ✅ GET /api/v1/analytics/company/export/?format=csv

### ✅ GET /api/v1/analytics/company/export/?format=pdf

> Экспорт той же сводки.

🔒 `company_admin`, `superadmin`

**Response 200** — файл `analytics-company-{slug}.csv` или `.pdf` (UTF-8 BOM для CSV)

| Параметр | Обязательно | Значения |
|----------|-------------|----------|
| `format` | да | `csv`, `pdf` |

---

## Superadmin Dashboard

### ✅ GET /api/v1/analytics/superadmin/

> Платформенная аналитика по всем компаниям.

🔒 Только `superadmin`

**Request**

```http
GET /api/v1/analytics/superadmin/?period=30d&company_id=5&resource_type=meeting_room
```

| Параметр | Default | Описание |
|----------|---------|----------|
| `period` | `30d` | `7d`, `30d`, `90d`, `custom` |
| `date_from`, `date_to` | — | Обязательны при `period=custom` (YYYY-MM-DD) |
| `company_id` | — | Сузить overview и графики до компании |
| `resource_type` | — | `desk`, `meeting_room`, `parking`, `capsule` |

**Response 200** — фрагмент:

```json
{
  "period": "30d",
  "date_from": "2024-05-17",
  "date_to": "2024-06-15",
  "overview": {
    "total_companies": 12,
    "active_companies": 11,
    "total_users": 340,
    "active_users_7d": 210,
    "bookings_today": 45,
    "guests_today": 8,
    "open_service_requests": 6
  },
  "resource_utilization": [
    {
      "date": "2024-06-15",
      "desk_bookings": 20,
      "room_bookings": 8,
      "parking_bookings": 3,
      "capsule_bookings": 1
    }
  ],
  "peak_hours": [
    {
      "day_of_week": 0,
      "hour": 10,
      "booking_count": 15
    }
  ],
  "new_registrations": [
    { "week": "2024-W24", "count": 5 }
  ],
  "service_requests_by_type": [
    { "type": "cleaning", "count": 42 }
  ],
  "top_resources": [
    {
      "resource_id": 50,
      "name": "Переговорная A-201",
      "resource_type": "meeting_room",
      "booking_count": 89
    }
  ],
  "top_companies": [
    {
      "company_id": 5,
      "company_name": "ACME",
      "booking_count": 120
    }
  ],
  "low_utilization": [
    {
      "resource_id": 12,
      "name": "Стол C-05",
      "resource_type": "desk",
      "booking_count": 2,
      "utilization_percent": 6.67
    }
  ]
}
```

| Секция | Описание |
|--------|----------|
| `overview.bookings_today` | Подтверждённые/завершённые брони **сегодня** |
| `overview.guests_today` | Входы гостей (`AccessLog`, `is_entry=true`) сегодня |
| `peak_hours.day_of_week` | 0 = понедельник … 6 = воскресенье |
| `low_utilization` | Ресурсы с загрузкой **< 20%** за период |

---

### ✅ GET /api/v1/analytics/superadmin/export/?format=csv

### ✅ GET /api/v1/analytics/superadmin/export/?format=pdf

> Экспорт KPI overview (те же query-параметры, что у dashboard).

🔒 `superadmin`

**Response 200** — `analytics-superadmin-{period}.csv` или `.pdf`

---

## Resource Usage (Superadmin)

### ✅ GET /api/v1/analytics/resources/

> Детальная статистика по каждому ресурсу.

🔒 `superadmin`

**Request**

```http
GET /api/v1/analytics/resources/?period=30d&resource_id=50&floor=3&company_id=5
```

| Параметр | Описание |
|----------|----------|
| `period`, `date_from`, `date_to` | Как у superadmin dashboard |
| `resource_id` | Один ресурс |
| `floor` | Номер этажа ресурса |
| `company_id` | Брони компании |

**Response 200**

```json
{
  "period": "30d",
  "date_from": "2024-05-17",
  "date_to": "2024-06-15",
  "results": [
    {
      "resource_id": 50,
      "resource_name": "Переговорная A-201",
      "resource_type": "meeting_room",
      "floor": 3,
      "total_bookings": 89,
      "avg_duration_minutes": 62.5,
      "total_booked_minutes": 5562.5,
      "peak_hour": 10,
      "peak_hour_bookings": 18
    }
  ]
}
```

| Поле | Описание |
|------|----------|
| `peak_hour` | Час с макс. числом броней (0–23, local TZ) |
| `avg_duration_minutes` | Средняя длительность брони |

---

### Важные детали для мобильщиков (Analytics)

#### Доступ

| Эндпоинт | `employee` | `company_admin` | `superadmin` |
|----------|------------|-----------------|--------------|
| `/company/` | **403** | ✅ | ✅ (нужна компания) |
| `/company/export/` | **403** | ✅ | ✅ |
| `/superadmin/` | **403** | **403** | ✅ |
| `/superadmin/export/` | **403** | **403** | ✅ |
| `/resources/` | **403** | **403** | ✅ |

`guest`, `reception`, `service_manager`, `employee` — **нет доступа** к Analytics API.

#### Admin mobile vs employee app

| Роль | Что использовать |
|------|------------------|
| `employee`, `guest` | `GET /api/v1/dashboard/` (Core) |
| `company_admin` | `GET /analytics/company/` для admin-дашборда |
| `superadmin` | `GET /analytics/superadmin/` + `/resources/` |

Проверяйте permission `view_analytics` из `/users/me/` перед показом admin-аналитики.

#### Мобильные экраны → эндпоинты

| Экран | Эндпоинт |
|-------|----------|
| Admin: KPI карточки | `GET /analytics/company/` |
| Admin: очередь согласований | `pending_approvals` в том же ответе |
| Admin: активность команды | `employee_activity` |
| Admin: экспорт отчёта | `GET /company/export/?format=pdf` |
| Superadmin: платформа | `GET /analytics/superadmin/?period=30d` |
| Superadmin: загрузка ресурсов | `GET /analytics/resources/` |

#### Периоды

```http
GET /analytics/superadmin/?period=7d
GET /analytics/superadmin/?period=custom&date_from=2024-01-01&date_to=2024-03-31
```

Default `period=30d`. Даты inclusive, timezone — **Asia/Almaty** (как в остальном API).

#### Экспорт на мобильном

- CSV/PDF возвращаются как **binary attachment** (`Content-Disposition`).
- Открывайте через Share Sheet / Files, не парсите как JSON.
- Параметр `format` — query (`format=csv`), не DRF content negotiation.

#### CRM buckets

`todo` / `in_progress` / `done` определяются **по имени колонки** (рус/англ эвристика). Кастомные колонки попадают в `other`.

#### Кэширование

Данные тяжёлые — кэшируйте на клиенте 2–5 минут, pull-to-refresh для обновления. Не поллить чаще раза в минуту.

#### Связь с другими блоками

| Метрика | Источник данных |
|---------|-----------------|
| `pending_approvals.leaves` | [HR](mobile_api_hr.md) |
| `pending_approvals.guest_passes` | [Access](mobile_api_access.md) |
| `bookings_*` | [Bookings](mobile_api_bookings.md) |
| `storage` | [Storage](mobile_api_storage.md) |
| `active_crm_tasks` | [CRM](mobile_api_crm.md) |

---
