from django.urls import path
from .views import apply_view, apply_success_view

urlpatterns = [
    path("schools/<slug:school_slug>/apply", apply_view, name="apply"),
    path("schools/<slug:school_slug>/apply/success", apply_success_view, name="apply_success"),
]
