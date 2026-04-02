# New Level Hub Backend (Clean Skeleton)

Проект очищен до базового каркаса и готов к разработке с нуля.

## Что оставили

- Django + DRF проектную структуру
- PostgreSQL конфигурацию
- Docker и docker-compose (local/prod)
- Nginx + Gunicorn для production
- Swagger/OpenAPI инфраструктуру (`drf-spectacular`)
- CI/CD пайплайны

## Что удалили

- Все старые API endpoint'ы
- Прикладную бизнес-логику
- Старые модели, сериалайзеры, views и тесты

## Быстрый старт

```bash
cp .env.example .env
docker-compose -f docker-compose.local.yml up --build
```

После запуска:
- Admin: `http://localhost:8000/admin/`
- Swagger: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`

## Следующие шаги

1. Создать первый доменный модуль (`python manage.py startapp <module_name>`).
2. Описать модели и миграции.
3. Добавить API (`urls.py`, `views.py`, `serializers.py`).
4. Подключить роуты в `config/urls.py`.
5. Добавить unit/integration тесты.
