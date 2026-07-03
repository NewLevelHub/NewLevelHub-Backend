import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.access.models import GuestPass
from apps.bookings.models import Booking, Resource
from apps.crm.models import Board, Column, Task
from apps.hr.models import LeaveRequest
from apps.users.models import User

COMPANY_ID = 134
ADMIN_USER_ID = 2
EMPLOYEE_IDS = [2, 7711, 214494, 322848, 328478, 328905]

COLUMN_NAMES = ['Backlog', 'dsds', 'вывы']

GUEST_NAMES = [
    'Алибек Жаксыбеков',
    'Марина Соколова',
    'Дастан Нурланов',
    'Анна Петрова',
    'Сергей Волков',
    'Жанара Касымова',
    'Николай Орлов',
    'Айгерим Бекова',
    'Тимур Рахимов',
    'Светлана Иванова',
    'Болат Сейткали',
]

TASK_TITLES = [
    'Настроить CI/CD pipeline',
    'Провести код ревью',
    'Обновить документацию',
    'Исправить баг в авторизации',
    'Добавить тесты для API',
    'Оптимизировать запросы к БД',
    'Разработать новый отчёт',
    'Провести ретроспективу',
    'Обновить зависимости',
    'Настроить мониторинг',
    'Написать спецификацию',
    'Провести демо для клиента',
    'Рефакторинг модуля хранилища',
    'Настроить уведомления',
    'Протестировать новый функционал',
]


