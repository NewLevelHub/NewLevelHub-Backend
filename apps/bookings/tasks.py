from celery import shared_task


@shared_task
def send_booking_reminder(booking_id):
    """Напоминание за 15 мин до начала бронирования."""
    # TODO: получить Booking, отправить уведомление пользователю и участникам
    pass


@shared_task
def auto_cancel_no_show():
    """Автоотмена бронирований конференц-залов, если no-show > 15 мин."""
    # TODO: найти confirmed bookings meeting_room, start_time + 15min < now, отменить
    pass


@shared_task
def complete_expired_bookings():
    """Автозавершение бронирований по окончании времени."""
    # TODO: Booking.objects.filter(status='confirmed', end_time__lt=now) → status='completed'
    pass


@shared_task
def generate_recurring_bookings():
    """Периодическая задача: создание Booking из RecurringBooking шаблонов."""
    # TODO: для каждого активного RecurringBooking создать реальный Booking
    #       на ближайшую подходящую дату, если ещё не создан
    pass
