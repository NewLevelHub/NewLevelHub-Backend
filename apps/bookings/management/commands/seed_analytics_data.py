import random
from datetime import timedelta, time, datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bookings.models import Resource, Booking
from apps.companies.models import Company
from apps.users.models import User

# Seed marker so we can identify and optionally clear seeded data
SEED_DESCRIPTION = '__seed_analytics__'

# Realistic booking hour distribution:
# morning peak 9-11, lunch dip, afternoon peak 14-16, some evening
HOUR_WEIGHTS = {
    9: 12, 10: 15, 11: 13,
    12: 6, 13: 7,
    14: 14, 15: 16, 16: 12,
    17: 8, 18: 5,
}
HOURS = list(HOUR_WEIGHTS.keys())
WEIGHTS = [HOUR_WEIGHTS[h] for h in HOURS]

RESOURCES_TO_CREATE = [
    {
        'name': 'Hot Desk A1',
        'resource_type': 'desk',
        'capacity': 1,
        'is_hot_desk': True,
        'floor': 1,
        'available_days': [0, 1, 2, 3, 4],
        'available_from': time(8, 0),
        'available_until': time(22, 0),
    },
    {
        'name': 'Hot Desk A2',
        'resource_type': 'desk',
        'capacity': 1,
        'is_hot_desk': True,
        'floor': 1,
        'available_days': [0, 1, 2, 3, 4],
        'available_from': time(8, 0),
        'available_until': time(22, 0),
    },
    {
        'name': 'Meeting Room Alpha',
        'resource_type': 'meeting_room',
        'capacity': 8,
        'is_hot_desk': False,
        'floor': 2,
        'has_projector': True,
        'has_whiteboard': True,
        'available_days': [0, 1, 2, 3, 4],
        'available_from': time(8, 0),
        'available_until': time(20, 0),
    },
    {
        'name': 'Parking Spot P1',
        'resource_type': 'parking',
        'capacity': 1,
        'is_hot_desk': False,
        'floor': 0,
        'available_days': [0, 1, 2, 3, 4, 5, 6],
        'available_from': time(7, 0),
        'available_until': time(22, 0),
    },
]

# How many bookings to generate per resource type
BOOKINGS_PER_RESOURCE = {
    'desk': 20,
    'meeting_room': 12,
    'parking': 8,
}


class Command(BaseCommand):
    help = 'Seed analytics test data (resources + bookings) for the last 30 days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            help='Company ID to use. Defaults to the first active company.',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete previously seeded resources and bookings before re-seeding.',
        )

    def handle(self, *args, **options):
        company = self._resolve_company(options['company_id'])
        user = self._resolve_user(company)

        self.stdout.write(f'Using company: {company.name} (id={company.id})')
        self.stdout.write(f'Using user:    {user.email} (id={user.id}, role={user.role})')

        if options['clear']:
            self._clear_seed_data()

        resources = self._ensure_resources()
        bookings = self._create_bookings(resources, company, user)

        self.stdout.write(self.style.SUCCESS(
            f'Done. Resources created/found: {len(resources)}. Bookings created: {len(bookings)}.'
        ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_company(self, company_id):
        if company_id is not None:
            try:
                return Company.objects.get(pk=company_id)
            except Company.DoesNotExist:
                self.stderr.write(self.style.ERROR(f'Company with id={company_id} does not exist.'))
                raise SystemExit(1)

        company = Company.objects.filter(is_active=True).order_by('id').first()
        if company is None:
            # Fall back to any company
            company = Company.objects.order_by('id').first()
        if company is None:
            self.stderr.write(self.style.ERROR(
                'No companies found in the database. Create at least one company first.'
            ))
            raise SystemExit(1)
        return company

    def _resolve_user(self, company):
        # Prefer a member of the company; fall back to superadmin
        user = (
            User.objects.filter(company=company, is_active=True)
            .order_by('id')
            .first()
        )
        if user is None:
            user = User.objects.filter(role='superadmin', is_active=True).order_by('id').first()
        if user is None:
            user = User.objects.filter(is_active=True).order_by('id').first()
        if user is None:
            self.stderr.write(self.style.ERROR('No active users found in the database.'))
            raise SystemExit(1)
        return user

    def _clear_seed_data(self):
        deleted_bookings, _ = Booking.objects.filter(description=SEED_DESCRIPTION).delete()
        # Only delete resources that have no non-seed bookings attached
        seed_resource_names = [r['name'] for r in RESOURCES_TO_CREATE]
        deleted_resources = 0
        for name in seed_resource_names:
            qs = Resource.objects.filter(name=name)
            for resource in qs:
                has_real_bookings = resource.bookings.exclude(description=SEED_DESCRIPTION).exists()
                if not has_real_bookings:
                    resource.delete()
                    deleted_resources += 1
        self.stdout.write(
            f'Cleared: {deleted_bookings} seed booking(s), {deleted_resources} seed resource(s).'
        )

    def _ensure_resources(self):
        """Create seed resources if they do not already exist. Returns list of Resource objects."""
        resources = []
        for spec in RESOURCES_TO_CREATE:
            name = spec['name']
            resource, created = Resource.objects.get_or_create(
                name=name,
                defaults={k: v for k, v in spec.items() if k != 'name'},
            )
            if created:
                self.stdout.write(f'  Created resource: {name}')
            else:
                self.stdout.write(f'  Found existing resource: {name}')
            resources.append(resource)
        return resources

    def _create_bookings(self, resources, company, user):
        """Generate ~60 bookings spread across the last 30 days."""
        now = timezone.now()
        tz = timezone.get_current_timezone()
        created = []

        for resource in resources:
            count = BOOKINGS_PER_RESOURCE.get(resource.resource_type, 10)
            for _ in range(count):
                booking = self._make_booking(resource, company, user, now, tz)
                if booking is not None:
                    created.append(booking)

        return created

    def _make_booking(self, resource, company, user, now, tz):
        """Create a single booking on a random past day at a realistic hour."""
        # Pick a random day in the last 30 days (exclude today to keep data historical)
        days_ago = random.randint(1, 30)
        target_date = (now - timedelta(days=days_ago)).date()

        # Skip weekends for resources that are only available Mon-Fri
        if target_date.weekday() >= 5 and resource.available_days and 6 not in resource.available_days:
            # Try a weekday offset instead
            target_date = target_date - timedelta(days=target_date.weekday() - 4)

        # Pick a start hour based on realistic weights
        start_hour = random.choices(HOURS, weights=WEIGHTS, k=1)[0]

        # Duration: desks 1-3 h, meeting rooms 1-2 h, parking full-day slot
        if resource.resource_type == 'desk':
            duration_hours = random.choice([1, 1, 2, 2, 3])
        elif resource.resource_type == 'meeting_room':
            duration_hours = random.choice([1, 1, 1, 2])
        else:
            duration_hours = random.choice([2, 4, 8])

        start_dt = timezone.make_aware(
            datetime.combine(target_date, time(start_hour, 0)), tz
        )
        end_dt = start_dt + timedelta(hours=duration_hours)

        # Avoid creating exact duplicates (same resource + same start)
        if Booking.objects.filter(
            resource=resource,
            start_time=start_dt,
            description=SEED_DESCRIPTION,
        ).exists():
            return None

        status = random.choices(['confirmed', 'completed'], weights=[30, 70], k=1)[0]

        return Booking.objects.create(
            resource=resource,
            user=user,
            company=company,
            start_time=start_dt,
            end_time=end_dt,
            status=status,
            description=SEED_DESCRIPTION,
        )
