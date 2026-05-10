[
  {
    "identifier": "DEV-50",
    "title": "Permission-классы и CompanyIsolationMixin",
    "description": "Создать переиспользуемые DRF Permission-классы для ролей и миксин изоляции данных по компании.\n\nЭто фундамент всего проекта — почти все остальные тикеты зависят от пермишенов. Пока этот тикет не готов, ViewSet-ы остаются без контроля доступа.\n\nСейчас User.role есть, но единой системы проверки прав нет.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend", "foundation"],
    "area": "auth",
    "parallel": false,
    "dependsOn": [],
    "acceptanceCriteria": [
      "Создан Permission-класс IsSuperAdmin — пропускает только role='superadmin'",
      "Создан Permission-класс IsCompanyAdmin — пропускает role='company_admin'",
      "Создан Permission-класс IsCompanyMember — пропускает role in ('company_admin', 'employee')",
      "Создан Permission-класс IsCompanyAdminOrReadOnly — company_admin может писать, остальные — только читать",
      "Создан миксин CompanyIsolationMixin — фильтрует queryset по company текущего пользователя, суперадмин видит всё",
      "Гость коворкинга (role='guest') не имеет доступа к CRM, HR, внутренним объявлениям компании — 403",
      "Суперадмин имеет доступ ко всем эндпоинтам без ограничений",
      "Неавторизованный запрос — 401 на все защищённые эндпоинты"
    ]
  },
  {
    "identifier": "DEV-51",
    "title": "Подключение пермишенов ко всем ViewSet-ам + unit-тесты",
    "description": "Применить созданные в DEV-50 Permission-классы и CompanyIsolationMixin ко всем существующим ViewSet-ам. Написать unit-тесты.\n\nБез этого тикета ViewSet-ы работают без проверки ролей — любой авторизованный пользователь имеет полный доступ.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend"],
    "area": "auth",
    "parallel": false,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "Пермишены подключены к CompanyViewSet, BookingViewSet, ResourceViewSet, BoardViewSet, TaskViewSet, FolderViewSet, FileViewSet, LeaveRequestViewSet, GuestPassViewSet, ServiceRequestViewSet, AnnouncementViewSet, NotificationViewSet",
      "CompanyIsolationMixin подключен ко всем ViewSet-ам, работающим в контексте компании",
      "Написаны unit-тесты на каждый Permission-класс: позитивный и негативный кейс (superadmin, company_admin, employee, guest, anonymous)",
      "Тесты проверяют: 401 для anonymous, 403 для неразрешённых ролей, 200/201 для разрешённых",
      "Тесты запускаются через pytest и проходят"
    ]
  },
  {
    "identifier": "DEV-52",
    "title": "Email Verification — flow подтверждения почты",
    "description": "Реализовать сквозной flow подтверждения email после регистрации.\n\nМодель EmailVerificationToken уже есть. Нужно: генерация токена при регистрации, отправка письма (Celery task), эндпоинт верификации, ограничение доступа до подтверждения.\n\nТекущее состояние: view verify_email и serializer EmailVerifySerializer — заглушки (TODO).\n\nНезависим от других тикетов — можно брать параллельно с DEV-53, DEV-55, DEV-56.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend", "auth"],
    "area": "auth",
    "parallel": true,
    "dependsOn": [],
    "acceptanceCriteria": [
      "POST /api/v1/auth/register/ создаёт пользователя с is_email_verified=False и генерирует EmailVerificationToken",
      "После регистрации Celery task отправляет письмо с ссылкой вида /verify-email?token=<uuid>",
      "GET /api/v1/auth/email/verify/?token=<uuid> — при валидном токене is_email_verified=True, токен помечается is_used=True",
      "Повторный вызов с тем же токеном — 400 'Token already used'; истёкший (>24ч) — 400 'Token expired'; несуществующий — 404",
      "Пользователь с is_email_verified=False не может создавать бронирования, работать в CRM — 403 'Email not verified'",
      "POST /api/v1/auth/email/resend/ — повторная отправка (старый токен аннулируется); rate limit: 3 запроса за 10 мин — иначе 429",
      "Фронт-интеграция: на /verify-email фронт вызывает эндпоинт с token из query params, при успехе — редирект на дашборд"
    ]
  },
  {
    "identifier": "DEV-53",
    "title": "Password Reset — восстановление пароля через email",
    "description": "Реализовать полный flow сброса пароля: запрос ссылки → письмо → переход по ссылке → новый пароль.\n\nМодель PasswordResetToken есть. Views password_reset_request и password_reset_confirm — TODO заглушки.\n\nНезависим от других тикетов — можно брать параллельно с DEV-52, DEV-55, DEV-56.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend", "auth"],
    "area": "auth",
    "parallel": true,
    "dependsOn": [],
    "acceptanceCriteria": [
      "POST /api/v1/auth/password/reset/ с {email} — если пользователь существует, создаётся PasswordResetToken и Celery task с письмом; если email не найден — всё равно 200",
      "Письмо содержит ссылку /reset-password?token=<uuid>",
      "POST /api/v1/auth/password/reset/confirm/ с {token, new_password} — при валидном токене пароль обновляется, токен is_used=True",
      "Истёкший токен (>1ч) — 400 'Token expired'; использованный — 400 'Token already used'",
      "Пароль проходит стандартные Django-валидаторы (минимум 8 символов)",
      "После успешного сброса все refresh-токены инвалидируются (SimpleJWT blacklist)",
      "Rate limit: не более 5 запросов reset за 15 минут с одного IP — иначе 429",
      "Фронт-интеграция: на /reset-password форма нового пароля, при успехе редирект на /login"
    ]
  },
  {
    "identifier": "DEV-54",
    "title": "Invite Registration — регистрация сотрудника по инвайт-ссылке",
    "description": "Реализовать регистрацию сотрудника компании через инвайт-ссылку.\n\nМодель Invitation уже есть в apps/companies. InviteRegistrationSerializer — заглушка.\n\nFlow: админ компании создаёт инвайт (DEV-64) → сотрудник переходит по ссылке → регистрируется → привязывается к компании.\n\nТикет реализует серверную часть приёма инвайта. Создание инвайтов — DEV-64.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend", "auth"],
    "area": "auth",
    "parallel": true,
    "dependsOn": ["DEV-64"],
    "acceptanceCriteria": [
      "GET /api/v1/auth/register/invite/?token=<uuid> — данные инвайта (company_name, email, role) или 400 если невалиден/истёк",
      "POST /api/v1/auth/register/invite/ с {token, first_name, last_name, password, phone?} — создаёт пользователя",
      "Пользователь привязывается к company из инвайта, role устанавливается из инвайта; инвайт помечается is_used=True",
      "Если инвайт истёк (>72ч) — 400; уже использован — 400; email уже зарегистрирован — 400",
      "Проверяется лимит сотрудников компании по тарифу — если лимит исчерпан, 400 'Employee limit reached'",
      "После регистрации автоматически отправляется email verification; возвращаются JWT-токены для мгновенного входа",
      "Фронт-интеграция: на /invite?token=... GET для проверки инвайта → форма регистрации → POST"
    ]
  },
  {
    "identifier": "DEV-55",
    "title": "Профиль пользователя — аватар и расширение полей",
    "description": "Доработать эндпоинты профиля: загрузка/смена аватара, поле position, просмотр роли и компании.\n\nUserProfileSerializer и UserProfileUpdateSerializer уже есть.\n\nНезависим от других тикетов — можно брать параллельно с DEV-52, DEV-53, DEV-56.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend"],
    "area": "auth",
    "parallel": true,
    "dependsOn": [],
    "acceptanceCriteria": [
      "PATCH /api/v1/auth/me/update/ принимает multipart/form-data с полем avatar (JPEG, PNG, WebP; max 5 МБ; ресайз до 400×400 px)",
      "Аватар сохраняется в MEDIA_ROOT/avatars/<user_id>/ — старый файл удаляется при замене",
      "DELETE /api/v1/auth/me/avatar/ — удаляет текущий аватар, поле avatar=null",
      "GET /api/v1/auth/me/ возвращает: id, email, first_name, last_name, phone, position, avatar (URL), role, company {id, name}, is_email_verified, date_joined",
      "PATCH позволяет обновить: first_name, last_name, phone, position — но НЕ email, role, company",
      "Аватар доступен по прямому URL без авторизации (публичный media)"
    ]
  },
  {
    "identifier": "DEV-56",
    "title": "Auto-Logout — JWT TTL и 'Запомнить меня'",
    "description": "Настроить JWT TTL, реализовать 'Запомнить меня' и middleware обновления last_activity.\n\nНезависим от других тикетов — можно брать параллельно с DEV-52, DEV-53, DEV-55.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend", "frontend-integration"],
    "area": "auth",
    "parallel": true,
    "dependsOn": [],
    "acceptanceCriteria": [
      "JWT access token TTL = 15 минут; refresh token TTL = 7 дней (обычный вход) или 30 дней ('Запомнить меня')",
      "POST /api/v1/auth/login/ принимает поле remember_me (bool) — влияет на TTL refresh token",
      "Middleware: при каждом авторизованном запросе обновляет user.last_login",
      "Настраиваемые параметры: ACCESS_TOKEN_LIFETIME, REFRESH_TOKEN_LIFETIME, REMEMBER_ME_LIFETIME — через env/settings",
      "При logout — refresh token добавляется в blacklist (SimpleJWT Outstanding/Blacklist)",
      "Фронт-интеграция: фронт хранит access в памяти, refresh в httpOnly cookie; при 401 → refresh; при ошибке refresh → /login"
    ]
  },
  {
    "identifier": "DEV-57",
    "title": "Суперадмин — список пользователей с фильтрами",
    "description": "Расширить UserListView и UserDetailView: фильтры, сортировка, детальная информация о пользователе.\n\nUserListView и UserDetailView уже есть. Нужны пермишены из DEV-50 для ограничения доступа только суперадмином.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend"],
    "area": "auth",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "GET /api/v1/auth/users/ — фильтры: role, company_id, is_active, search (по email, имени); ordering (date_joined, last_login)",
      "GET /api/v1/auth/users/<id>/ — полная информация: профиль + last_login + количество бронирований/задач",
      "Пагинация, поиск и сортировка работают через django-filter / DRF filters",
      "Все эндпоинты доступны только суперадмину — остальные роли получают 403"
    ]
  },
  {
    "identifier": "DEV-58",
    "title": "Суперадмин — блокировка и разблокировка пользователей",
    "description": "Реализовать блокировку/разблокировку пользователей суперадмином с инвалидацией сессий.\n\nИспользует IsSuperAdmin пермишен из DEV-50. Логически продолжает DEV-57 (список пользователей).",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend"],
    "area": "auth",
    "parallel": true,
    "dependsOn": ["DEV-50", "DEV-57"],
    "acceptanceCriteria": [
      "POST /api/v1/auth/users/<id>/block/ — суперадмин блокирует пользователя (is_active=False), все сессии инвалидируются",
      "POST /api/v1/auth/users/<id>/unblock/ — суперадмин разблокирует (is_active=True)",
      "Заблокированный при логине получает 403 'Account is blocked'",
      "Заблокированный не может использовать существующий access token — 401",
      "Только суперадмин — остальные роли 403"
    ]
  },
  {
    "identifier": "DEV-59",
    "title": "Суперадмин — импенсонация (вход от имени пользователя)",
    "description": "Реализовать возможность суперадмину войти от имени любого пользователя для поддержки/дебага.\n\nИспользует IsSuperAdmin пермишен из DEV-50. Логически продолжает DEV-57 (список пользователей).\n\nМожно брать параллельно с DEV-58.",
    "block": "Блок 1 — Пользователи и роли",
    "labels": ["backend"],
    "area": "auth",
    "parallel": true,
    "dependsOn": ["DEV-50", "DEV-57"],
    "acceptanceCriteria": [
      "POST /api/v1/auth/users/<id>/impersonate/ — суперадмин получает JWT-токены от имени указанного пользователя",
      "В токене добавляется claim impersonated_by=<superadmin_id> для аудита",
      "Нельзя импенсонировать другого суперадмина — 400",
      "Только суперадмин — остальные роли 403"
    ]
  },
  {
    "identifier": "DEV-60",
    "title": "Company — создание и получение",
    "description": "Довести до рабочего состояния создание и получение компаний. CompanyViewSet уже есть.\n\nСуперадмин создаёт компанию с тарифом и лимитами. Использует пермишены из DEV-50.\n\nЭто базовый тикет Блока 2 — от него зависят: DEV-61 (деактивация), DEV-62 (тарифы), DEV-64 (инвайты), DEV-65 (сотрудники), DEV-67 (настройки), DEV-68 (онбординг).",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": false,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/companies/ (superadmin) — создаёт компанию: name, description, logo (file), floor, office_number, contact_email, contact_phone, plan, max_employees, storage_limit_gb",
      "При создании автоматически создаётся CompanySettings (OneToOne)",
      "GET /api/v1/companies/ — суперадмин видит все; company_admin/employee видит только свою; guest — 403",
      "GET /api/v1/companies/<id>/ — детали: все поля + employee_count + storage_used",
      "PATCH — суперадмин может менять все поля; company_admin — только name, description, logo, contact_*",
      "Фильтры на GET list: plan, is_active, search (по name)"
    ]
  },
  {
    "identifier": "DEV-61",
    "title": "Company — деактивация, активация и удаление",
    "description": "Реализовать деактивацию/активацию компании с каскадными эффектами и удаление с подтверждением.\n\nПродолжение DEV-60 (Company CRUD). Каскадная деактивация затрагивает сотрудников и бронирования.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": true,
    "dependsOn": ["DEV-60"],
    "acceptanceCriteria": [
      "POST /api/v1/companies/<id>/deactivate/ — is_active=False, все сотрудники получают is_active=False, все активные бронирования отменяются",
      "POST /api/v1/companies/<id>/activate/ — обратное: is_active=True, сотрудники восстанавливаются",
      "DELETE /api/v1/companies/<id>/ — только суперадмин, требует ?confirm=true, каскадно удаляет все данные",
      "Без ?confirm=true — 400 'Confirmation required'",
      "Фронт-интеграция: на /admin/companies кнопки деактивации/удаления с модалкой подтверждения"
    ]
  },
  {
    "identifier": "DEV-62",
    "title": "Тарифные планы — дефолтные лимиты и эндпоинт limits",
    "description": "Реализовать дефолтные значения лимитов по тарифам и эндпоинт для просмотра текущего использования.\n\nНужна готовая модель Company из DEV-60. Можно брать параллельно с DEV-61, DEV-64, DEV-65, DEV-67.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": true,
    "dependsOn": ["DEV-60"],
    "acceptanceCriteria": [
      "При создании компании: если max_employees/max_boards/storage_limit_gb не заданы — подставляются дефолты по plan",
      "Дефолты basic: max_employees=10, max_boards=1, storage_limit_gb=5",
      "Дефолты standard: max_employees=30, max_boards=5, storage_limit_gb=20",
      "Дефолты premium: max_employees=9999, max_boards=9999, storage_limit_gb=100",
      "GET /api/v1/companies/<id>/limits/ — employees {current, max}, boards {current, max}, storage {used_gb, limit_gb}",
      "Суперадмин может переопределить лимиты через PATCH /api/v1/companies/<id>/"
    ]
  },
  {
    "identifier": "DEV-63",
    "title": "Тарифные планы — проверка лимитов при операциях",
    "description": "Внедрить проверку лимитов тарифа в ключевые точки: приглашение сотрудников, создание досок, загрузка файлов.\n\nТребует готовые дефолтные лимиты из DEV-62. Проверки вызываются в InvitationCreateSerializer, BoardViewSet.create, FileViewSet.create.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": false,
    "dependsOn": ["DEV-62"],
    "acceptanceCriteria": [
      "При приглашении сотрудника (InvitationCreateSerializer) проверяется: текущее кол-во < max_employees — иначе 400 'Employee limit reached'",
      "При создании CRM-доски (BoardViewSet.create) проверяется: текущее кол-во досок < max_boards — иначе 400",
      "При загрузке файла (FileViewSet.create) проверяется: текущий storage_used < storage_limit_gb — иначе 400",
      "При приближении к лимиту (80% и 95%) создаётся системное уведомление для company_admin"
    ]
  },
  {
    "identifier": "DEV-64",
    "title": "Invitation System — создание инвайтов и email-рассылка",
    "description": "Довести до рабочего состояния создание приглашений с email.\n\nInvitationViewSet и InvitationCreateSerializer существуют, email и resend — TODO.\n\nНужна готовая Company из DEV-60. После этого тикета DEV-54 (приём инвайта) может быть реализован.\n\nМожно брать параллельно с DEV-61, DEV-62, DEV-65, DEV-67.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": true,
    "dependsOn": ["DEV-60"],
    "acceptanceCriteria": [
      "POST /api/v1/companies/<company_id>/invitations/ с {email, role} — создаёт Invitation, expires_at = now + 72ч",
      "Celery task отправляет email с ссылкой /invite?token=<uuid>",
      "Нельзя пригласить уже зарегистрированный email — 400; нельзя при активном инвайте — 400",
      "GET /api/v1/companies/<company_id>/invitations/ — список с фильтрами: is_used, is_expired",
      "POST .../invitations/<id>/revoke/ — отзыв (is_used=True)",
      "POST .../invitations/<id>/resend/ — старый токен аннулируется, новый создаётся, email отправляется",
      "Инвайт на роль company_admin может создать только суперадмин",
      "Фронт-интеграция: на /company/settings/members кнопка 'Пригласить' → форма с email и ролью"
    ]
  },
  {
    "identifier": "DEV-65",
    "title": "Управление сотрудниками — список и фильтры",
    "description": "Реализовать API списка сотрудников компании для company_admin.\n\nЭндпоинт members в CompanyViewSet существует, нужно расширить. Требуется готовая Company из DEV-60.\n\nМожно брать параллельно с DEV-61, DEV-62, DEV-64, DEV-67.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": true,
    "dependsOn": ["DEV-60"],
    "acceptanceCriteria": [
      "GET /api/v1/companies/<id>/members/ — id, email, full_name, role, position, avatar, is_active, date_joined, last_login",
      "Фильтры: role, is_active, search (по имени/email), ordering (date_joined, last_login, full_name)",
      "GET /api/v1/companies/<id>/members/<user_id>/activity/ — last_login, кол-во задач (active/completed), кол-во бронирований за 30 дней",
      "Доступ: company_admin видит свою компанию; суперадмин — любую"
    ]
  },
  {
    "identifier": "DEV-66",
    "title": "Управление сотрудниками — деактивация, удаление, переназначение",
    "description": "Реализовать деактивацию и удаление сотрудников с переназначением задач.\n\nПродолжение DEV-65 (список сотрудников). Переназначение задач подразумевает, что CRM Tasks (DEV-84) уже есть, но для MVP можно делать без — задачи уходят в unassigned.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": false,
    "dependsOn": ["DEV-65"],
    "acceptanceCriteria": [
      "POST /api/v1/companies/<id>/members/<user_id>/deactivate/ — is_active=False, JWT инвалидируются; данные (задачи, бронирования) сохраняются",
      "POST /api/v1/companies/<id>/members/<user_id>/activate/ — реактивация",
      "DELETE /api/v1/companies/<id>/members/<user_id>/?reassign_to=<other_user_id> — удаление с переназначением задач",
      "Если reassign_to не указан — задачи переходят в unassigned",
      "Удалить company_admin может только суперадмин; удалить себя нельзя — 400"
    ]
  },
  {
    "identifier": "DEV-67",
    "title": "Company Settings — настройки компании",
    "description": "Реализовать эндпоинты настроек компании.\n\nCompanySettings модель (O2O к Company) существует, эндпоинт — заглушка. Создаётся автоматически в DEV-60 при создании компании.\n\nМожно брать параллельно с DEV-61, DEV-62, DEV-64, DEV-65.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend"],
    "area": "companies",
    "parallel": true,
    "dependsOn": ["DEV-60"],
    "acceptanceCriteria": [
      "GET /api/v1/companies/<id>/settings/ — custom_task_categories, custom_labels, vacation_days_per_year, onboarding_enabled, working_hours, brand_primary_color",
      "PATCH — обновление; company_admin свою, суперадмин любую; employee может только GET",
      "custom_task_categories — JSON массив строк, max 50",
      "custom_labels — JSON массив {name, color}; color — валидный hex; max 30",
      "vacation_days_per_year — int >= 0; working_hours_start < end",
      "Фронт-интеграция: на /company/settings табы с секциями, PATCH отправляет только изменённые поля"
    ]
  },
  {
    "identifier": "DEV-68",
    "title": "Company Onboarding Flow — первичная настройка",
    "description": "Реализовать пошаговый онбординг для нового company_admin при первом входе.\n\nТребует Company CRUD (DEV-60) и Company Settings (DEV-67) — онбординг проверяет, заполнены ли описание, лого, создана ли доска, приглашён ли сотрудник.",
    "block": "Блок 2 — Управление компаниями",
    "labels": ["backend", "frontend-integration"],
    "area": "companies",
    "parallel": false,
    "dependsOn": ["DEV-60", "DEV-67"],
    "acceptanceCriteria": [
      "При создании компании автоматически создаётся onboarding_completed=False",
      "GET /api/v1/companies/<id>/onboarding-status/ — {completed, steps: [{key, title, completed}]}",
      "Шаги: upload_logo, fill_description, create_first_board, invite_first_employee — автоматически помечаются completed",
      "POST .../onboarding-status/skip/ — пропустить (onboarding_completed=True)",
      "GET /api/v1/auth/me/ содержит company.onboarding_completed — фронт решает wizard или дашборд"
    ]
  },
  {
    "identifier": "DEV-69",
    "title": "Resource CRUD — создание и обновление ресурсов",
    "description": "Довести до рабочего состояния CRUD ресурсов бронирования. ResourceViewSet и модель Resource существуют.\n\nТипы: desk, meeting_room, parking, capsule.\n\nИспользует пермишены из DEV-50. Это базовый тикет Блока 3 — от него зависят: DEV-70 (bulk), DEV-71 (каталог), DEV-73 (бронирование), DEV-78 (блокировка), DEV-107 (карта).",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": false,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/bookings/resources/ (superadmin) — type, name, floor, zone, description, photo, capacity, equipment (JSON), is_active",
      "Для desk: has_monitor, has_dock, has_power_outlet, is_hot_desk, assigned_company",
      "Для meeting_room: capacity (обязательно), equipment JSON, min/max_duration_minutes",
      "Для parking: parking_type (regular/vip), assigned_company; для capsule: capsule_zone (quiet/regular)",
      "PATCH — обновление; при деактивации (is_active=False) все будущие бронирования отменяются с уведомлением",
      "DELETE — только если нет будущих бронирований, иначе 400",
      "GET /api/v1/bookings/resources/ доступен всем авторизованным"
    ]
  },
  {
    "identifier": "DEV-70",
    "title": "Resource — массовое создание и настройка расписания",
    "description": "Реализовать bulk-create ресурсов и настройку расписания доступности.\n\nПродолжение DEV-69 (Resource CRUD). Можно брать параллельно с DEV-71.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-69"],
    "acceptanceCriteria": [
      "POST /api/v1/bookings/resources/bulk-create/ с {template: {type, floor, ...}, count: 10, name_prefix: 'Стол'} → 'Стол 1'...'Стол 10'",
      "Настройка расписания: availability_start, availability_end (time), availability_days (JSON [1,2,3,4,5] = Пн-Пт)",
      "Валидация: availability_start < availability_end; availability_days — массив чисел 0-6",
      "Только суперадмин"
    ]
  },
  {
    "identifier": "DEV-71",
    "title": "Каталог ресурсов — список с фильтрами и поиском",
    "description": "Реализовать API каталога ресурсов с фильтрами для подбора. ResourceListSerializer уже есть.\n\nТребует готовые ресурсы из DEV-69. Можно брать параллельно с DEV-70.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend", "frontend-integration"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-69"],
    "acceptanceCriteria": [
      "GET /api/v1/bookings/resources/ — пагинация, фильтры: type, floor, capacity_min, capacity_max, equipment (projector,tv)",
      "Каждый ресурс: id, type, name, floor, zone, capacity, equipment, photo_url, is_active, status (free/occupied/soon_available)",
      "status рассчитывается на текущий момент; для soon_available — available_at (datetime)",
      "Поиск: search=байтерек — по name (icontains); сортировка: ordering=name, floor, capacity",
      "Премиум-компании видят assigned ресурсы; другие их не видят",
      "Фронт-интеграция: /bookings/catalog с карточками, фильтр-сайдбар"
    ]
  },
  {
    "identifier": "DEV-72",
    "title": "Каталог ресурсов — проверка доступности и расписание",
    "description": "Реализовать фильтр свободных ресурсов по временному интервалу и детальное расписание ресурса.\n\nРасширяет каталог из DEV-71. Нужен для того, чтобы DEV-73 (создание бронирования) мог проверять конфликты.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend", "frontend-integration"],
    "area": "bookings",
    "parallel": false,
    "dependsOn": ["DEV-71"],
    "acceptanceCriteria": [
      "Фильтр available_from + available_to (datetime) — возвращает только ресурсы без пересечений с бронированиями и блокировками",
      "GET /api/v1/bookings/resources/<id>/ — детали + расписание на 7 дней: [{start, end, booking_id?, user_name?}]",
      "GET /api/v1/bookings/resources/<id>/schedule/?date=YYYY-MM-DD — расписание на конкретный день",
      "GET /api/v1/bookings/resources/<id>/schedule/?week=YYYY-MM-DD — расписание на неделю",
      "Фронт-интеграция: на странице ресурса таймлайн с занятыми/свободными слотами"
    ]
  },
  {
    "identifier": "DEV-73",
    "title": "Создание бронирования — базовый flow и конфликт-контроль",
    "description": "Реализовать создание бронирования с атомарной проверкой конфликтов.\n\nBookingCreateSerializer существует, конфликт-контроль — TODO.\n\nКлючевая фича MVP. Требует каталог ресурсов (DEV-69) для привязки к ресурсу. Использует расписание из DEV-72.\n\nОт этого тикета зависят: DEV-74 (валидация), DEV-75 (мои бронирования), DEV-76 (изменение), DEV-77 (админ), DEV-78 (блокировка), DEV-79 (рекурренты), DEV-80 (автоматизация).",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend", "frontend-integration"],
    "area": "bookings",
    "parallel": false,
    "dependsOn": ["DEV-69", "DEV-72"],
    "acceptanceCriteria": [
      "POST /api/v1/bookings/reservations/ с {resource_id, start_time, end_time, description?}",
      "Конфликт-контроль: SELECT FOR UPDATE на ресурс + проверка пересечений — если занят, 409 Conflict",
      "Валидация расписания ресурса: нельзя забронировать вне availability_days/hours — 400",
      "Лимит активных бронирований на пользователя (настраиваемый, default=5) — 400 если превышен",
      "Компания бронирует assigned + общие ресурсы",
      "Ответ: booking {id, resource, user, start_time, end_time, status=confirmed}",
      "Фронт-интеграция: клик по свободному слоту → модалка бронирования"
    ]
  },
  {
    "identifier": "DEV-74",
    "title": "Бронирование — валидация правил по типам ресурсов и участники",
    "description": "Добавить type-specific правила и участников для meeting_room.\n\nРасширяет логику создания бронирования из DEV-73. Можно брать параллельно с DEV-75, DEV-77.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-73"],
    "acceptanceCriteria": [
      "Desk: нельзя забронировать более чем на 14 дней вперёд",
      "Meeting room: минимум 30 мин, максимум 4 часа",
      "Parking: бронирование только на целый день, не более 7 дней вперёд",
      "Capsule: минимум 1ч, максимум 8ч",
      "Валидация min/max_duration ресурса — 400 'Minimum booking duration is 30 minutes'",
      "Для meeting_room: participant_ids в create — FK на User, участники получают уведомление"
    ]
  },
  {
    "identifier": "DEV-75",
    "title": "Мои бронирования — список, фильтры, отмена",
    "description": "Реализовать страницу 'Мои бронирования' с фильтрами и отменой.\n\nBookingViewSet.my_bookings и cancel существуют, cancel rules — TODO.\n\nТребует готовое создание бронирования из DEV-73. Можно брать параллельно с DEV-74, DEV-77.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend", "frontend-integration"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-73"],
    "acceptanceCriteria": [
      "GET /api/v1/bookings/reservations/my/ — фильтры: status (upcoming/past/cancelled), resource_type, date_from, date_to",
      "Сортировка: upcoming — start_time asc; past — start_time desc",
      "POST .../reservations/<id>/cancel/ — нельзя менее чем за 30 мин до начала (настраиваемо) — 400; уже начавшееся — 400",
      "Статусы бронирования: confirmed, completed, cancelled, no_show",
      "Каждая отмена записывается для аудита",
      "Фронт-интеграция: /bookings/my — табы 'Предстоящие'/'Прошедшие'/'Отменённые'"
    ]
  },
  {
    "identifier": "DEV-76",
    "title": "Бронирование — изменение времени и управление участниками",
    "description": "Реализовать изменение времени бронирования и добавление/удаление участников.\n\nРасширяет DEV-73 (создание) и DEV-75 (мои бронирования). Конфликт-контроль переиспользуется из DEV-73.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": false,
    "dependsOn": ["DEV-73", "DEV-75"],
    "acceptanceCriteria": [
      "PATCH /api/v1/bookings/reservations/<id>/ с {start_time, end_time} — проходит тот же конфликт-контроль",
      "POST .../reservations/<id>/participants/ с {user_ids} — добавить участников (meeting_room); уведомление",
      "DELETE .../reservations/<id>/participants/<user_id>/ — убрать участника",
      "Изменения записываются для аудита"
    ]
  },
  {
    "identifier": "DEV-77",
    "title": "Бронирования — админ-панель (суперадмин + company_admin)",
    "description": "Эндпоинты для просмотра и управления бронированиями администраторами.\n\nТребует базовое бронирование из DEV-73. Можно брать параллельно с DEV-74, DEV-75.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-73"],
    "acceptanceCriteria": [
      "GET /api/v1/bookings/reservations/ (superadmin) — все; фильтры: company_id, user_id, resource_id, resource_type, status, date_from, date_to",
      "GET (company_admin) — только бронирования сотрудников своей компании",
      "POST .../reservations/<id>/admin-cancel/ с {reason} — суперадмин/company_admin отменяет с причиной; пользователь получает уведомление"
    ]
  },
  {
    "identifier": "DEV-78",
    "title": "Resource Blocking — блокировка ресурсов суперадмином",
    "description": "Реализовать блокировку ресурсов на период (ремонт, мероприятие). ResourceBlock модель существует.\n\nТребует ресурсы из DEV-69 и бронирование из DEV-73 (для отмены пересекающихся броней).",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-69", "DEV-73"],
    "acceptanceCriteria": [
      "POST /api/v1/bookings/resources/<id>/block/ с {start_time, end_time, reason} — суперадмин блокирует",
      "Пересекающиеся бронирования автоматически отменяются; пользователи получают уведомление с причиной",
      "Конфликт-контроль учитывает ResourceBlock",
      "GET .../resources/<id>/blocks/ — список блокировок; DELETE .../blocks/<block_id>/ — снятие досрочно",
      "В каталоге заблокированный ресурс — status=blocked с reason"
    ]
  },
  {
    "identifier": "DEV-79",
    "title": "Recurring Bookings — рекуррентные бронирования",
    "description": "Рекуррентные бронирования для компаний. RecurringBookingViewSet существует.\n\nТребует базовое бронирование из DEV-73. Переиспользует конфликт-контроль. Можно брать параллельно с DEV-78, DEV-80.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-73"],
    "acceptanceCriteria": [
      "POST /api/v1/bookings/recurring/ с {resource_id, day_of_week, start_time, end_time, repeat_until} — только company_admin/employee; guest — 403",
      "Генерирует Booking-записи на все даты до repeat_until; конфликтующие пропускаются → skipped_dates в ответе",
      "GET — список рекуррентных бронирований пользователя",
      "DELETE — отмена: удаляет все будущие бронирования серии",
      "Celery task (раз в неделю) дорастает серию на следующий период",
      "Каждое бронирование серии содержит recurring_booking_id"
    ]
  },
  {
    "identifier": "DEV-80",
    "title": "Booking Automations — напоминания и auto-complete",
    "description": "Celery beat tasks: напоминания за 15 минут и автоматическое завершение по окончании времени.\n\nТребует бронирования из DEV-73. Можно брать параллельно с DEV-78, DEV-79.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": true,
    "dependsOn": ["DEV-73"],
    "acceptanceCriteria": [
      "Celery beat каждые 5 мин: находит бронирования, начинающиеся через 15 мин → in-app уведомление (+ email если включен)",
      "Celery beat каждые 5 мин: бронирования с end_time в прошлом → status=completed",
      "Все автоматические действия логируются",
      "Настраиваемые параметры через env: REMINDER_MINUTES_BEFORE=15"
    ]
  },
  {
    "identifier": "DEV-81",
    "title": "Booking Automations — no-show detection и check-in",
    "description": "Celery task для определения no-show в meeting_room и эндпоинт check-in.\n\nРасширяет автоматизации из DEV-80. Использует ту же инфраструктуру Celery beat.",
    "block": "Блок 3 — Бронирование ресурсов",
    "labels": ["backend"],
    "area": "bookings",
    "parallel": false,
    "dependsOn": ["DEV-80"],
    "acceptanceCriteria": [
      "Celery beat каждые 5 мин: meeting_room бронирования, начавшиеся >15 мин назад без check-in → status=no_show, ресурс освобождается",
      "POST /api/v1/bookings/reservations/<id>/check-in/ — пользователь подтверждает присутствие",
      "Настраиваемый параметр: NO_SHOW_MINUTES=15",
      "No-show логируется; ресурс становится доступным для бронирования"
    ]
  },
  {
    "identifier": "DEV-82",
    "title": "CRM Boards — CRUD досок",
    "description": "Довести до рабочего состояния CRUD досок. BoardViewSet существует.\n\nИспользует пермишены из DEV-50 и привязку к компании из DEV-60.\n\nЭто базовый тикет Блока 4 CRM — от него зависят: DEV-83 (колонки), DEV-84 (задачи), DEV-91 (лейблы).",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": false,
    "dependsOn": ["DEV-50", "DEV-60"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/boards/ с {name, description?} — создаёт доску для компании текущего пользователя",
      "При создании автоматически 3 колонки: 'К выполнению', 'В работе', 'Готово'",
      "Проверяется лимит досок по тарифу — 400 если превышен",
      "GET /api/v1/crm/boards/ — доски компании; суперадмин видит все + фильтр company_id",
      "POST .../boards/<id>/archive/ — is_archived=True; GET list по умолчанию не показывает, ?include_archived=true показывает",
      "Фронт-интеграция: /crm/boards — список досок; /crm/boards/<id> — канбан"
    ]
  },
  {
    "identifier": "DEV-83",
    "title": "CRM Columns — CRUD колонок, reorder, WIP-лимит",
    "description": "Реализовать управление колонками на доске: добавление, переименование, удаление, сортировка, WIP.\n\nColumnViewSet существует. Продолжение DEV-82 (доски).",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": false,
    "dependsOn": ["DEV-82"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/boards/<board_pk>/columns/ с {name, wip_limit?}",
      "PATCH .../columns/<id>/ — переименовать, изменить wip_limit, изменить order",
      "POST .../columns/reorder/ с {column_ids: [id3, id1, id2]} — массовое изменение порядка",
      "DELETE .../columns/<id>/?move_to=<other_column_id> — удаление, задачи переносятся",
      "Фронт-интеграция: drag-and-drop колонок на доске"
    ]
  },
  {
    "identifier": "DEV-84",
    "title": "CRM Tasks — CRUD (создание, получение, обновление)",
    "description": "Реализовать CRUD задач на канбан-доске. TaskViewSet и TaskSerializer существуют.\n\nТребует доски (DEV-82) и колонки (DEV-83). От этого тикета зависят: DEV-85 (move), DEV-86 (чеклисты), DEV-87 (комментарии), DEV-88 (вложения), DEV-89 (история), DEV-90 (фильтры).",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": false,
    "dependsOn": ["DEV-82", "DEV-83"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/tasks/ с {board_id, column_id, title, description?, priority, deadline?, assignee_id?, label_ids?}",
      "Исполнитель — только сотрудник той же компании; 400 если из другой",
      "GET /api/v1/crm/tasks/?board_id=<id> — задачи доски; фильтры: column_id, assignee_id, priority, label_ids, deadline_from/to, search",
      "GET /api/v1/crm/tasks/<id>/ — полная карточка: все поля + checklists + comments_count + attachments_count + history (10)",
      "PATCH — обновление: title, description, priority, deadline, assignee_id, label_ids",
      "При назначении assignee → уведомление 'Вам назначена задача'",
      "Фронт-интеграция: карточки задач на доске"
    ]
  },
  {
    "identifier": "DEV-85",
    "title": "CRM Tasks — перемещение, WIP-лимит, архивация",
    "description": "Реализовать move между колонками с проверкой WIP и архивацию задач.\n\nТребует задачи (DEV-84) и колонки (DEV-83). Можно брать параллельно с DEV-86, DEV-87, DEV-88, DEV-89.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-84", "DEV-83"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/tasks/<id>/move/ с {column_id, order?} — перемещение в другую колонку",
      "Проверка WIP-лимита — если колонка полна, 400 'WIP limit reached (max N tasks)'",
      "При перемещении создаётся TaskHistory (who, from_column, to_column, timestamp)",
      "POST /api/v1/crm/tasks/<id>/archive/ — soft delete (is_deleted=True), пропадает с доски",
      "Фронт-интеграция: drag-and-drop между колонками → POST /move/"
    ]
  },
  {
    "identifier": "DEV-86",
    "title": "CRM — чеклисты внутри задач",
    "description": "Реализовать чеклисты (подзадачи с чекбоксами). Модели Checklist и ChecklistItem существуют.\n\nТребует задачи из DEV-84. Можно брать параллельно с DEV-85, DEV-87, DEV-88, DEV-89.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-84"],
    "acceptanceCriteria": [
      "POST .../tasks/<task_id>/checklists/ с {title} — создать чеклист",
      "POST .../checklists/<id>/items/ с {text} — добавить пункт",
      "PATCH .../items/<id>/ с {is_completed, text?, order?} — отметка галочки / обновление",
      "DELETE — удаление пункта или всего чеклиста",
      "GET задачи включает checklists с items + checklist_progress: {total, completed}",
      "Фронт-интеграция: в карточке задачи раскрывающийся чеклист"
    ]
  },
  {
    "identifier": "DEV-87",
    "title": "CRM — комментарии к задачам",
    "description": "Реализовать комментарии внутри задачи. CommentViewSet существует, notify — TODO.\n\nТребует задачи из DEV-84. Можно брать параллельно с DEV-85, DEV-86, DEV-88, DEV-89.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-84"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/tasks/<task_pk>/comments/ с {text}",
      "GET — список, ordered by created_at asc; каждый: id, text, author {id, full_name, avatar}, created_at",
      "PATCH — автор редактирует свой; DELETE — автор или company_admin",
      "При создании: уведомление assignee и создателю задачи (если не автор комментария)",
      "GET задачи содержит comments_count",
      "Фронт-интеграция: лента комментариев в карточке задачи"
    ]
  },
  {
    "identifier": "DEV-88",
    "title": "CRM — вложения файлов к задачам",
    "description": "Прикрепление файлов к CRM-задачам. TaskAttachment модель существует.\n\nТребует задачи из DEV-84. Можно брать параллельно с DEV-85, DEV-86, DEV-87, DEV-89.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-84"],
    "acceptanceCriteria": [
      "POST .../tasks/<id>/attachments/ с multipart file — загрузить; или {storage_file_id} — привязать из Storage",
      "Max 50 МБ; допустимые: pdf, doc/docx, xls/xlsx, png, jpg, gif — иначе 400",
      "GET — список: {id, filename, size, mime_type, url, uploaded_by, created_at}",
      "DELETE — удалить (файл с диска только если прямая загрузка, не из Storage)",
      "GET задачи содержит attachments_count; вложения учитываются в storage quota"
    ]
  },
  {
    "identifier": "DEV-89",
    "title": "CRM — история изменений задач",
    "description": "Автоматический лог всех изменений задачи. TaskHistory модель существует.\n\nТребует задачи из DEV-84. Можно брать параллельно с DEV-85, DEV-86, DEV-87, DEV-88.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-84"],
    "acceptanceCriteria": [
      "При PATCH записывается TaskHistory: user, action, field_name, old_value, new_value, created_at",
      "Отслеживаемые поля: title, description, priority, deadline, assignee, column, status",
      "label_added / label_removed; archived",
      "GET .../tasks/<id>/history/ — полная история с пагинацией, -created_at",
      "История read-only — нельзя редактировать/удалять"
    ]
  },
  {
    "identifier": "DEV-90",
    "title": "CRM — фильтрация, поиск, мои задачи",
    "description": "Фильтры, поиск и альтернативные виды для CRM-досок. my_tasks action уже есть.\n\nТребует задачи (DEV-84) и лейблы (DEV-91). Агрегирует данные со всех досок.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "crm",
    "parallel": false,
    "dependsOn": ["DEV-84", "DEV-91"],
    "acceptanceCriteria": [
      "Фильтры комбинируются: assignee_id, priority, label_ids (comma-separated), deadline=overdue/today/this_week; search по title",
      "GET /api/v1/crm/tasks/my/ — задачи со ВСЕХ досок, назначенные на текущего пользователя; группировка по board_id",
      "ordering=priority,deadline,-created_at — множественная сортировка; пагинация",
      "GET /api/v1/crm/boards/<id>/?view=list — плоский список вместо группировки по колонкам",
      "Фронт-интеграция: фильтр-бар на доске, переключатель Канбан/Список, /crm/my-tasks"
    ]
  },
  {
    "identifier": "DEV-91",
    "title": "CRM Labels — кастомные лейблы для задач",
    "description": "CRUD кастомных лейблов (тегов). LabelViewSet существует.\n\nТребует доски из DEV-82 (лейблы привязаны к компании). Можно брать параллельно с DEV-83.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "crm",
    "parallel": true,
    "dependsOn": ["DEV-82"],
    "acceptanceCriteria": [
      "POST /api/v1/crm/labels/ с {name, color (hex)} — уникальны в рамках компании",
      "GET — список лейблов компании",
      "PATCH — обновление name/color; только company_admin",
      "DELETE — удаление; M2M автоматически снимается с задач"
    ]
  },
  {
    "identifier": "DEV-92",
    "title": "Справочник команды — Team Directory API",
    "description": "API справочника сотрудников компании для страницы 'Команда'.\n\nТребует Company из DEV-60 и пермишены из DEV-50.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "portal",
    "parallel": true,
    "dependsOn": ["DEV-50", "DEV-60"],
    "acceptanceCriteria": [
      "GET /api/v1/companies/<id>/directory/ — avatar, full_name, position, email, phone, role, is_active, last_login",
      "Поиск: search по first_name, last_name, email; фильтр: position, role; сортировка: full_name, date_joined",
      "GET .../directory/<user_id>/ — контакты + кол-во задач + бронирований за 30 дней + last_login",
      "Доступен сотрудникам компании и суперадмину; guest — 403",
      "Фронт-интеграция: /company/team — карточки сотрудников, поиск, клик → профиль"
    ]
  },
  {
    "identifier": "DEV-93",
    "title": "Календарь команды — Team Calendar API",
    "description": "API агрегированного календаря: бронирования, дедлайны CRM, отпуска, гостевые визиты.\n\nАгрегирует данные из нескольких модулей: бронирования (DEV-73), заявки на отсутствие (DEV-98), CRM-задачи (DEV-84). Поэтому берётся после них.\n\nМожно начинать, когда хотя бы бронирования готовы.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "portal",
    "parallel": false,
    "dependsOn": ["DEV-73", "DEV-84", "DEV-98"],
    "acceptanceCriteria": [
      "GET /api/v1/companies/<id>/calendar/?date_from=...&date_to=... — все события компании",
      "Типы: booking (resource.name), task_deadline (task.title), leave (user — leave_type), guest_visit (guest_name)",
      "Каждое событие: {type, title, start, end, user {id, full_name}}",
      "Фильтр: user_id, event_type, my (только мои)",
      "GET .../calendar/busy/?user_id=X&date=... — слоты занятости на день",
      "Фронт-интеграция: /company/calendar — день/неделя/месяц, фильтры по сотруднику и типу"
    ]
  },
  {
    "identifier": "DEV-94",
    "title": "File Storage — папки CRUD",
    "description": "Реализовать работу с папками: личное и общее хранилище. FolderViewSet существует.\n\nИспользует пермишены из DEV-50. От этого тикета зависит DEV-95 (файлы).\n\nМожно брать параллельно с другими блоками — независим от CRM/Bookings.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "storage",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/storage/folders/ с {name, parent_id?, is_company_shared}",
      "GET ?parent_id=null&scope=personal — корневые личные; scope=company — общее хранилище компании",
      "GET /api/v1/storage/folders/<id>/ — вложенные папки + файлы",
      "PATCH — переименование; DELETE — удаление (рекурсивно)",
      "Фронт-интеграция: файловый менеджер с навигацией по папкам"
    ]
  },
  {
    "identifier": "DEV-95",
    "title": "File Storage — файлы: загрузка, скачивание, управление",
    "description": "CRUD файлов: загрузка, скачивание, переименование, перемещение, мягкое удаление. FileViewSet существует, quota — TODO.\n\nТребует папки из DEV-94. После этого тикета можно делать DEV-96 (шаринг) и DEV-97 (квоты).",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "storage",
    "parallel": false,
    "dependsOn": ["DEV-94"],
    "acceptanceCriteria": [
      "POST /api/v1/storage/files/ с multipart file + {folder_id} — загрузка; проверка квоты",
      "Max 100 МБ — иначе 400",
      "GET .../files/<id>/ — метаданные: name, size, mime_type, download_url, uploaded_by, created_at",
      "GET .../files/<id>/download/ — Content-Disposition: attachment",
      "PATCH — переименование; POST .../files/<id>/move/ с {folder_id} — перемещение",
      "DELETE — мягкое удаление (is_deleted=True, deleted_at); Celery task автоочистка через 30 дней",
      "Поиск: search по name; сортировка: name, -created_at, size",
      "Фронт-интеграция: /storage — загрузка, скачивание, переименование, удаление"
    ]
  },
  {
    "identifier": "DEV-96",
    "title": "File Sharing — шаринг файлов между сотрудниками",
    "description": "Шаринг файлов с уровнями доступа. FileShareViewSet существует.\n\nТребует файлы из DEV-95. Можно брать параллельно с DEV-97.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "storage",
    "parallel": true,
    "dependsOn": ["DEV-95"],
    "acceptanceCriteria": [
      "POST /api/v1/storage/shares/ с {file_id, shared_with_user_id, permission (view/download/full)}",
      "Только сотрудник своей компании — 400 если другая",
      "GET ?shared_with_me=true — расшаренные мне; GET .../files/<id>/shares/ — кому расшарен",
      "PATCH — изменить permission; DELETE — отозвать",
      "view = метаданные; download = + скачивание; full = + переименование/удаление",
      "При шаринге — уведомление получателю"
    ]
  },
  {
    "identifier": "DEV-97",
    "title": "Storage Quotas — учёт и отображение квот",
    "description": "Учёт использования хранилища и проверка лимитов.\n\nТребует файлы из DEV-95 и тарифные лимиты из DEV-62. Можно брать параллельно с DEV-96.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "storage",
    "parallel": true,
    "dependsOn": ["DEV-95", "DEV-62"],
    "acceptanceCriteria": [
      "GET /api/v1/storage/usage/ — {personal: {used_bytes, file_count}, company: {used_bytes, limit_bytes, file_count}}",
      "При загрузке: если used + new_size > limit — 400 'Storage limit exceeded'",
      "Уведомление company_admin при 80% и 95%",
      "Celery task ежедневно: удаляет is_deleted + deleted_at > 30 дней с диска"
    ]
  },
  {
    "identifier": "DEV-98",
    "title": "Leave Requests — подача и просмотр заявок",
    "description": "Реализовать подачу и просмотр заявок на отсутствие. LeaveRequestViewSet существует.\n\nИспользует пермишены из DEV-50. От этого тикета зависит DEV-99 (ревью) и DEV-93 (календарь).\n\nМожно брать параллельно с другими блоками — независим от CRM/Bookings.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "hr",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/hr/leaves/ с {leave_type (vacation/day_off/sick_leave/remote), start_date, end_date, comment?}",
      "Валидация: start <= end; start >= today; нельзя на даты с одобренной заявкой — 400",
      "GET — employee видит свои; company_admin — все сотрудники; фильтры: status, leave_type",
      "GET /<id>/ — user, leave_type, dates, status, reviewer, review_comment, created_at",
      "Фронт-интеграция: /hr/leaves — список заявок + кнопка 'Подать заявку'"
    ]
  },
  {
    "identifier": "DEV-99",
    "title": "Leave Requests — review (одобрение/отклонение)",
    "description": "Реализовать одобрение/отклонение заявок company_admin-ом с проверкой баланса.\n\nТребует подачу заявок (DEV-98) и баланс дней (DEV-100). При одобрении vacation списываются дни из LeaveBalance.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "hr",
    "parallel": false,
    "dependsOn": ["DEV-98", "DEV-100"],
    "acceptanceCriteria": [
      "POST /api/v1/hr/leaves/<id>/review/ с {status (approved/rejected), comment?} — только company_admin",
      "При одобрении vacation: проверяется LeaveBalance — если остаток < duration_days, 400",
      "При одобрении: LeaveBalance.used_days += duration_days",
      "Уведомление сотруднику: 'одобрена' или 'отклонена' + комментарий",
      "Одобренные заявки видны в Team Calendar как leave-события",
      "Фронт-интеграция: кнопки approve/reject на карточке заявки"
    ]
  },
  {
    "identifier": "DEV-100",
    "title": "Leave Balance — управление балансом отпускных дней",
    "description": "Система баланса отпускных дней. LeaveBalance модель существует.\n\nИспользует vacation_days_per_year из CompanySettings (DEV-67). Можно брать параллельно с DEV-98.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend"],
    "area": "hr",
    "parallel": true,
    "dependsOn": ["DEV-67"],
    "acceptanceCriteria": [
      "GET /api/v1/hr/leaves/balance/ — {year, total_days, used_days, remaining_days}",
      "POST .../balance/set/ с {user_id, year, total_days} — company_admin устанавливает",
      "Дефолт из CompanySettings.vacation_days_per_year",
      "GET .../balance/team/ (company_admin) — балансы всех сотрудников",
      "При отмене одобренной заявки: used_days -= duration_days",
      "sick_leave и remote не списывают дни"
    ]
  },
  {
    "identifier": "DEV-101",
    "title": "Employee Onboarding Checklist",
    "description": "Онбординг-чеклист для нового сотрудника.\n\nМодели OnboardingTemplate, OnboardingStep, UserOnboardingProgress существуют.\n\nТребует Invite Registration (DEV-54) — при регистрации по инвайту автоматически создаётся прогресс.",
    "block": "Блок 4 — Корпоративный портал",
    "labels": ["backend", "frontend-integration"],
    "area": "hr",
    "parallel": true,
    "dependsOn": ["DEV-54"],
    "acceptanceCriteria": [
      "POST /api/v1/hr/onboarding/templates/ с {name, steps: [{title, description, order}]}; GET, PATCH — company_admin",
      "При регистрации по инвайту: автоматически создаётся UserOnboardingProgress",
      "GET /api/v1/hr/onboarding/progress/ — {completed, steps: [{id, title, is_completed}]}",
      "POST .../progress/steps/<step_id>/complete/ — отметить шаг; все шаги → completed=True",
      "GET .../progress/team/ (company_admin) — прогресс всех: [{user, completed_steps, total_steps}]",
      "Фронт-интеграция: step-by-step wizard при первом входе (если completed=False)"
    ]
  },
  {
    "identifier": "DEV-102",
    "title": "Guest Pass — создание пропуска с QR-кодом",
    "description": "Создание цифрового пропуска с QR. GuestPass модель и GuestPassViewSet существуют, email — TODO.\n\nИспользует пермишены из DEV-50. От этого тикета зависят: DEV-103 (отзыв), DEV-104 (валидация QR).",
    "block": "Блок 5 — Система доступа",
    "labels": ["backend", "frontend-integration"],
    "area": "access",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/access/passes/ с {guest_name, guest_email, guest_phone?, purpose, valid_from, valid_until, is_single_use}",
      "Генерируется QR-код (UUID → QR image), сохраняется как файл",
      "Гость коворкинга: max 2 активных; сотрудники/admin — без лимита; valid_until max +30 дней",
      "Celery task отправляет email гостю с QR и деталями",
      "GET — мои пропуска; фильтры: status (active/used/expired/revoked)",
      "GET /<id>/ — детали + QR URL",
      "Фронт-интеграция: /access/passes — список + 'Создать пропуск'; QR на детальной"
    ]
  },
  {
    "identifier": "DEV-103",
    "title": "Guest Pass — отзыв и повторная отправка",
    "description": "Отзыв пропусков, resend QR, admin-просмотр. revoke и resend — TODO.\n\nПродолжение DEV-102 (создание пропусков). Можно брать параллельно с DEV-104.",
    "block": "Блок 5 — Система доступа",
    "labels": ["backend"],
    "area": "access",
    "parallel": true,
    "dependsOn": ["DEV-102"],
    "acceptanceCriteria": [
      "POST .../passes/<id>/revoke/ — status=revoked; нельзя отозвать used/expired — 400",
      "POST .../passes/<id>/resend/ — повторная отправка QR; rate limit 3/час",
      "GET (company_admin) — пропуска сотрудников компании; (superadmin) — все; фильтры: company_id, created_by, status, dates",
      "Суперадмин может revoke любой; company_admin — пропуска своей компании"
    ]
  },
  {
    "identifier": "DEV-104",
    "title": "QR Validation — проверка пропуска на ресепшн",
    "description": "Эндпоинт валидации QR-кода. validate_qr view существует, notify — TODO.\n\nТребует пропуска из DEV-102. От этого тикета зависит DEV-105 (журнал доступа). Можно брать параллельно с DEV-103.",
    "block": "Блок 5 — Система доступа",
    "labels": ["backend", "frontend-integration"],
    "area": "access",
    "parallel": true,
    "dependsOn": ["DEV-102"],
    "acceptanceCriteria": [
      "POST /api/v1/access/validate/ с {qr_code} — проверка",
      "Валидный → {valid: true, guest_name, purpose, invited_by, valid_from/until}",
      "Невалидный → {valid: false, reason: expired/revoked/already_used/not_found}",
      "При валидации single_use → status=used; создаётся AccessLog; уведомление создателю",
      "Доступен суперадмину и роли reception; остальные — 403",
      "Фронт-интеграция: /access/validate — поле ввода + камера, отображение результата"
    ]
  },
  {
    "identifier": "DEV-105",
    "title": "Access Log — журнал доступа и CSV-экспорт",
    "description": "Просмотр и экспорт лога доступа. AccessLogViewSet существует.\n\nТребует валидацию QR (DEV-104) — AccessLog-записи создаются при валидации.",
    "block": "Блок 5 — Система доступа",
    "labels": ["backend"],
    "area": "access",
    "parallel": false,
    "dependsOn": ["DEV-104"],
    "acceptanceCriteria": [
      "GET /api/v1/access/logs/ (superadmin) — все; поля: guest_pass, invited_by, validated_at, validated_by",
      "Фильтры: company_id, date_from/to, search; сортировка: -validated_at; пагинация",
      "GET .../logs/export/?date_from=...&date_to=...&format=csv — CSV-выгрузка",
      "Company_admin видит только логи своей компании"
    ]
  },
  {
    "identifier": "DEV-106",
    "title": "Floor Plans — CRUD этажей и загрузка планов",
    "description": "Управление планами этажей. FloorViewSet существует.\n\nИспользует пермишены из DEV-50 (только суперадмин). От этого тикета зависит DEV-107 (точки на карте).",
    "block": "Блок 6 — Сервисы здания",
    "labels": ["backend"],
    "area": "building",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/services/floors/ (superadmin) с {number, name, plan_image (file)}",
      "GET — список этажей с plan_image_url",
      "GET /<id>/ — детали + все map_points этажа",
      "PATCH — обновление; DELETE — удаление (каскадно удаляет map_points)"
    ]
  },
  {
    "identifier": "DEV-107",
    "title": "Map Points — точки на карте и realtime-статусы",
    "description": "CRUD точек на плане этажа с привязкой к ресурсам и realtime-статусами.\n\nТребует этажи из DEV-106 и ресурсы из DEV-69 (точки привязываются к ресурсам для отображения статуса free/occupied).",
    "block": "Блок 6 — Сервисы здания",
    "labels": ["backend", "frontend-integration"],
    "area": "building",
    "parallel": false,
    "dependsOn": ["DEV-106", "DEV-69"],
    "acceptanceCriteria": [
      "POST /api/v1/services/map-points/ (superadmin) с {floor_id, point_type, x, y, resource_id?, company_id?, label}",
      "point_type=desk/meeting_room → resource_id обязателен; office → company_id обязателен",
      "PATCH — позиция / привязка; DELETE — удаление",
      "GET .../floors/<id>/map/?datetime=... — точки с статусами ресурсов (free/occupied/blocked)",
      "Поиск: GET .../map-points/search/?q=... — по label и resource.name; возвращает {floor_id, x, y}",
      "Фронт-интеграция: /map — этажи табами, SVG/img план с точками; клик на ресурс → бронирование"
    ]
  },
  {
    "identifier": "DEV-108",
    "title": "Service Requests — заявки и вызов уборки",
    "description": "Сервисные заявки и быстрый вызов уборки. ServiceRequestViewSet с actions существует, notify — TODO.\n\nИспользует пермишены из DEV-50. Независим от остальных тикетов блока — можно начинать сразу после DEV-50.",
    "block": "Блок 6 — Сервисы здания",
    "labels": ["backend", "frontend-integration"],
    "area": "building",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "POST /api/v1/services/requests/ с {request_type, floor, location, description, urgency, photo?}",
      "POST .../requests/quick-cleaning/ с {floor?} — тип=cleaning, floor из последнего бронирования если не указан",
      "GET — мои заявки / все (superadmin); фильтры: request_type, status, urgency, floor",
      "Статусы: new → accepted → in_progress → completed; PATCH .../status/ → уведомление создателю",
      "POST .../rate/ с {rating (1-5)} — после completed; один раз",
      "Фронт-интеграция: 'Вызвать уборку' на дашборде; /services/requests — список + форма"
    ]
  },
  {
    "identifier": "DEV-109",
    "title": "Announcements — CRUD и лента",
    "description": "Лента объявлений БЦ (суперадмин, видят все) и компании (company_admin, видят сотрудники). AnnouncementViewSet существует.\n\nИспользует пермишены из DEV-50 и привязку к компании из DEV-60. От этого тикета зависит DEV-110 (прочтение).",
    "block": "Блок 6 — Сервисы здания",
    "labels": ["backend", "frontend-integration"],
    "area": "announcements",
    "parallel": true,
    "dependsOn": ["DEV-50", "DEV-60"],
    "acceptanceCriteria": [
      "POST (superadmin) с {title, text, category, image?, is_pinned, company_id (null=БЦ)}",
      "POST (company_admin) — автоматически company_id = своя компания",
      "GET — БЦ (company=null) + своя компания; ordered: is_pinned desc, created_at desc; guest видит только БЦ",
      "Cursor-based пагинация для бесконечного скролла",
      "DELETE — автор или суперадмин",
      "Фронт-интеграция: виджет на дашборде (последние 5); /announcements — полная лента"
    ]
  },
  {
    "identifier": "DEV-110",
    "title": "Announcements — прочтение и email-рассылка",
    "description": "Отметка прочитанного и email-рассылка для важных объявлений.\n\nПродолжение DEV-109 (CRUD объявлений). Email-рассылка через Celery.",
    "block": "Блок 6 — Сервисы здания",
    "labels": ["backend"],
    "area": "announcements",
    "parallel": false,
    "dependsOn": ["DEV-109"],
    "acceptanceCriteria": [
      "POST .../announcements/<id>/read/ — создаёт AnnouncementRead; is_read=true в ответе",
      "Каждое announcement в ответе: is_read (для текущего пользователя), read_count (для автора)",
      "Если is_pinned=true и notify_email=true — Celery task email всем (БЦ) или сотрудникам компании"
    ]
  },
  {
    "identifier": "DEV-111",
    "title": "Notification System — helper-функция и ViewSet",
    "description": "Создать ядро уведомлений: helper create_notification и API для просмотра/управления.\n\nNotificationViewSet существует. Использует пермишены из DEV-50.\n\nОт этого тикета зависят: DEV-112 (подключение ко всем модулям), DEV-113 (email), DEV-114 (preferences), DEV-119 (дашборд).",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "notifications",
    "parallel": true,
    "dependsOn": ["DEV-50"],
    "acceptanceCriteria": [
      "Создана create_notification(user, notification_type, title, message, link?) — вызывается из любого места кода",
      "notification_type enum: booking_confirmed, booking_reminder, booking_cancelled, task_assigned, task_moved, task_comment, task_deadline, guest_validated, guest_pass_expiring, service_request_update, announcement, invitation, leave_review, system",
      "GET /api/v1/notifications/ — мои; фильтры: is_read, notification_type; ordering: -created_at; пагинация 20",
      "GET .../unread-count/ — {count: N}; POST /<id>/read/; POST /read-all/; DELETE /<id>/",
      "Каждое: {id, type, title, message, link, is_read, created_at}",
      "Фронт-интеграция: колокольчик с бейджем → dropdown → 'Смотреть все' → /notifications"
    ]
  },
  {
    "identifier": "DEV-112",
    "title": "Notification System — подключение ко всем модулям",
    "description": "Вставить вызовы create_notification во все места, где уведомления были TODO.\n\nТребует хелпер из DEV-111. Также нужны готовые модули, в которые вставляются вызовы: бронирования (DEV-73), CRM (DEV-84), доступ (DEV-102), сервисы (DEV-108), объявления (DEV-109), HR (DEV-98), инвайты (DEV-64).\n\nЭтот тикет берётся одним из последних — после завершения основных модулей.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend"],
    "area": "notifications",
    "parallel": false,
    "dependsOn": ["DEV-111", "DEV-73", "DEV-84", "DEV-102", "DEV-108", "DEV-109", "DEV-98", "DEV-64"],
    "acceptanceCriteria": [
      "Booking: create → booking_confirmed; cancel → booking_cancelled; admin-cancel → booking_cancelled с причиной",
      "CRM: assign → task_assigned; move → task_moved; comment → task_comment; deadline tomorrow → task_deadline",
      "Access: validate QR → guest_validated; pass expiring → guest_pass_expiring",
      "Services: status change → service_request_update; new announcement → announcement",
      "HR: leave review → leave_review; invitation create → invitation",
      "Все вызовы покрыты: ни одного TODO notify не осталось"
    ]
  },
  {
    "identifier": "DEV-113",
    "title": "Email Notifications — шаблоны и Celery tasks",
    "description": "Email-уведомления для критичных событий. HTML-шаблоны + Celery tasks.\n\nТребует хелпер из DEV-111. Можно брать параллельно с DEV-114.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend"],
    "area": "notifications",
    "parallel": true,
    "dependsOn": ["DEV-111"],
    "acceptanceCriteria": [
      "Celery task send_notification_email(user_id, notification_type, context) — отправляет если в preferences включен email",
      "HTML-шаблон: логотип NLH, заголовок, текст, кнопка 'Открыть в платформе'; адаптивный (Gmail, Outlook, mobile)",
      "По умолчанию email для: booking_confirmed, task_assigned, invitation, guest_validated, leave_review",
      "Celery task send_bulk_email — для объявлений с notify_email=true",
      "Отправка через Django send_mail; unsubscribe ссылка → /settings/notifications"
    ]
  },
  {
    "identifier": "DEV-114",
    "title": "Notification Preferences — настройки уведомлений",
    "description": "Персональные настройки уведомлений. NotificationPreference модель существует.\n\nТребует хелпер из DEV-111. Можно брать параллельно с DEV-113.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "notifications",
    "parallel": true,
    "dependsOn": ["DEV-111"],
    "acceptanceCriteria": [
      "GET /api/v1/notifications/preferences/ — {notification_type: {in_app: bool, email: bool}} для каждого типа",
      "PATCH — частичное обновление; при первом обращении создаётся с дефолтами",
      "POST .../do-not-disturb/ с {enabled, until?} — подавляет все in-app до until",
      "create_notification проверяет preferences: in_app=false → не создаёт; email=false → не отправляет",
      "Фронт-интеграция: /settings/notifications — таблица типов с тогглами in_app/email"
    ]
  },
  {
    "identifier": "DEV-115",
    "title": "Superadmin Analytics — карточки-метрики",
    "description": "API аналитики суперадмина: обзорные числа (карточки). superadmin_dashboard view существует.\n\nАгрегирует данные из: компании (DEV-60), бронирования (DEV-73), сервисные заявки (DEV-108). Берётся после этих модулей.\n\nОт этого тикета зависит DEV-116 (графики) и DEV-118 (экспорт).",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "analytics",
    "parallel": true,
    "dependsOn": ["DEV-60", "DEV-73", "DEV-108"],
    "acceptanceCriteria": [
      "GET /api/v1/analytics/superadmin/ с period (7d/30d/90d/custom), date_from, date_to",
      "overview: total_companies, active_companies, total_users, active_users_7d, bookings_today, guests_today, open_service_requests",
      "Фильтр по resource_type, company_id",
      "Фронт-интеграция: /admin/analytics — карточки наверху"
    ]
  },
  {
    "identifier": "DEV-116",
    "title": "Superadmin Analytics — графики и таблицы",
    "description": "Расширить аналитику суперадмина: графики загруженности, пиковые часы, таблицы top/low.\n\nПродолжение DEV-115 (карточки). Переиспользует те же агрегации + добавляет новые.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "analytics",
    "parallel": false,
    "dependsOn": ["DEV-115"],
    "acceptanceCriteria": [
      "resource_utilization: [{date, desk_bookings, room_bookings, parking_bookings, capsule_bookings}]",
      "peak_hours: [{day_of_week, hour, booking_count}] — тепловая карта",
      "new_registrations: [{week, count}]; service_requests_by_type: [{type, count}]",
      "top_resources: топ-5 по booking_count; top_companies: топ-5; low_utilization: <20%",
      "Фронт-интеграция: графики (chart.js/recharts) + таблицы на /admin/analytics"
    ]
  },
  {
    "identifier": "DEV-117",
    "title": "Company Admin Analytics — дашборд компании",
    "description": "Аналитика для company_admin по своей компании. company_dashboard view существует.\n\nАгрегирует данные из: компании (DEV-60), бронирования (DEV-73), CRM (DEV-84), хранилище (DEV-95). Берётся после этих модулей.\n\nМожно брать параллельно с DEV-115.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "analytics",
    "parallel": true,
    "dependsOn": ["DEV-60", "DEV-73", "DEV-84"],
    "acceptanceCriteria": [
      "GET /api/v1/analytics/company/ — total_employees, active_7d, bookings_month, storage {used, limit}, active_crm_tasks, guest_visits_month",
      "active_crm_tasks по статусам: {todo, in_progress, done}",
      "employee_activity: [{user_id, full_name, booking_count_30d, task_count_active, last_login}]",
      "Только company_admin и superadmin; employee — 403",
      "Фронт-интеграция: /company/analytics — карточки + таблица"
    ]
  },
  {
    "identifier": "DEV-118",
    "title": "Analytics Export — выгрузка в CSV",
    "description": "Экспорт аналитики в CSV для отчётности.\n\nТребует superadmin analytics (DEV-115) и company analytics (DEV-117) — переиспользует их данные для выгрузки.",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend"],
    "area": "analytics",
    "parallel": false,
    "dependsOn": ["DEV-115", "DEV-117"],
    "acceptanceCriteria": [
      "GET /api/v1/analytics/superadmin/export/?format=csv&period=30d — CSV superadmin",
      "GET /api/v1/analytics/company/export/?format=csv — CSV company admin",
      "Content-Type: text/csv; Content-Disposition: attachment; UTF-8 BOM",
      "Только superadmin/company_admin; остальные — 403"
    ]
  },
  {
    "identifier": "DEV-119",
    "title": "Role-Based Dashboard API — дашборд по ролям",
    "description": "API дашборда, возвращающего данные в зависимости от роли пользователя. Каждая роль — свой набор виджетов.\n\nЭтот тикет делается последним — агрегирует данные из всех блоков: аналитика superadmin (DEV-115), аналитика company (DEV-117), уведомления (DEV-111), бронирования (DEV-73), CRM (DEV-84), объявления (DEV-109).",
    "block": "Блок 7 — Уведомления и аналитика",
    "labels": ["backend", "frontend-integration"],
    "area": "analytics",
    "parallel": false,
    "dependsOn": ["DEV-115", "DEV-117", "DEV-111", "DEV-73", "DEV-84", "DEV-109"],
    "acceptanceCriteria": [
      "GET /api/v1/dashboard/ — данные зависят от role текущего пользователя",
      "Superadmin: total_companies, total_users, bookings_today, recent_events (10), quick_actions",
      "Company admin: employee_count, active_tasks, bookings_today, announcement_feed (5), pending_approvals {leaves, guest_passes}",
      "Employee: my_tasks_today, my_bookings_today, announcement_feed (5), unread_notifications_count",
      "Guest: my_bookings_today, quick_booking {available_desks, available_rooms}, bc_announcements (5)",
      "Ответ содержит role и user {id, full_name, avatar}",
      "Фронт-интеграция: главная / рендерит виджеты по role"
    ]
  }
]