class Command(BaseCommand):
    help = 'Seed analytics test data for company 134'

    def handle(self, *args, **options):
        now = timezone.now()
        today = timezone.localdate()

        # Current month boundaries (aware datetimes)
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Last month boundaries
        if now.month == 1:
            last_month_start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = current_month_start - timedelta(seconds=1)
        else:
            last_month_start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = current_month_start - timedelta(seconds=1)

        # Fetch users
        users = list(User.objects.filter(id__in=EMPLOYEE_IDS))
        if not users:
            self.stdout.write(self.style.ERROR('No users found with specified IDs'))
            return
        self.stdout.write(f'Found {len(users)} users')

        # Fetch resources
        resources = list(Resource.objects.filter(is_active=True)[:5])
        if not resources:
            resources = list(Resource.objects.all()[:5])
        if not resources:
            self.stdout.write(self.style.ERROR('No resources found in DB'))
            return
        self.stdout.write(f'Found {len(resources)} resources')

        # ──────────────────────────────────────────────────
        # 1. BOOKINGS — 15 current month + 5 last month
        # ──────────────────────────────────────────────────
        bookings_created = 0

        # 15 bookings in current month
        days_in_current_month = (now.day)  # up to today
        for i in range(15):
            try:
                day_offset = i % max(days_in_current_month, 1)
                booking_date = current_month_start + timedelta(days=day_offset)
                start_hour = 9 + (i % 8)  # hours 9–16
                start_time = booking_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                end_time = start_time + timedelta(hours=1)
                user = random.choice(users)
                resource = resources[i % len(resources)]
                Booking.objects.create(
                    user=user,
                    resource=resource,
                    company_id=COMPANY_ID,
                    status='confirmed',
                    start_time=start_time,
                    end_time=end_time,
                )
                bookings_created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Booking #{i+1} (current month) skipped: {e}'))

        # 5 bookings in last month
        last_month_days = (last_month_end.day if hasattr(last_month_end, 'day') else 28)
        for i in range(5):
            try:
                day_offset = i * 4  # spread across last month
                booking_date = last_month_start + timedelta(days=day_offset)
                start_hour = 10 + (i % 6)
                start_time = booking_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                end_time = start_time + timedelta(hours=1)
                user = random.choice(users)
                resource = resources[i % len(resources)]
                Booking.objects.create(
                    user=user,
                    resource=resource,
                    company_id=COMPANY_ID,
                    status='confirmed',
                    start_time=start_time,
                    end_time=end_time,
                )
                bookings_created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Booking #{i+1} (last month) skipped: {e}'))

        self.stdout.write(f'Bookings created: {bookings_created}')

        # ──────────────────────────────────────────────────
        # 2. CRM TASKS — 3-5 per column
        # ──────────────────────────────────────────────────
        tasks_created = 0

        try:
            board = Board.objects.get(company_id=COMPANY_ID, name='123')
            self.stdout.write(f'Found board: id={board.id}, name={board.name}')
        except Board.DoesNotExist:
            self.stdout.write(self.style.ERROR('Board with name="123" for company 134 not found'))
            board = None
        except Board.MultipleObjectsReturned:
            board = Board.objects.filter(company_id=COMPANY_ID, name='123').first()
            self.stdout.write(f'Multiple boards found, using first: id={board.id}')

        if board:
            title_index = 0
            for col_name in COLUMN_NAMES:
                try:
                    column = Column.objects.get(board=board, name=col_name)
                except Column.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f'Column "{col_name}" not found in board {board.id}, skipping'))
                    continue
                except Column.MultipleObjectsReturned:
                    column = Column.objects.filter(board=board, name=col_name).first()

                tasks_in_col = random.randint(3, 5)
                for j in range(tasks_in_col):
                    try:
                        title = TASK_TITLES[title_index % len(TASK_TITLES)]
                        title_index += 1
                        Task.objects.create(
                            title=f'{title} [{col_name}]',
                            column=column,
                            created_by_id=ADMIN_USER_ID,
                            assignee=random.choice(users),
                            priority=random.choice(['low', 'medium', 'high']),
                            is_archived=False,
                            is_deleted=False,
                        )
                        tasks_created += 1
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f'Task in "{col_name}" #{j+1} skipped: {e}'))

        self.stdout.write(f'CRM tasks created: {tasks_created}')

        # ──────────────────────────────────────────────────
        # 3. GUEST PASSES — 8 current month + 3 last month
        # ──────────────────────────────────────────────────
        passes_created = 0

        # 8 passes in current month
        for i in range(8):
            try:
                day_offset = i * 2
                valid_from_date = current_month_start + timedelta(days=day_offset)
                valid_until_date = valid_from_date + timedelta(days=1)
                GuestPass.objects.create(
                    company_id=COMPANY_ID,
                    created_by_id=ADMIN_USER_ID,
                    guest_name=GUEST_NAMES[i % len(GUEST_NAMES)],
                    guest_email=f'guest{i+1}@example.com',
                    status='active',
                    valid_from=valid_from_date,
                    valid_until=valid_until_date,
                )
                passes_created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'GuestPass #{i+1} (current month) skipped: {e}'))

        # 3 passes in last month
        for i in range(3):
            try:
                day_offset = i * 7
                valid_from_date = last_month_start + timedelta(days=day_offset)
                valid_until_date = valid_from_date + timedelta(days=1)
                GuestPass.objects.create(
                    company_id=COMPANY_ID,
                    created_by_id=ADMIN_USER_ID,
                    guest_name=GUEST_NAMES[(i + 8) % len(GUEST_NAMES)],
                    guest_email=f'guest_prev{i+1}@example.com',
                    status='active',
                    valid_from=valid_from_date,
                    valid_until=valid_until_date,
                )
                passes_created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'GuestPass #{i+1} (last month) skipped: {e}'))

        self.stdout.write(f'Guest passes created: {passes_created}')

        # ──────────────────────────────────────────────────
        # 4. LEAVE REQUESTS — 3 pending
        # ──────────────────────────────────────────────────
        leaves_created = 0
        leave_users = users[:3] if len(users) >= 3 else users

        leave_configs = [
            {'leave_type': 'vacation', 'start_offset': 5, 'duration': 7},
            {'leave_type': 'day_off', 'start_offset': 10, 'duration': 1},
            {'leave_type': 'sick_leave', 'start_offset': 15, 'duration': 3},
        ]

        for i, cfg in enumerate(leave_configs):
            try:
                user = leave_users[i % len(leave_users)]
                start_date = today + timedelta(days=cfg['start_offset'])
                end_date = start_date + timedelta(days=cfg['duration'] - 1)
                LeaveRequest.objects.create(
                    user=user,
                    company_id=COMPANY_ID,
                    leave_type=cfg['leave_type'],
                    status='pending',
                    start_date=start_date,
                    end_date=end_date,
                    assigned_reviewer_id=ADMIN_USER_ID,
                    comment='Тестовый запрос на отпуск (seed data)',
                )
                leaves_created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'LeaveRequest #{i+1} skipped: {e}'))

        self.stdout.write(f'Leave requests created: {leaves_created}')

        # ──────────────────────────────────────────────────
        # Summary
        # ──────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f'\nAnalytics test data seeded successfully:\n'
            f'  Bookings:      {bookings_created} (15 current month + 5 last month target)\n'
            f'  CRM Tasks:     {tasks_created}\n'
            f'  Guest Passes:  {passes_created} (8 current month + 3 last month target)\n'
            f'  Leave Requests:{leaves_created}'
        ))
