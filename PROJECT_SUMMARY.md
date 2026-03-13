# New Level Hub Backend - Project Summary

## ✅ Completed Tasks (Week 1)

### 1. Infrastructure Setup ✅
- **PostgreSQL 15** configured in Docker
- **Django 4.2** with modular settings (base/local/production)
- **Docker Compose** files for local and production environments
- **Health check endpoint** at `/api/v1/health/`

### 2. Authentication System ✅
- **Custom User Model** with role-based access control
- **JWT Authentication** using djangorestframework-simplejwt
- **5 User Roles**: supermentor, admin, tenant, employee, guest
- **Biometric support** (face_data_ref field for Face ID)

### 3. API Documentation ✅
- **Swagger UI** integrated at `/api/docs/`
- **ReDoc** at `/api/redoc/`
- **OpenAPI Schema** at `/api/schema/`
- drf-spectacular для автоматической генерации документации

### 4. Project Structure ✅

```
NewLevelHub-Backend/
├── config/                 # Django configuration
│   ├── settings/
│   │   ├── base.py        # Shared settings
│   │   ├── local.py       # Development settings
│   │   └── production.py  # Production settings
│   ├── urls.py            # Main URL configuration
│   ├── wsgi.py / asgi.py  # WSGI/ASGI entry points
│   └── celery.py          # Celery configuration (prepared)
│
├── apps/
│   ├── core/              # Core utilities
│   │   ├── models.py      # TimeStampedModel, SoftDeleteModel
│   │   ├── permissions.py # Role-based permissions
│   │   ├── exceptions.py  # Custom exception handler
│   │   └── views.py       # Health check
│   │
│   └── users/             # User management
│       ├── models.py      # Custom User model
│       ├── serializers.py # API serializers
│       ├── views.py       # Auth endpoints
│       ├── urls.py        # URL routing
│       └── admin.py       # Django admin
│
├── Docker Files
│   ├── Dockerfile                  # Production-ready image
│   ├── docker-compose.local.yml    # Local development
│   └── docker-compose.prod.yml     # Production with Nginx
│
├── CI/CD
│   └── .github/workflows/ci.yml    # GitHub Actions pipeline
│
├── Scripts
│   ├── init.sh            # Development initialization
│   └── deploy.sh          # Production deployment
│
└── Documentation
    ├── README.md           # Full project documentation
    ├── requirements.txt    # Python dependencies
    └── setup.cfg          # pytest & flake8 config
```

## 📚 Implemented API Endpoints

### Authentication (`/api/v1/auth/`)
- `POST /auth/register/` - User registration
- `POST /auth/login/` - Login with JWT tokens
- `POST /auth/logout/` - Logout (blacklist refresh token)
- `POST /auth/token/refresh/` - Refresh access token
- `POST /auth/mobile/confirm/` - Mobile biometric confirmation
- `GET /auth/me/` - Get current user profile
- `POST /auth/password/reset/` - Password reset request

### Health Check (`/api/v1/health/`)
- `GET /health/` - Server and database status

### Documentation
- `GET /api/docs/` - Swagger UI
- `GET /api/redoc/` - ReDoc
- `GET /api/schema/` - OpenAPI JSON schema

## 🔑 User Role System

| Role | Access Level | Permissions |
|------|-------------|-------------|
| **supermentor** | Maximum | Full platform access, can do everything |
| **admin** | High | Business center administration |
| **tenant** | Medium | Office management, resource booking |
| **employee** | Basic | Limited resource access |
| **guest** | Restricted | Temporary access with limitations |

## 🛡️ Security Features

✅ **Authentication**
- JWT tokens (access + refresh)
- Automatic token rotation
- Token blacklisting on logout
- Bcrypt password hashing

✅ **Authorization**
- Role-based access control (RBAC)
- Custom permission classes
- Object-level permissions

✅ **API Security**
- CORS configuration
- CSRF protection
- XSS protection headers
- SQL injection prevention (Django ORM)

