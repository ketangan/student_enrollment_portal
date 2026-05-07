from django.urls import path
from core import views_ops

urlpatterns = [
    path("", views_ops.ops_dashboard_view, name="ops_dashboard"),
    path("schools/", views_ops.ops_schools_list_view, name="ops_schools_list"),
    path("schools/new/", views_ops.ops_school_create_view, name="ops_school_create"),
    path("schools/<slug:slug>/", views_ops.ops_school_detail_view, name="ops_school_detail"),
    path("schools/<slug:slug>/members/add/", views_ops.ops_school_member_add_view, name="ops_school_member_add"),
    path("schools/<slug:slug>/members/<int:user_id>/remove/", views_ops.ops_school_member_remove_view, name="ops_school_member_remove"),
    path("users/", views_ops.ops_users_list_view, name="ops_users_list"),
    path("users/new/", views_ops.ops_user_create_view, name="ops_user_create"),
    path("users/<int:user_id>/", views_ops.ops_user_detail_view, name="ops_user_detail"),
    path("users/<int:user_id>/toggle-active/", views_ops.ops_user_deactivate_view, name="ops_user_toggle_active"),
]
