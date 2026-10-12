"""Host scoping for the existing Django operator screens and their choices."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import CharField, Q
from django.db.models.functions import Cast

from core.services.portal_environment import request_environment, scope_form_choices, scope_schools, scope_users


class PortalScopedAdminMixin:
    portal_school_lookup = "school__"
    portal_users = False
    portal_user_lookup = ""

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.portal_users:
            return scope_users(queryset, request)
        if self.portal_user_lookup:
            return queryset.filter(**{self.portal_user_lookup + "__in": scope_users(get_user_model().objects.all(), request)})
        return scope_schools(queryset, request, self.portal_school_lookup)

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class ScopedForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                scope_form_choices(self, request)

        return ScopedForm

    def get_list_filter(self, request):
        return [(item, admin.RelatedOnlyFieldListFilter) if item in {"school", "profile", "user"} else item
            for item in super().get_list_filter(request)]


def scope_admin_history(queryset, request):
    """Django's own Recent Actions must not bypass the tenant-filtered lists."""
    if not request_environment(request):
        return queryset
    predicate = Q(pk__in=[])
    for model, model_admin in admin.site._registry.items():
        if isinstance(model_admin, PortalScopedAdminMixin):
            ids = model_admin.get_queryset(request).order_by().annotate(
                portal_object_id=Cast("pk", CharField())).values("portal_object_id")
            predicate |= Q(content_type=ContentType.objects.get_for_model(model), object_id__in=ids)
    # Deleted/unclassifiable legacy objects remain stored, but are not guessed into a host.
    return queryset.filter(predicate)
