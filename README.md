# New Level Hub - Backend

Backend API для платформы New Level Hub - управление физическим бизнес-центром, бронирование ресурсов, CRM и IoT интеграция.

## 🏗️ Технологический стек

- **Backend**: Django 4.2 + Django REST Framework
- **Database**: PostgreSQL 15
- **Authentication**: JWT (djangorestframework-simplejwt)
- **Documentation**: drf-spectacular (Swagger/OpenAPI)
- **Containerization**: Docker + Docker Compose

## 📁 Структура проекта

```
NewLevelHub-Backend/
├── config/                 # Django settings and configuration
│   ├── settings/
│   │   ├── base.py        # Base settings
│   │   ├── local.py       # Local development settings
│   │   └── production.py  # Production settings
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── core/              # Core utilities and base models
│   ├── users/             # User authentication and management
│   ├── booking/           # Booking system (Coming soon)
│   ├── crm/               # CRM and Kanban boards (Coming soon)
│   └── iot/               # IoT device management (Coming soon)
├── docker-compose.local.yml   # Docker compose for local development
├── docker-compose.prod.yml    # Docker compose for production
├── Dockerfile
├── requirements.txt
├── manage.py
└── README.md
```

## 🚀 Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone <repository-url>
cd NewLevelHub-Backend
```

### 2. Создать .env файл

```bash
cp .env.example .env
```

Отредактируйте `.env` файл с вашими настройками.

### 3. Запуск с Docker (Рекомендуется)

#### Local Development:

```bash
# Собрать и запустить контейнеры
docker-compose -f docker-compose.local.yml up --build

# Создать миграции
docker-compose -f docker-compose.local.yml exec backend python manage.py makemigrations

# Применить миграции
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate

# Создать суперпользователя
docker-compose -f docker-compose.local.yml exec backend python manage.py createsuperuser
```

#### Production:

```bash
# Настроить .env.production
cp .env.example .env.production

# Запустить production контейнеры
docker-compose -f docker-compose.prod.yml up -d --build
```

### 4. Доступ к сервисам

- **Backend API**: http://localhost:8000/api/v1/
- **Swagger Documentation**: http://localhost:8000/api/docs/
- **Admin Panel**: http://localhost:8000/admin/
- **Database**: localhost:5432

## 📚 API Documentation

После запуска проекта документация доступна по адресам:

- **Swagger UI**: http://localhost:8000/api/docs/
- **ReDoc**: http://localhost:8000/api/redoc/
- **OpenAPI Schema**: http://localhost:8000/api/schema/

## 🔑 Роли пользователей

| Роль | Описание | Уровень доступа |
|------|----------|-----------------|
| `supermentor` | Полный доступ ко всему | Максимальный |
| `admin` | Администратор бизнес-центра | Высокий |
| `tenant` | Арендатор офиса | Средний |
| `employee` | Рядовой сотрудник | Базовый |
| `guest` | Гость (временный доступ) | Ограниченный |

## 🛠️ Разработка

### Запуск без Docker

```bash
# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# Установить зависимости
pip install -r requirements.txt

# Создать БД PostgreSQL вручную
createdb newlevelhub_db

# Применить миграции
python manage.py migrate

# Запустить сервер
python manage.py runserver
```

### Полезные команды

```bash
# Создать приложение
python manage.py startapp app_name

# Создать миграции
python manage.py makemigrations

# Применить миграции
python manage.py migrate

# Создать суперпользователя
python manage.py createsuperuser

# Запустить тесты
pytest

# Собрать статические файлы
python manage.py collectstatic
```

## 📋 Roadmap

### ✅ Неделя 1-2 (Completed)
- [x] PostgreSQL + Django setup
- [x] CI/CD pipeline basics
- [x] Custom User model with roles
- [x] JWT Authentication
- [x] Swagger documentation
- [x] Health check endpoint

### 🚧 Неделя 3 (In Progress)
- [ ] Booking system API
- [ ] Resource management
- [ ] Double-booking prevention

### 📅 Неделя 4 (Planned)
- [ ] CRM module (Kanban boards)
- [ ] IoT endpoints skeleton
- [ ] Guest pass system

## 🧪 Тестирование

```bash
# Запустить все тесты
pytest

# Запустить с coverage
pytest --cov=apps --cov-report=html

# Запустить конкретный тест
pytest apps/users/tests/test_auth.py
```

## 🔐 Безопасность

- JWT токены с автоматическим обновлением
- Bcrypt хеширование паролей
- CORS настройки
- Rate limiting (TODO)
- Device API keys для IoT устройств

## 📦 Production Deployment

```bash
# Настроить production переменные окружения
vim .env.production

# Запустить с Nginx
docker-compose -f docker-compose.prod.yml up -d

# Проверить логи
docker-compose -f docker-compose.prod.yml logs -f backend
```

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is proprietary and confidential.

## 👥 Team

Backend Development Team - New Level Hub

---

**Версия**: 1.0.0  
**Последнее обновление**: March 2026
