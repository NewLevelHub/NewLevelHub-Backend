from celery import shared_task


@shared_task
def send_verification_email(user_id):
    """Отправка email для подтверждения аккаунта."""
    # TODO: создать EmailVerificationToken, сформировать ссылку, отправить email
    pass


@shared_task
def send_password_reset_email(user_id):
    """Отправка ссылки для сброса пароля."""
    # TODO: создать PasswordResetToken, сформировать ссылку, отправить email
    pass
