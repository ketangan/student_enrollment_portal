from django.urls import path
from .views import apply_view

urlpatterns = [
    path("schools/<slug:school_slug>/apply", apply_view, name="apply"),
]
