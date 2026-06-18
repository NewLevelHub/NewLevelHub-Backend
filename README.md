# New Level Hub Backend

Backend API для веб-платформы бизнес-центра нового поколения в Астане.

## Стек

- **Django 4.2** + Django REST Framework
- **PostgreSQL** — основная БД
- **Redis + Celery** — фоновые задачи
- **JWT** (`djangorestframework-simplejwt`) — авторизация
- **drf-spectacular** — Swagger / OpenAPI
- **Docker Compose** — local и production
- **Nginx + Gunicorn** — production runtime
- **GitHub Actions** — CI/CD

## Структура модулей

```
apps/
├── core/            Базовые модели, пермишны, миксины, health check
├── users/           Пользователи, авторизация, профили
├── companies/       Компании-арендаторы, тарифы, инвайты
├── bookings/        Бронирование ресурсов (столы, залы, парковка, капсулы)
├── crm/             Канбан-доски, задачи, комментарии, лейблы
├── storage/         Файловое хранилище (личное + корпоративное)
├── hr/              Отпуска, отгулы, онбординг
├── access/          Гостевые пропуска, QR-коды, лог доступа
├── services/        Карта здания, сервисные заявки, объявления
├── notifications/   Уведомления + настройки доставки
└── analytics/       Дашборды и аналитика
```

## Быстрый старт

```bash
cp .env.example .env
docker-compose -f docker-compose.local.yml up --build

# В контейнере:
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

## Доступ после запуска

| Сервис | URL |
|--------|-----|
| Admin | http://localhost:8000/admin/ |
| Swagger UI | http://localhost:8000/api/docs/ |
| ReDoc | http://localhost:8000/api/redoc/ |
| OpenAPI schema | http://localhost:8000/api/schema/ |
| Health check | http://localhost:8000/api/v1/health/ |
| Dozzle (логи контейнеров) | http://localhost:9999/ |

## Dozzle (логи контейнеров)

Локально Dozzle поднимается вместе с `docker-compose.local.yml` на порту **9999** без авторизации.

На production UI доступен по `https://production.newlevelhub.kz/dozzle/` (с авторизацией).

На preview-средах (PR) — `https://be-<ticket>-pr<N>.staging.newlevelhub.kz/dozzle/` (или `https://fe-<ticket>-pr<N>.staging.newlevelhub.kz/dozzle/`). Dozzle показывает контейнеры backend и frontend preview с тем же тикетом (`dev-XXX`), например `be-dev-392-pr*` и `nlh_fe_fe-dev-392-pr*`.

На production фильтр `nlh_` включает и backend, и `nlh_frontend_prod`.

Перед первым деплоем на сервере создайте файл с учётными данными:

```bash
mkdir -p dozzle
docker run --rm amir20/dozzle generate admin \
  --password 'your-secure-password' \
  --name 'Admin' > dozzle/users.yml
```

Файл `dozzle/users.yml` не коммитится в git. Пример структуры — `dozzle/users.yml.example`.

## API Prefix

Все бизнес-эндпоинты под `/api/v1/`:

```
/api/v1/auth/           — регистрация, логин, JWT
/api/v1/companies/      — компании, инвайты, настройки
/api/v1/bookings/       — ресурсы, бронирования
/api/v1/crm/            — доски, задачи, лейблы
/api/v1/storage/        — файлы, папки, шаринг
/api/v1/hr/             — отпуска, онбординг
/api/v1/access/         — гостевые пропуска, QR валидация
/api/v1/services/       — карта, заявки, объявления
/api/v1/notifications/  — уведомления, настройки
/api/v1/analytics/      — дашборды
```

## Роли

| Роль | Код | Описание |
|------|-----|----------|
| Суперадмин БЦ | `superadmin` | Полный контроль над платформой |
| Админ компании | `company_admin` | Управляет своей компанией |
| Сотрудник | `employee` | Работает в CRM, бронирует |
| Гость коворкинга | `guest` | Только бронирование и базовые сервисы |

## Мультитенанси

Изоляция данных между компаниями реализована через `CompanyQuerySetMixin` — каждый queryset автоматически фильтруется по `company` текущего пользователя. Суперадмин видит всё.

## Что реализовано в scaffold

- Все модели с полями и связями
- Сериализаторы для каждой модели
- ViewSets с правильным роутингом и тегами Swagger
- Пермишны (`IsSuperAdmin`, `IsCompanyAdmin`, `IsCompanyMember`, `IsOwnerOrAdmin`)
- Django Admin регистрация всех моделей
- Celery task-заготовки для фоновых операций
- `TODO` комментарии в местах со сложной бизнес-логикой

## Что нужно реализовать (TODO)

Бизнес-логика помечена `TODO` в коде. Основные направления:

- Валидация конфликтов при бронировании
- Email-рассылка (verification, invite, password reset, notifications)
- QR-код генерация для гостевых пропусков
- Проверка тарифных лимитов (сотрудники, доски, хранилище)
- WIP-лимиты в CRM-колонках
- Автоматизации: напоминания, auto-cancel, recurring bookings
- Аналитика: графики и экспорт в CSV/PDF
