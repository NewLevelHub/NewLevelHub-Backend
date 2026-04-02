"""
Миксины для мультитенанси: автоматическая фильтрация queryset по company
текущего пользователя.
"""


class CompanyQuerySetMixin:
    """
    Автоматически ограничивает queryset по company пользователя.
    Суперадмин видит всё; остальные — только данные своей компании.
    Предполагается, что модель имеет поле `company` (FK).
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
    При создании объекта автоматически проставляет company
    из текущего пользователя.
    """
    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)
