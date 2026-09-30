from django.urls import path

from . import school_admin_views

app_name = "school_scheduling"

urlpatterns = [
    path("groups/", school_admin_views.manager_groups, name="groups"),
    path("groups/new/", school_admin_views.manager_group_create, name="group_create"),
    path("groups/<uuid:group_id>/", school_admin_views.manager_group_detail, name="group_detail"),
    path("groups/<uuid:group_id>/edit/", school_admin_views.manager_group_edit, name="group_edit"),
    path("memberships/", school_admin_views.manager_memberships, name="memberships"),
    path("memberships/new/", school_admin_views.manager_membership_create, name="membership_create"),
    path("memberships/<uuid:membership_id>/edit/", school_admin_views.manager_membership_edit, name="membership_edit"),
]
