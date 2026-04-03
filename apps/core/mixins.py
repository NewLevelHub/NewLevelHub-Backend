"""
Mixins for multi-tenancy: automatic queryset scoping by the current user's
company, and automatic company assignment on object creation.
"""


class CompanyIsolationMixin:
    """
    Scope a viewset's queryset to the requesting user's company.

    Behaviour by role:
      - superadmin  → sees all objects across every company
      - company_admin / employee with a company → sees only their company's data
      - any user without a company (incl. guest) → empty queryset

    The mixin assumes the model has a ``company`` FK.  Override
    ``company_field`` if the FK is named differently::

        class MyView(CompanyIsolationMixin, ListAPIView):
            company_field = 'organisation'   # FK name on the model

    Works with Django's ``get_queryset()`` pattern via ``super()`` chaining,
    so it composes correctly with other mixins and DRF's generic views.
    """

    company_field = 'company'

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == 'superadmin':
            return qs
        if user.company_id:
            return qs.filter(**{self.company_field: user.company_id})
        return qs.none()


class SetCompanyOnCreateMixin:
    """
    Automatically stamp the ``company`` field from the requesting user when
    a new object is created via a DRF generic create view.

    Relies on the serializer accepting ``company`` as a keyword argument in
    ``save()`` (the default DRF behaviour).
    """

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


# ---------------------------------------------------------------------------
# Backward-compatibility alias
# ---------------------------------------------------------------------------
# The mixin was previously named CompanyQuerySetMixin.  Keep the old name so
# that any code already importing it continues to work without changes.
CompanyQuerySetMixin = CompanyIsolationMixin
