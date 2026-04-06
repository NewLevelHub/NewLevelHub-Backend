# Роадмап для React Native / TypeScript разработчика — NewLevelHub Backend

> Целевая аудитория: разработчик с опытом React Native и TypeScript, который начинает работать с этим конкретным Django-проектом.
> Общее время: ~5-6 недель при полной занятости, 8-10 недель при частичной.

---

## Содержание

1. [Фаза 0 — Ориентация (1-2 дня)](#фаза-0--ориентация-1-2-дня)
2. [Фаза 1 — Основы Python/Django (1-2 недели)](#фаза-1--основы-pythondjango-1-2-недели)
3. [Фаза 2 — Django REST Framework (1 неделя)](#фаза-2--django-rest-framework-1-неделя)
4. [Фаза 3 — Архитектура этого проекта (1 неделя)](#фаза-3--архитектура-этого-проекта-1-неделя)
5. [Фаза 4 — База данных и миграции (3-5 дней)](#фаза-4--база-данных-и-миграции-3-5-дней)
6. [Фаза 5 — Аутентификация и безопасность (2-3 дня)](#фаза-5--аутентификация-и-безопасность-2-3-дня)
7. [Фаза 6 — Тестирование (3-5 дней)](#фаза-6--тестирование-3-5-дней)
8. [Фаза 7 — Docker и DevOps (2-3 дня)](#фаза-7--docker-и-devops-2-3-дня)
9. [Фаза 8 — Celery и асинхронность (2-3 дня)](#фаза-8--celery-и-асинхронность-2-3-дня)
10. [Фаза 9 — Первые реальные задачи](#фаза-9--первые-реальные-задачи)
11. [Шпаргалка](#шпаргалка)

---

## Фаза 0 — Ориентация (1-2 дня)

### Цель

За 1-2 дня получить рабочее локальное окружение и общую карту проекта — не читать весь код, а понять, где что лежит.

### Что читать в первую очередь

| Порядок | Файл | Зачем |
|---------|------|-------|
| 1 | `CLAUDE.md` | Полный контракт проекта: стек, команды, архитектура, conventions |
| 2 | `New_Level_Hub_MVP_Scope.md` | Бизнес-контекст: что за продукт, какие модули |
| 3 | `config/urls.py` | Карта всех API-маршрутов — видно всю структуру за 30 секунд |
| 4 | `config/settings/base.py` | Что установлено, какие apps, ключевые настройки |
| 5 | `apps/core/models.py` | Базовые абстракции от которых наследуется всё остальное |
| 6 | `apps/bookings/models.py` | Самый насыщенный domain-пример — посмотреть как выглядит типичная модель |
| 7 | `apps/bookings/views.py` | Типичный ViewSet с permissions и actions |

### Как запустить локально

```bash
# 1. Скопировать env
cp .env.example .env

# 2. Запустить Docker (Compose v1 — именно docker-compose, не docker compose)
docker-compose -f docker-compose.local.yml up -d --build

# 3. Применить миграции
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate

# 4. Создать суперпользователя
docker-compose -f docker-compose.local.yml exec backend python manage.py createsuperuser

# 5. Открыть Swagger UI
open http://localhost:8000/api/docs/
```

### Быстрая карта проекта

```
NewLevelHub-Backend/
├── config/               # Django settings, urls, celery, wsgi
│   └── settings/
│       ├── base.py       # Общие настройки
│       ├── local.py      # Dev (DEBUG=True)
│       └── production.py # Prod (HTTPS, SMTP)
├── apps/                 # Все Django-приложения
│   ├── core/             # Базовые классы, permissions, mixins
│   ├── users/            # Auth, User model
│   ├── companies/        # Multi-tenancy root
│   ├── bookings/         # Бронирования ресурсов
│   ├── crm/              # Kanban CRM
│   ├── storage/          # Файловое хранилище
│   ├── hr/               # HR workflows
│   ├── access/           # QR-доступ гостей
│   ├── services/         # Здание, объявления
│   ├── notifications/    # In-app уведомления
│   └── analytics/        # Аналитика (заглушки)
├── docker-compose.local.yml
├── Dockerfile
├── manage.py             # Django CLI (аналог yarn/npm run)
├── conftest.py           # pytest fixtures
└── setup.cfg             # pytest + flake8 конфиг
```

### Практическое упражнение фазы 0

1. Запустить проект локально и открыть `http://localhost:8000/api/docs/`
2. Залогиниться через `POST /api/v1/auth/login/`, получить access token
3. Выполнить `GET /api/v1/bookings/resources/` через Swagger с авторизацией
4. Найти в коде, где именно обрабатывается этот запрос (`apps/bookings/views.py` → `ResourceViewSet`)

### Критерий завершения фазы 0

Ты готов двигаться дальше, если:
- Проект запускается локально без ошибок
- Ты можешь объяснить, что такое `apps/core/` и зачем он нужен
- Ты знаешь URL всех основных сущностей
- Swagger UI открывается и ты можешь делать запросы

---

## Фаза 1 — Основы Python/Django (1-2 недели)

### Параллели: TypeScript vs Python

Самое важное — не учить Python с нуля, а переотобразить уже знакомые концепции.

| TypeScript / JS | Python |
|-----------------|--------|
| `const x: string = 'hello'` | `x: str = 'hello'` (type hints опциональны) |
| `interface User { name: string }` | `class User(TypedDict): name: str` или dataclass |
| `async/await` | `async/await` (практически идентично) |
| `Array.map/filter/reduce` | list comprehensions `[x for x in lst if x > 0]` |
| `?.` optional chaining | `getattr(obj, 'field', None)` или `obj.field if obj else None` |
| `??` nullish coalescing | `value or default` |
| `export default` / `import` | нет export — всё модули, `from apps.core.models import TimeStampedModel` |
| `package.json` / `npm` | `requirements.txt` / `pip` |
| `tsconfig.json` | нет прямого аналога; конфиг в `setup.cfg`, `pyproject.toml` |
| `jest` | `pytest` |
| `eslint` | `flake8` |

### Ключевые концепции Python, которые нужно понять

#### 1. Классы и наследование (критично для Django)

```python
# Python — классы с множественным наследованием (используется везде в Django)
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:        # внутренний класс — аналог static readonly metadata
        abstract = True  # не создаёт таблицу в БД

class Booking(TimeStampedModel):  # наследуется, получает created_at бесплатно
    ...
```

```typescript
// TypeScript аналог (упрощённо)
abstract class TimeStampedModel {
  createdAt: Date = new Date();
}

class Booking extends TimeStampedModel {
  // автоматически имеет createdAt
}
```

#### 2. Декораторы (используются повсюду)

```python
# Python декораторы — функции-обёртки
@property
def full_name(self):              # вызывается как user.full_name, не user.full_name()
    return f'{self.first_name} {self.last_name}'

@extend_schema(tags=['Bookings']) # DRF Spectacular — OpenAPI аннотация
@action(detail=True, methods=['post'])
def cancel(self, request, pk=None):
    ...
```

```typescript
// TypeScript аналог
get fullName(): string {
  return `${this.firstName} ${this.lastName}`;
}

// Декораторы в TS (NestJS стиль)
@Controller('bookings')
@Get(':id/cancel')
async cancel(@Param('id') id: string) { ... }
```

#### 3. Менеджеры и QuerySet — это не массивы

```python
# QuerySet — ленивый (lazy) запрос к БД. Ничего не выполняется до момента итерации
bookings = Booking.objects.filter(status='confirmed')  # SQL ещё не выполнен
bookings = bookings.filter(company=user.company)       # добавляем условие
results = list(bookings)                               # вот здесь SQL выполняется
```

```typescript
// Аналог в Prisma
const bookings = await prisma.booking.findMany({
  where: {
    status: 'confirmed',
    companyId: user.companyId,
  }
});
// В отличие от Django, Prisma всегда await — нет "ленивости"
```

#### 4. f-строки и форматирование

```python
name = "World"
greeting = f"Hello, {name}!"           # f-строка
greeting = "Hello, {}!".format(name)   # .format()
greeting = "Hello, %s!" % name         # старый стиль, не используй

# Многострочные строки
sql = """
    SELECT *
    FROM users
    WHERE is_active = true
"""
```

```typescript
const name = "World";
const greeting = `Hello, ${name}!`;  // template literal — почти то же самое
```

### Что нужно знать из Django (не из DRF)

**Изучи, НЕ пропускай:**
- `models.Model` и поля (`CharField`, `ForeignKey`, `JSONField`, etc.)
- `class Meta` внутри модели
- Django ORM: `.filter()`, `.exclude()`, `.select_related()`, `.prefetch_related()`
- `settings.py` и как Django загружает настройки
- Django migrations: `makemigrations` и `migrate`
- `manage.py` команды

**Можно пропустить на старте:**
- Django templates (в этом проекте не используются — только DRF)
- Django forms (заменены DRF serializers)
- Django admin (есть, но не core функционал)
- Class-based views Django (в проекте используются DRF ViewSets)

### Ресурсы

| Ресурс | Что изучать | Время |
|--------|------------|-------|
| [Official Django Tutorial](https://docs.djangoproject.com/en/4.2/intro/tutorial01/) | Части 1-4 (models, views, urls) — пропустить templates | 4-6 часов |
| [Python Official Docs — Classes](https://docs.python.org/3/tutorial/classes.html) | Наследование, `super()`, `__str__` | 1 час |
| [Real Python — Django ORM](https://realpython.com/django-orm-python/) | QuerySet API | 2 часа |
| [Learnpython.org](https://www.learnpython.org/) | Синтаксис, если нужно освежить | 2-3 часа |

### Практическое упражнение фазы 1

1. Открыть `apps/bookings/models.py` и `apps/users/models.py` и прочитать каждую строку, разобрав каждое поле
2. В Django shell попробовать ORM-запросы:

```bash
docker-compose -f docker-compose.local.yml exec backend python manage.py shell

# Внутри shell:
from apps.users.models import User
User.objects.all()
User.objects.filter(role='employee').count()
User.objects.select_related('company').first()
```

3. Написать 3 ORM-запроса: получить все бронирования одной компании, найти ресурсы с проектором, получить пользователя по email

### Критерий завершения фазы 1

Ты готов двигаться дальше, если:
- Читаешь Python-код без гуглинга базового синтаксиса
- Понимаешь, что такое `abstract = True` в `class Meta`
- Можешь написать ORM-запрос с `filter`, `exclude`, `select_related`
- Понимаешь разницу между `objects` и `all_objects` в `SoftDeleteModel`

---

## Фаза 2 — Django REST Framework (1 неделя)

### Архитектура DRF одним графом

```
HTTP Request
    |
    v
[Router] ──> [ViewSet] ──> [Permission Classes] ──> [Serializer] ──> [Response]
                |                                         |
                v                                         v
          get_queryset()                           validate() / create()
                |
                v
           [Database]
```

### ViewSet — это не контроллер, это набор action'ов

```python
# Python — DRF ViewSet (из apps/bookings/views.py)
class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.filter(is_active=True)
    serializer_class = ResourceSerializer

    def get_permissions(self):
        # Динамические permissions в зависимости от action
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]

    # Кастомный action — добавляет маршрут /resources/{id}/schedule/
    @action(detail=True, methods=['get'], url_path='schedule')
    def schedule(self, request, pk=None):
        resource = self.get_object()
        ...
        return Response(data)
```

```typescript
// TypeScript аналог (NestJS)
@Controller('resources')
export class ResourceController {
  @Get()
  @UseGuards(AuthGuard)
  findAll() { ... }

  @Post()
  @UseGuards(SuperAdminGuard)
  create(@Body() dto: CreateResourceDto) { ... }

  @Get(':id/schedule')
  getSchedule(@Param('id') id: string) { ... }
}
```

### Стандартные action'ы ModelViewSet

| Action | HTTP Метод | URL | Описание |
|--------|-----------|-----|---------|
| `list` | GET | `/resources/` | Список |
| `create` | POST | `/resources/` | Создать |
| `retrieve` | GET | `/resources/{id}/` | Получить один |
| `update` | PUT | `/resources/{id}/` | Заменить |
| `partial_update` | PATCH | `/resources/{id}/` | Обновить частично |
| `destroy` | DELETE | `/resources/{id}/` | Удалить |

### Serializer — это DTO + валидация одновременно

```python
# Python — DRF Serializer (из apps/bookings/serializers.py)
class BookingCreateSerializer(serializers.ModelSerializer):
    # Дополнительное поле, которого нет в модели
    participant_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=[]
    )

    class Meta:
        model = Booking
        fields = ['resource', 'start_time', 'end_time', 'description', 'participant_ids']

    def validate(self, attrs):
        # Кросс-field валидация
        if attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError('start_time must be before end_time')
        return attrs

    def create(self, validated_data):
        # Кастомная логика создания
        participant_ids = validated_data.pop('participant_ids', [])
        user = self.context['request'].user
        validated_data['user'] = user
        booking = super().create(validated_data)
        return booking
```

```typescript
// TypeScript аналог (class-validator + class-transformer в NestJS)
export class CreateBookingDto {
  @IsUUID()
  resourceId: string;

  @IsDateString()
  startTime: string;

  @IsDateString()
  endTime: string;

  @IsArray()
  @IsOptional()
  participantIds?: number[];
}

// Валидация — отдельный Pipe или в сервисе
```

### Computed поля в Serializer

```python
# Поле из связанной модели
resource_name = serializers.CharField(source='resource.name', read_only=True)

# Полностью кастомное поле
participants = serializers.SerializerMethodField()

def get_participants(self, obj):
    return list(obj.participants.values_list('user__email', flat=True))
```

### Фильтры (django-filter)

```python
# apps/bookings/filters.py — декларативное описание фильтров
class BookingFilter(django_filters.FilterSet):
    status = django_filters.CharFilter()
    # Фильтр по полю связанной модели через __
    resource_type = django_filters.CharFilter(field_name='resource__resource_type')
    # Фильтр с lookup expression (gte = >=)
    date_from = django_filters.DateTimeFilter(field_name='start_time', lookup_expr='gte')

    class Meta:
        model = Booking
        fields = ['status', 'resource', 'user']
```

```typescript
// TypeScript аналог — вручную парсишь query params
// В NestJS обычно через @Query() dto
async findAll(@Query() query: FilterBookingDto) {
  const where: Prisma.BookingWhereInput = {};
  if (query.status) where.status = query.status;
  if (query.dateFrom) where.startTime = { gte: new Date(query.dateFrom) };
  return this.bookingService.findAll(where);
}
```

### Роутер — автогенерация URL

```python
# apps/bookings/urls.py
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'resources', views.ResourceViewSet)
router.register(r'bookings', views.BookingViewSet, basename='booking')

urlpatterns = router.urls
# Автоматически создаёт:
# GET/POST /resources/
# GET/PUT/PATCH/DELETE /resources/{id}/
# GET /resources/{id}/schedule/   (кастомный action)
# GET/POST /bookings/
# ...
```

### Ресурсы

| Ресурс | Приоритет |
|--------|----------|
| [DRF Official Docs — Tutorial](https://www.django-rest-framework.org/tutorial/1-serialization/) | Обязательно: части 1-6 |
| [DRF Official Docs — ViewSets](https://www.django-rest-framework.org/api-guide/viewsets/) | Обязательно |
| [DRF Official Docs — Serializers](https://www.django-rest-framework.org/api-guide/serializers/) | Обязательно |
| [django-filter docs](https://django-filter.readthedocs.io/en/stable/) | Желательно |
| [drf-spectacular docs](https://drf-spectacular.readthedocs.io/) | Желательно |

### Практическое упражнение фазы 2

1. Прочитать `apps/bookings/views.py` и `apps/bookings/serializers.py` полностью — теперь это должно быть понятно
2. Добавить в `BookingFilter` новый фильтр по `user_id` (в учебных целях)
3. Добавить в `BookingSerializer` вычисляемое поле `duration_minutes` (разница `end_time - start_time` в минутах)

### Критерий завершения фазы 2

Ты готов двигаться дальше, если:
- Можешь объяснить разницу между `serializer_class` и `get_serializer_class()`
- Понимаешь, когда `validate()` получает `attrs` и как работает `ValidationError`
- Знаешь, что делает `@action(detail=True, methods=['post'])`
- Понимаешь разницу между `has_permission` и `has_object_permission`

---

## Фаза 3 — Архитектура этого проекта (1 неделя)

### Multi-tenancy — главный паттерн проекта

В этом проекте multi-tenancy реализован через `company` FK: каждая запись принадлежит компании. Изоляция обеспечивается через `CompanyIsolationMixin`.

```
Запрос приходит от user (company_id=5)
        |
        v
CompanyIsolationMixin.get_queryset()
        |
        ├── user.role == 'superadmin' → возвращает ВСЕ записи
        ├── user.company_id != None   → фильтрует: WHERE company_id = 5
        └── user.company_id == None   → возвращает пустой QuerySet
```

```python
# apps/core/mixins.py — как работает изоляция
class CompanyIsolationMixin:
    company_field = 'company'  # переопредели если FK называется иначе

    def get_queryset(self):
        qs = super().get_queryset()  # super() вызывает ViewSet.get_queryset()
        user = self.request.user
        if user.role == 'superadmin':
            return qs  # видит всё
        if user.company_id:
            return qs.filter(**{self.company_field: user.company_id})
        return qs.none()  # пустой QuerySet без SQL

class SetCompanyOnCreateMixin:
    def perform_create(self, serializer):
        # Автоматически присваивает company при создании
        serializer.save(company=self.request.user.company)
```

### Иерархия разрешений

```
superadmin        — полный доступ ко всему, кроссполученный
  |
company_admin     — управляет своей компанией
  |
employee          — участник компании, ограниченные права на запись
  |
guest             — только чтение в открытых областях
```

**Правило выбора permission class:**

```python
# Только суперадмин (создание ресурсов, конфигурация платформы)
permission_classes = [IsSuperAdmin]

# Управление компанией (создание/удаление сотрудников, настройки)
permission_classes = [IsCompanyAdmin]

# Большинство рабочих endpoint'ов (CRM, HR, bookings)
permission_classes = [IsCompanyMember]

# Каталог, справочники — читают все, пишут только admins
permission_classes = [IsCompanyAdminOrReadOnly]

# Пользователь работает только со своими объектами
permission_classes = [IsOwnerOrAdmin]
```

### Soft Delete — почему это важно

```python
# apps/core/models.py
class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Менеджер по умолчанию скрывает удалённые
    objects = SoftDeleteManager()       # WHERE is_deleted = false
    all_objects = models.Manager()      # всё включая удалённые

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])
```

```python
# Использование
task = Task.objects.get(id=pk)  # не найдёт удалённый (is_deleted=True)
task.soft_delete()              # помечает как удалённый

# Восстановление
task.restore()

# Просмотр включая удалённые (только для admins)
Task.all_objects.filter(is_deleted=True)
```

### Структура типичного Django app

Каждое приложение в `apps/` следует одному паттерну:

```
apps/bookings/
├── __init__.py
├── admin.py          # Django admin (регистрация моделей)
├── apps.py           # AppConfig (имя приложения)
├── filters.py        # django-filter FilterSet классы
├── migrations/       # Автогенерированные SQL миграции
│   ├── 0001_initial.py
│   └── ...
├── models.py         # Django ORM модели
├── serializers.py    # DRF Serializers (валидация + сериализация)
├── tasks.py          # Celery tasks (фоновые задачи)
├── tests/            # Тесты
│   └── test_views.py
├── urls.py           # DRF Router + urlpatterns
└── views.py          # DRF ViewSets
```

### Как читать незнакомый ViewSet за 2 минуты

Открой любой `views.py` и ответь на 5 вопросов:

1. **Какая модель?** → `queryset = Model.objects.all()`
2. **Какие permissions?** → `permission_classes = [...]` или `get_permissions()`
3. **Какая изоляция?** → унаследован ли `CompanyIsolationMixin`?
4. **Какой сериализатор?** → `serializer_class` или `get_serializer_class()`
5. **Есть ли кастомные actions?** → методы с `@action`

### Разбор `BookingViewSet` построчно

```python
class BookingViewSet(viewsets.ModelViewSet):
    serializer_class = BookingSerializer
    filterset_class = BookingFilter          # автоподключение django-filter
    ordering_fields = ['start_time', 'created_at']
    http_method_names = ['get', 'post', 'patch', 'delete']  # PUT отключён намеренно

    def get_queryset(self):
        # Ручная изоляция (не через mixin — более гибко для этой модели)
        user = self.request.user
        if user.role == 'superadmin':
            return Booking.objects.all()
        if user.role == 'company_admin' and user.company_id:
            return Booking.objects.filter(company=user.company)
        return Booking.objects.filter(user=user)  # employee видит только свои

    def get_serializer_class(self):
        # Разные сериализаторы для create vs read
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer
```

### OpenAPI аннотации — обязательный паттерн

В проекте каждый endpoint аннотирован через `drf-spectacular`. Это не опционально — это convention.

```python
@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],           # группировка в Swagger
        summary='List bookings',     # короткое описание
        responses={200: BookingSerializer(many=True)},  # схема ответа
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create booking',
        request=BookingCreateSerializer,    # схема запроса
        responses={
            201: BookingSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
)
class BookingViewSet(viewsets.ModelViewSet):
    ...
```

### Практическое упражнение фазы 3

1. Прочитать `apps/crm/views.py` и ответить на 5 вопросов для каждого ViewSet
2. Найти все места, где используется `CompanyIsolationMixin` (их должно быть несколько)
3. Найти модели, которые наследуют `SoftDeleteModel` — понять, зачем они soft-delete
4. Нарисовать на бумаге / в Mermaid граф: Company → User → Booking → Resource

### Критерий завершения фазы 3

Ты готов двигаться дальше, если:
- Можешь без подсказки объяснить, как `CompanyIsolationMixin` защищает данные
- Понимаешь, когда использовать `IsSuperAdmin` vs `IsCompanyAdmin` vs `IsCompanyMember`
- Можешь прочитать любой `views.py` в проекте и объяснить его логику
- Знаешь разницу между `Model.objects.all()` и `Model.all_objects.all()`

---

## Фаза 4 — База данных и миграции (3-5 дней)

### Django ORM vs TypeScript ORM

| Концепция | Django ORM | Prisma | Drizzle |
|-----------|-----------|--------|---------|
| Определение схемы | Python класс `models.Model` | `schema.prisma` | TypeScript объект |
| Создание миграций | `makemigrations` (авто) | `prisma migrate dev` (авто) | `drizzle-kit generate` |
| Применение | `migrate` | `migrate dev/deploy` | `drizzle-kit push` |
| Запрос | `Model.objects.filter(...)` | `prisma.model.findMany({where})` | `db.select()...` |
| Связи | `ForeignKey`, `ManyToManyField` | `@relation` | `references()` |
| Raw SQL | `Model.objects.raw(...)` | `prisma.$queryRaw` | `sql\`...\`` |

### Поля Django моделей — шпаргалка

```python
# Строки
name = models.CharField(max_length=255)          # VARCHAR(255)
description = models.TextField(blank=True)       # TEXT, может быть пустым
email = models.EmailField(unique=True)           # VARCHAR(254) + уникальность

# Числа
count = models.IntegerField(default=0)
price = models.DecimalField(max_digits=10, decimal_places=2)
floor = models.PositiveIntegerField(default=1)   # только положительные

# Даты/время
created_at = models.DateTimeField(auto_now_add=True)  # заполняется при создании
updated_at = models.DateTimeField(auto_now=True)       # обновляется при save()
start_time = models.DateTimeField(db_index=True)       # с индексом

# Boolean
is_active = models.BooleanField(default=True)

# JSON
available_days = models.JSONField(default=list)   # PostgreSQL JSONB

# Файлы
photo = models.ImageField(upload_to='resources/', null=True, blank=True)

# Связи
company = models.ForeignKey(
    'companies.Company',   # строка — отложенная ссылка (нет circular import)
    on_delete=models.CASCADE,    # удалить каскадно
    # on_delete=models.SET_NULL, # обнулить FK при удалении родителя
    related_name='bookings',     # обратная ссылка: company.bookings.all()
    null=True, blank=True        # nullable FK
)
```

### Миграции — workflow

```bash
# 1. Изменил модель — создать миграцию
python manage.py makemigrations bookings
# Создаётся файл apps/bookings/migrations/0002_booking_new_field.py

# 2. Применить миграции к БД
python manage.py migrate

# 3. Посмотреть SQL который будет выполнен (без применения)
python manage.py sqlmigrate bookings 0002

# 4. Если нужен откат
python manage.py migrate bookings 0001  # откат к конкретной миграции

# Внутри Docker
docker-compose -f docker-compose.local.yml exec backend python manage.py makemigrations bookings
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate
```

### ORM запросы — практика

```python
# Базовые операции
User.objects.all()                          # SELECT * FROM users
User.objects.filter(role='employee')        # WHERE role = 'employee'
User.objects.exclude(role='guest')          # WHERE role != 'guest'
User.objects.get(email='user@example.com')  # одна запись или DoesNotExist
User.objects.first()                        # LIMIT 1

# Сложные фильтры
from django.db.models import Q

# OR условие
User.objects.filter(Q(role='admin') | Q(role='superadmin'))

# AND (по умолчанию)
Booking.objects.filter(status='confirmed', company_id=5)

# Поиск в связанных моделях (JOIN через __)
Booking.objects.filter(resource__resource_type='meeting_room')
Booking.objects.filter(user__company__id=5)

# Оптимизация — N+1 проблема
# ПЛОХО (N+1):
for booking in Booking.objects.all():
    print(booking.resource.name)  # каждый раз новый SQL запрос!

# ХОРОШО (один JOIN):
for booking in Booking.objects.select_related('resource', 'user').all():
    print(booking.resource.name)  # данные уже загружены

# Для ManyToMany — prefetch_related
for booking in Booking.objects.prefetch_related('participants').all():
    print(booking.participants.all())
```

```typescript
// Аналог в Prisma
// ПЛОХО (N+1):
const bookings = await prisma.booking.findMany();
for (const b of bookings) {
  const resource = await prisma.resource.findUnique({ where: { id: b.resourceId } });
}

// ХОРОШО:
const bookings = await prisma.booking.findMany({
  include: { resource: true, user: true }
});
```

### Индексы в проекте

```python
# Простой индекс на поле
start_time = models.DateTimeField(db_index=True)

# Составной индекс в class Meta
class Meta:
    db_table = 'bookings'
    indexes = [
        models.Index(fields=['resource', 'start_time', 'end_time']),  # для поиска конфликтов
        models.Index(fields=['user', 'status']),                       # для my bookings
    ]
```

### Практическое упражнение фазы 4

1. Открыть все модели в `apps/` и найти паттерн: какие используют `SoftDeleteModel`, какие только `TimeStampedModel`
2. В Django shell выполнить запрос с `select_related` к `Booking` (включить `resource` и `user`)
3. Добавить новое поле `notes = models.TextField(blank=True, default='')` в модель `Booking`, создать и применить миграцию
4. Откатить эту миграцию командой `migrate bookings <предыдущий номер>`

### Критерий завершения фазы 4

Ты готов двигаться дальше, если:
- Можешь самостоятельно добавить поле в модель и создать корректную миграцию
- Знаешь разницу между `select_related` и `prefetch_related` и когда использовать каждый
- Понимаешь `on_delete=models.CASCADE` vs `models.SET_NULL` и последствия каждого
- Можешь написать ORM-запрос с `Q` объектами для OR-условий

---

## Фаза 5 — Аутентификация и безопасность (2-3 дня)

### JWT в этом проекте

```
POST /api/v1/auth/login/
{ "email": "user@example.com", "password": "secret" }

→ 200 OK
{
  "access": "eyJ...",   // живёт 60 минут
  "refresh": "eyJ..."   // живёт 7 дней
}

Все последующие запросы:
Authorization: Bearer eyJ...

Обновление токена:
POST /api/v1/auth/token/refresh/
{ "refresh": "eyJ..." }
→ новый access token (refresh ротируется + старый blacklist'ится)
```

### Как SimpleJWT интегрирован

```python
# config/settings/base.py
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,      # каждое обновление → новый refresh
    'BLACKLIST_AFTER_ROTATION': True,   # старый refresh становится недействительным
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# REST_FRAMEWORK
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',  # по умолчанию всё закрыто
    ],
}
```

### Как работают permission классы под капотом

```python
# Упрощённый flow DRF:
# 1. Request приходит в ViewSet
# 2. DRF вызывает check_permissions(request) для каждого класса в permission_classes
# 3. Если хоть один класс вернул False → DRF выбрасывает исключение
# 4. Если 401 (не аутентифицирован) → DRF отвечает HTTP 401
# 5. Если 403 (аутентифицирован, но нет прав) → HTTP 403

# Базовый класс из apps/core/permissions.py
class _AuthenticatedPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False  # → 401
        return self._has_role_permission(request, view)  # → 403 если False

# Уровень объекта (вызывается при get_object())
class IsOwnerOrAdmin(_AuthenticatedPermission):
    owner_field = 'user'  # переопредели: owner_field = 'created_by'

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'superadmin':
            return True
        # obj.user == request.user → владелец
        owner = getattr(obj, self.owner_field, None)
        if owner == user:
            return True
        # company_admin видит всё в своей компании
        if user.role == 'company_admin' and hasattr(obj, 'company'):
            return obj.company_id == user.company_id
        return False
```

### Стекинг permission классов

```python
# ВСЕ классы в списке должны вернуть True (AND логика)
permission_classes = [IsCompanyMember, IsCompanyAdminOrReadOnly]
# IsCompanyMember — блокирует guests без company
# IsCompanyAdminOrReadOnly — разрешает read всем, write только admins

# ВАЖНО: IsCompanyAdminOrReadOnly позволяет guest читать,
# поэтому стекай с IsCompanyMember если guests тоже должны быть заблокированы
```

### Роли в контексте бизнес-логики

| Роль | company_id | Что видит |
|------|-----------|----------|
| `superadmin` | null | Все данные всех компаний |
| `company_admin` | обязателен | Всё в своей компании |
| `employee` | обязателен | Своя компания, ограниченная запись |
| `guest` | nullable | Только открытые endpoints (доступ, QR) |

### Практическое упражнение фазы 5

1. Прочитать `apps/core/tests/test_permissions.py` полностью — это живая документация поведения permissions
2. В Swagger UI: залогиниться как employee, попробовать создать Resource → получить 403
3. Понять почему в `IsCompanyAdminOrReadOnly` гость может читать, но не писать

### Тестирование auth вручную

```bash
# Получить токены
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "yourpass"}'

# Использовать access token
curl http://localhost:8000/api/v1/bookings/bookings/ \
  -H "Authorization: Bearer <access_token>"

# Обновить токен
curl -X POST http://localhost:8000/api/v1/auth/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "<refresh_token>"}'
```

### Критерий завершения фазы 5

Ты готов двигаться дальше, если:
- Можешь объяснить, почему `has_permission` и `has_object_permission` — разные методы
- Знаешь, когда вызывается `has_object_permission` (подсказка: только при `get_object()`)
- Понимаешь разницу между HTTP 401 и 403 в контексте этого проекта
- Можешь написать новый permission class, следуя паттерну `_AuthenticatedPermission`

---

## Фаза 6 — Тестирование (3-5 дней)

### pytest vs Jest

| Концепция | Jest (TS) | pytest (Python) |
|-----------|----------|----------------|
| Тест-файл | `*.test.ts` | `test_*.py` или `*_test.py` |
| Test suite | `describe('...', () => {...})` | `class TestSomething:` или просто функции |
| Test case | `it('...', () => {...})` | `def test_something(self):` |
| Assertions | `expect(x).toBe(y)` | `assert x == y` |
| Mocks | `jest.fn()`, `jest.mock()` | `MagicMock()`, `patch()` |
| Setup | `beforeEach(() => {...})` | `@pytest.fixture` или `setup_method` |
| Async тесты | `it('...', async () => {...})` | `@pytest.mark.asyncio async def test_...` |
| DB тесты | нужна настройка | `@pytest.mark.django_db` |
| Coverage | `jest --coverage` | `pytest --cov=apps` |

### Два типа тестов в этом проекте

#### 1. Unit тесты без БД (быстрые)

```python
# Аналог Jest unit тестов с моками
# apps/core/tests/test_permissions.py

from unittest.mock import MagicMock
from rest_framework.test import APIRequestFactory
from apps.core.permissions import IsCompanyMember

factory = APIRequestFactory()

def _make_user(role, company_id=None, authenticated=True):
    """Мок пользователя без обращения к БД"""
    user = MagicMock()
    user.role = role
    user.company_id = company_id
    user.is_authenticated = authenticated
    return user

class TestIsCompanyMember:
    perm = IsCompanyMember()

    def test_employee_with_company_allowed(self):
        request = factory.get('/')
        request.user = _make_user('employee', company_id=1)

        result = self.perm.has_permission(request, MagicMock())

        assert result is True  # аналог: expect(result).toBe(true)

    def test_guest_denied(self):
        request = factory.get('/')
        request.user = _make_user('guest')

        assert self.perm.has_permission(request, MagicMock()) is False
```

```typescript
// TypeScript аналог (Jest)
describe('IsCompanyMember', () => {
  it('should allow employee with company', () => {
    const user = { role: 'employee', companyId: 1, isAuthenticated: true };
    const result = checkPermission(user);
    expect(result).toBe(true);
  });
});
```

#### 2. Интеграционные тесты с БД

```python
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

User = get_user_model()

@pytest.mark.django_db  # этот декоратор критичен — без него нет доступа к БД
class TestBookingAPI:

    def setup_method(self):
        """Аналог beforeEach"""
        self.client = APIClient()

        # Создать тестовую компанию и пользователя
        from apps.companies.models import Company
        self.company = Company.objects.create(name='Test Corp')

        self.user = User.objects.create_user(
            email='employee@test.com',
            password='testpass123',
            role='employee',
            company=self.company
        )
        self.client.force_authenticate(user=self.user)  # обойти JWT

    def test_list_bookings_returns_200(self):
        response = self.client.get('/api/v1/bookings/bookings/')
        assert response.status_code == status.HTTP_200_OK

    def test_unauthenticated_returns_401(self):
        unauthenticated_client = APIClient()  # без аутентификации
        response = unauthenticated_client.get('/api/v1/bookings/bookings/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_cannot_create_resource(self):
        response = self.client.post('/api/v1/bookings/resources/', {
            'name': 'New Room',
            'resource_type': 'meeting_room',
        })
        assert response.status_code == status.HTTP_403_FORBIDDEN
```

### Fixtures — переиспользуемый setup

```python
# conftest.py (в корне или в apps/<app>/tests/)
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Test Company')

@pytest.fixture
def employee_user(db, company):
    return User.objects.create_user(
        email='emp@test.com',
        password='pass',
        role='employee',
        company=company
    )

@pytest.fixture
def auth_client(employee_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=employee_user)
    return client

# Использование в тесте:
@pytest.mark.django_db
def test_something(auth_client, company):
    response = auth_client.get('/api/v1/...')
    assert response.status_code == 200
```

```typescript
// TypeScript аналог (Jest + beforeEach)
let company: Company;
let client: TestClient;

beforeEach(async () => {
  company = await Company.create({ name: 'Test Company' });
  const user = await User.create({ role: 'employee', companyId: company.id });
  client = new TestClient({ user });
});
```

### Запуск тестов

```bash
# Все тесты
pytest

# Конкретный файл
pytest apps/core/tests/test_permissions.py

# Конкретный тест-класс
pytest apps/core/tests/test_permissions.py::TestIsCompanyMember

# Конкретный тест
pytest apps/core/tests/test_permissions.py::TestIsCompanyMember::test_guest_denied

# С фильтром по имени
pytest -k "test_guest"

# Показать print() вывод
pytest -s

# Без coverage (быстрее)
pytest --no-cov

# В Docker
docker-compose -f docker-compose.local.yml exec backend pytest
```

### Что тестировать в первую очередь

По конвенции проекта — всегда проверяй три сценария:

```python
# 1. Неаутентифицированный → 401
def test_unauthenticated(self):
    client = APIClient()
    response = client.get('/api/v1/...')
    assert response.status_code == 401

# 2. Неправильная роль / компания → 403
def test_wrong_role(self, employee_client):
    response = employee_client.post('/api/v1/admin-only-endpoint/', data)
    assert response.status_code == 403

# 3. Изоляция компаний → пустой результат
def test_company_isolation(self, employee_from_company_a):
    # company B создала данные
    # employee из company A не должен их видеть
    response = employee_from_company_a.get('/api/v1/...')
    assert response.data['results'] == []
```

### Практическое упражнение фазы 6

1. Запустить `pytest apps/core/tests/test_permissions.py` и убедиться что все тесты проходят
2. Прочитать каждый тест-класс в `test_permissions.py` — теперь это должно быть полностью понятно
3. Написать интеграционный тест для `GET /api/v1/bookings/resources/` с тремя сценариями: 401, 200 для employee, 200 для admin

### Критерий завершения фазы 6

Ты готов двигаться дальше, если:
- Понимаешь разницу между `MagicMock` тестами и `@pytest.mark.django_db` тестами
- Можешь написать fixture в `conftest.py` и использовать её в тесте
- Знаешь зачем `force_authenticate()` вместо реального JWT в тестах
- Все существующие тесты проекта проходят на твоей машине

---

## Фаза 7 — Docker и DevOps (2-3 дня)

### Docker в этом проекте

```yaml
# docker-compose.local.yml — два сервиса
services:
  db:                              # PostgreSQL 15
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: newlevelhub_db
      POSTGRES_USER: nlh_user
    ports:
      - "5432:5432"
    healthcheck:                   # backend ждёт пока db не готова
      test: ["CMD-SHELL", "pg_isready"]
      ...

  backend:                         # Django + DRF
    build:
      context: .
      dockerfile: Dockerfile
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/app                     # hot reload — изменения кода видны без rebuild
    ports:
      - "8000:8000"
    env_file:
      - .env                       # переменные окружения
    depends_on:
      db:
        condition: service_healthy # ждёт healthcheck db
```

### Ключевые Docker команды для этого проекта

```bash
# Старт
docker-compose -f docker-compose.local.yml up -d --build

# Логи в реальном времени
docker-compose -f docker-compose.local.yml logs -f backend

# Войти в контейнер (аналог ssh)
docker-compose -f docker-compose.local.yml exec backend bash

# Запустить команду в контейнере
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate
docker-compose -f docker-compose.local.yml exec backend python manage.py shell
docker-compose -f docker-compose.local.yml exec backend pytest

# Остановить
docker-compose -f docker-compose.local.yml down

# Остановить и удалить volumes (сбросить БД!)
docker-compose -f docker-compose.local.yml down -v

# Rebuild только backend (после изменения requirements.txt)
docker-compose -f docker-compose.local.yml up -d --build backend
```

### Переменные окружения

```bash
# .env (создаётся из .env.example)
SECRET_KEY=your-secret-key
POSTGRES_DB=newlevelhub_db
POSTGRES_USER=nlh_user
POSTGRES_PASSWORD=nlh_password
POSTGRES_HOST=db          # имя сервиса в docker-compose, не localhost!
POSTGRES_PORT=5432
CELERY_BROKER_URL=redis://redis:6379/0
DEBUG=True
```

> Важно: `POSTGRES_HOST=db`, а не `localhost`. Внутри Docker сеть сервисы общаются по имени контейнера.

### CI/CD pipeline проекта

```
PR в main
    |
    v
[CI — .github/workflows/ci.yml]
    ├── flake8 .                    # линтинг
    ├── pytest + coverage           # тесты
    └── Docker build & push         # сборка образа

Push в main
    |
    v
[CD — .github/workflows/cd.yml]
    ├── SSH на prod-сервер
    ├── docker pull <image>
    ├── docker compose up -d --force-recreate
    └── python manage.py migrate    # автоматически
```

### Что нужно знать из Docker для этого проекта

**Минимальный набор:**
- Понимать что такое image vs container
- Знать команды `up`, `down`, `exec`, `logs`, `build`
- Понимать volumes (`.:/app` — монтирование кода)
- Понимать env_file и переменные окружения

**Можно пропустить:**
- Написание Dockerfile с нуля (он уже есть)
- Kubernetes
- Docker Swarm
- Multi-stage builds (в базовом сценарии)

### Практическое упражнение фазы 7

1. Полностью остановить и поднять проект (`down` + `up --build`) — убедиться что всё работает
2. Посмотреть логи в реальном времени и сделать HTTP запрос — увидеть access log
3. Войти в контейнер backend, запустить Django shell, создать тестовую запись

### Критерий завершения фазы 7

Ты готов двигаться дальше, если:
- Можешь с нуля поднять проект через Docker без помощи
- Понимаешь почему `POSTGRES_HOST=db`, а не `localhost`
- Знаешь как посмотреть логи конкретного сервиса
- Понимаешь общий flow CI/CD этого проекта

---

## Фаза 8 — Celery и асинхронность (2-3 дня)

### Зачем Celery

```
Синхронный запрос (плохо для долгих операций):
User → HTTP Request → Django → [долгая операция] → HTTP Response (таймаут!)

Асинхронный с Celery (правильно):
User → HTTP Request → Django → [отправить задачу в очередь] → HTTP 202 Accepted
                                        |
                                        v
                               Redis (брокер) ← задача
                                        |
                                        v
                               Celery Worker → [выполнить задачу]
                                        |
                                        v
                               Redis (result backend) ← результат
```

```typescript
// TypeScript аналог — BullMQ или similar
import { Queue } from 'bullmq';

const emailQueue = new Queue('emails');

// В обработчике запроса
await emailQueue.add('send-verification', { userId: user.id });
return { status: 202, message: 'Email will be sent' };

// Worker (отдельный процесс)
const worker = new Worker('emails', async (job) => {
  await sendVerificationEmail(job.data.userId);
});
```

### Структура Celery в проекте

```python
# config/celery.py — точка входа
app = Celery('newlevelhub')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()  # автоматически находит tasks.py во всех apps

# apps/notifications/tasks.py — типичный task
from celery import shared_task

@shared_task                   # регистрирует функцию как Celery task
def create_notification(user_id, notification_type, title, body=''):
    # Этот код выполняется в воркере, не в веб-процессе
    from apps.notifications.models import Notification
    from apps.users.models import User
    user = User.objects.get(id=user_id)
    Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        body=body,
    )

# Вызов из views.py
def perform_create(self, serializer):
    booking = serializer.save(user=self.request.user)
    # Отправить задачу в очередь (не блокирует ответ пользователю)
    create_notification.delay(
        user_id=self.request.user.id,
        notification_type='booking_confirmed',
        title=f'Booking confirmed: {booking.resource.name}',
    )
```

### Запуск Celery локально

```bash
# Нужен Redis запущенный отдельно или через Docker
# Добавить в docker-compose.local.yml сервис redis (его пока нет — TODO)

# Воркер (обрабатывает задачи)
celery -A config.celery worker -l info

# Beat (планировщик — запускает задачи по расписанию)
celery -A config.celery beat -l info

# Запустить всё вместе (только для dev)
celery -A config.celery worker --beat -l info
```

### Типы задач

```python
# 1. Немедленная задача (fire and forget)
create_notification.delay(user_id=1, ...)

# 2. Задача с задержкой
from datetime import timedelta
send_reminder.apply_async(args=[booking_id], countdown=3600)  # через 1 час

# 3. Задача по расписанию (в settings.py)
CELERY_BEAT_SCHEDULE = {
    'cancel-overdue-bookings': {
        'task': 'apps.bookings.tasks.cancel_overdue_bookings',
        'schedule': 300.0,  # каждые 5 минут
    },
}
```

### Практическое упражнение фазы 8

1. Прочитать `apps/notifications/tasks.py` — понять структуру stub-задач
2. Найти все `tasks.py` в проекте — понять какие задачи ждут реализации
3. Реализовать `create_notification` task (заглушку превратить в рабочий код)

### Критерий завершения фазы 8

Ты готов двигаться дальше, если:
- Понимаешь разницу между `.delay()` и `.apply_async()`
- Знаешь зачем нужен Redis в этой схеме
- Понимаешь разницу между worker и beat
- Можешь объяснить почему нельзя передавать ORM объекты в Celery задачи (только IDs)

---

## Фаза 9 — Первые реальные задачи

### Уровень 1 — Простые (1-3 дня каждая)

Эти задачи не требуют изменения архитектуры, только реализация логики.

#### 1.1 Booking conflict validation

**Файл:** `apps/bookings/serializers.py` → `BookingCreateSerializer.validate()`

**Что нужно:** добавить проверку — нельзя забронировать ресурс если в это время уже есть активное бронирование.

```python
# Текущее состояние (TODO в коде):
def validate(self, attrs):
    # TODO: проверка конфликтов (overlap)
    if attrs['start_time'] >= attrs['end_time']:
        raise serializers.ValidationError('start_time must be before end_time')
    return attrs

# Что нужно реализовать:
def validate(self, attrs):
    if attrs['start_time'] >= attrs['end_time']:
        raise serializers.ValidationError('start_time must be before end_time')

    # Проверка пересечения временных интервалов
    overlapping = Booking.objects.filter(
        resource=attrs['resource'],
        status='confirmed',
        start_time__lt=attrs['end_time'],   # существующее начинается до нашего конца
        end_time__gt=attrs['start_time'],   # существующее заканчивается после нашего начала
    )
    if overlapping.exists():
        raise serializers.ValidationError('This time slot is already booked')
    return attrs
```

#### 1.2 Реализация create_notification task

**Файл:** `apps/notifications/tasks.py`

**Что нужно:** заменить `pass` на реальную логику создания уведомления.

#### 1.3 Schedule endpoint для ресурса

**Файл:** `apps/bookings/views.py` → `ResourceViewSet.schedule()`

**Что нужно:** принять query param `?date=2024-01-15`, вернуть все бронирования и блоки за этот день.

```python
# Текущий TODO:
@action(detail=True, methods=['get'], url_path='schedule')
def schedule(self, request, pk=None):
    # TODO: получить дату из query param, вернуть bookings + blocks за этот день
    resource = self.get_object()
    ...
```

### Уровень 2 — Средние (3-5 дней каждая)

#### 2.1 Email verification flow

**Файлы:** `apps/users/views.py`, `apps/users/models.py` (уже есть `EmailVerificationToken`)

**Что нужно:**
- При регистрации создавать `EmailVerificationToken` и отправлять email (через Celery task)
- Endpoint `POST /api/v1/auth/verify-email/` принимает token UUID и помечает `is_email_verified=True`
- Токен действует 24 часа (поле `expires_at`)

#### 2.2 Password reset flow

**Файлы:** `apps/users/views.py`, `apps/users/models.py` (уже есть `PasswordResetToken`)

Аналогично email verification, но для смены пароля.

#### 2.3 InviteRegistrationSerializer

**Файл:** `apps/companies/serializers.py`

**Что нужно:** при регистрации по invite-токену автоматически:
- Привязать пользователя к компании
- Установить `role='employee'`
- Пометить invite как использованный

### Уровень 3 — Сложные (1-2 недели каждая)

#### 3.1 Recurring bookings logic

**Файл:** `apps/bookings/tasks.py`

**Что нужно:** Celery beat задача, которая ежедневно проверяет активные `RecurringBooking` и создаёт `Booking` записи на следующие N дней.

#### 3.2 Analytics dashboards

**Файл:** `apps/analytics/views.py` (сейчас возвращает placeholder данные)

**Что нужно:** реальные запросы с агрегацией: количество бронирований по типу ресурса, загруженность по дням, активность сотрудников.

#### 3.3 CompanyViewSet.deactivate action

**Файл:** `apps/companies/views.py`

**Что нужно:** при деактивации компании → деактивировать всех её сотрудников (`is_active=False`).

### Порядок рекомендуемого старта

```
Неделя 1: Прочитать CLAUDE.md + запустить проект + фаза 0-1
Неделя 2: Фаза 2-3 (DRF + архитектура проекта)
Неделя 3: Фаза 4-5 (БД + auth)
Неделя 4: Фаза 6 (тесты) + первая реальная задача (1.1 booking conflict)
Неделя 5+: Задачи уровня 2 параллельно с углублением
```

---

## Шпаргалка

### Ключевые команды

```bash
# Docker
docker-compose -f docker-compose.local.yml up -d --build    # запустить
docker-compose -f docker-compose.local.yml down              # остановить
docker-compose -f docker-compose.local.yml logs -f backend   # логи
docker-compose -f docker-compose.local.yml exec backend bash # войти в контейнер

# Django
python manage.py runserver                           # dev сервер
python manage.py makemigrations <app>                # создать миграцию
python manage.py migrate                             # применить миграции
python manage.py createsuperuser                     # создать admin
python manage.py shell                               # интерактивный Python с Django

# Тесты
pytest                                               # все тесты
pytest apps/core/tests/test_permissions.py           # конкретный файл
pytest -k "test_guest"                               # фильтр по имени
pytest -s --no-cov                                   # без coverage, с print output

# Celery
celery -A config.celery worker -l info               # запустить воркер
celery -A config.celery beat -l info                 # запустить планировщик
```

### Структура нового endpoint

Создание нового endpoint — чеклист:

```python
# 1. apps/<app>/models.py — добавить/изменить модель
class MyModel(TimeStampedModel):
    company = models.ForeignKey('companies.Company', ...)  # обязателен для tenant
    ...

# 2. Создать и применить миграцию
# python manage.py makemigrations <app> && python manage.py migrate

# 3. apps/<app>/serializers.py — сериализатор
class MyModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = MyModel
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

# 4. apps/<app>/views.py — ViewSet
@extend_schema_view(...)  # OpenAPI аннотации — обязательно!
class MyModelViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = MyModelSerializer
    permission_classes = [IsCompanyMember]  # выбрать правильный класс

    queryset = MyModel.objects.all()

# 5. apps/<app>/urls.py — зарегистрировать роутер
router.register(r'my-models', views.MyModelViewSet)

# 6. apps/<app>/tests/ — тесты (минимум: 401, 403, 200)
```

### Паттерны кода — быстрый справочник

```python
# Получить текущего пользователя в view
user = self.request.user
company = self.request.user.company

# Получить текущего пользователя в serializer
user = self.context['request'].user

# Сохранить с дополнительными полями
serializer.save(user=request.user, company=request.user.company)

# Soft delete
instance.soft_delete()   # пометить удалённым
instance.restore()       # восстановить

# Получить включая удалённые
MyModel.all_objects.all()
MyModel.objects.all_with_deleted()

# Q объекты для OR
from django.db.models import Q
MyModel.objects.filter(Q(status='a') | Q(status='b'))

# Агрегации
from django.db.models import Count, Sum, Avg
Booking.objects.filter(company=company).aggregate(
    total=Count('id'),
    avg_duration=Avg('end_time') - Avg('start_time'),
)

# Аннотации
from django.db.models import Count
Company.objects.annotate(employee_count=Count('members'))
```

### Карта файлов по задаче

| Задача | Файл |
|--------|------|
| Добавить поле в модель | `apps/<app>/models.py` |
| Изменить что возвращает API | `apps/<app>/serializers.py` |
| Изменить логику доступа | `apps/<app>/views.py` → `get_permissions()` |
| Добавить фильтр | `apps/<app>/filters.py` |
| Добавить фоновую задачу | `apps/<app>/tasks.py` |
| Добавить новый URL | `apps/<app>/urls.py` |
| Изменить глобальные настройки | `config/settings/base.py` |
| Добавить тест | `apps/<app>/tests/test_*.py` |

### Типичные ошибки новичка

| Ошибка | Правильно |
|--------|----------|
| Inline permission logic в view | Всегда использовать классы из `apps.core.permissions` |
| Забыть `@extend_schema` на view | Аннотировать все views для OpenAPI |
| Создать модель без `TimeStampedModel` | Все новые модели наследуют `TimeStampedModel` |
| Не добавить `company` FK в tenant-модель | Все tenant-scoped модели имеют `company = ForeignKey(...)` |
| Использовать `CompanyQuerySetMixin` | Использовать `CompanyIsolationMixin` (новый код) |
| Делать `import` внутри класса (ради circular imports) | Использовать строку: `ForeignKey('companies.Company', ...)` |
| Не запустить тесты перед PR | `pytest` должен проходить всегда |
| `docker compose` вместо `docker-compose` | В этом проекте Compose v1: `docker-compose` |

### URL быстрого доступа (локальная разработка)

| URL | Назначение |
|-----|-----------|
| `http://localhost:8000/api/docs/` | Swagger UI — основной инструмент |
| `http://localhost:8000/api/redoc/` | ReDoc — альтернативная документация |
| `http://localhost:8000/api/schema/` | Raw OpenAPI JSON/YAML |
| `http://localhost:8000/admin/` | Django Admin |

---

> Последнее обновление: апрель 2026. При изменении стека или архитектуры — обновить этот документ.
