import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger('django.core.mail')


class LoggingEmailBackend(BaseEmailBackend):
    """Development email backend that routes emails through Python logging.

    Unlike ConsoleEmailBackend (which writes to sys.stderr), this backend
    uses the logging framework directly — making email content visible in
    Celery worker logs even when running inside forked pool workers.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        count = 0
        for message in email_messages:
            try:
                msg = message.message()
                charset = (
                    msg.get_charset().get_output_charset()
                    if msg.get_charset() else 'utf-8'
                )
                msg_data = msg.as_bytes().decode(charset)
                logger.info(
                    'EMAIL to=%s subject=%r\n%s\n%s',
                    ', '.join(message.recipients()),
                    message.subject,
                    '-' * 79,
                    msg_data,
                )
                count += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return count