## 🐳 Docker Configuration

### Local Development
```bash
# Start containers
docker-compose -f docker-compose.local.yml up -d --build

# Run migrations
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate

# Create superuser
docker-compose -f docker-compose.local.yml exec backend python manage.py createsuperuser
```

**Features:**
- Hot reload with volume mounting
- Debug mode enabled
- BrowsableAPI for easy testing
- Console email backend

### Production
```bash
# Deploy
docker-compose -f docker-compose.prod.yml up -d --build
```

**Features:**
- Nginx reverse proxy
- Gunicorn WSGI server (4 workers)
- Static file serving
- SSL/TLS ready
- Health checks
- Automatic restart on failure

## 📊 Database Schema

### User Model
```python
- id (PK)
- email (unique, indexed)
- phone (unique, nullable)
- first_name / last_name
- role (choice field, indexed)
- is_active / is_staff
- face_data_ref (for biometrics)
- created_at / updated_at
- last_login / date_joined
```

### Indexes
- `(email, is_active)`
- `(role, is_active)`
- `created_at` (from TimeStampedModel)

## 🧪 Testing Setup

- **pytest** configured
- **coverage** reporting
- **flake8** linting
- **GitHub Actions** CI pipeline

## 🚀 Next Steps (Week 2-4)

### Week 2: Complete Auth Module
- [ ] Implement email/SMS password reset
- [ ] Add email verification
- [ ] Implement biometric validation logic
- [ ] Unit tests for Auth (80%+ coverage)

### Week 3: Booking System
- [ ] Resource model (parking, desk, meeting_room, capsule)
- [ ] Booking model with double-booking prevention
- [ ] Availability grid API
- [ ] Race condition tests

### Week 4: CRM + IoT Skeleton
- [ ] Kanban board models (Board, Column, Task)
- [ ] Device model for IoT
- [ ] QR token generation (30-sec TTL)
- [ ] Guest pass system
- [ ] `/api/v1/iot/verify-access/` endpoint

## 📝 Environment Variables

Create `.env` file from `.env.example`:

```bash
# Django
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
POSTGRES_DB=newlevelhub_db
POSTGRES_USER=nlh_user
POSTGRES_PASSWORD=nlh_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# JWT
ACCESS_TOKEN_LIFETIME_MINUTES=60
REFRESH_TOKEN_LIFETIME_DAYS=7

# CORS
CORS_ALLOWED_ORIGINS=http://localhost:3000
```

## 🎯 Success Criteria (Week 1) - ✅ COMPLETED

- [x] PostgreSQL + Django работают локально и в Docker
- [x] CI/CD pipeline создан (GitHub Actions)
- [x] Модель User с ролями реализована
- [x] JWT authentication работает
- [x] Swagger доступен на `/api/docs/`
- [x] Health check endpoint возвращает 200 OK
- [x] Postman collection можно создать из Swagger
- [x] README с инструкциями создан

## 🔧 Quick Start Commands

```bash
# Initialize project
./init.sh

# View logs
docker-compose -f docker-compose.local.yml logs -f backend

# Access Django shell
docker-compose -f docker-compose.local.yml exec backend python manage.py shell

# Run tests
docker-compose -f docker-compose.local.yml exec backend pytest

# Create migrations
docker-compose -f docker-compose.local.yml exec backend python manage.py makemigrations
```

## 📦 Key Technologies

- **Django 4.2** - Web framework
- **PostgreSQL 15** - Database
- **Docker** - Containerization
- **Nginx** - Reverse proxy (production)
- **Gunicorn** - WSGI server (production)
- **JWT** - Authentication
- **drf-spectacular** - API documentation
- **pytest** - Testing

---

**Status**: Week 1 Infrastructure Complete ✅  
**Next Sprint**: Authentication Enhancement + Booking System  
**Team Ready**: Frontend can start integration with `/api/docs/`
