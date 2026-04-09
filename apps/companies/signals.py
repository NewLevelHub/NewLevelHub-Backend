from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Company, CompanySettings


@receiver(post_save, sender=Company)
def create_company_settings(sender, instance, created, **kwargs):
    """Auto-create a CompanySettings row whenever a new Company is saved."""
    if created:
        CompanySettings.objects.get_or_create(company=instance)
