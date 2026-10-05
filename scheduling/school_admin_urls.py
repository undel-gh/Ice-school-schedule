from django.urls import path

from . import school_admin_views

app_name = "school_scheduling"

urlpatterns = [
    path("groups/", school_admin_views.manager_groups, name="groups"),
    path("groups/new/", school_admin_views.manager_group_create, name="group_create"),
    path("groups/<uuid:group_id>/", school_admin_views.manager_group_detail, name="group_detail"),
    path("groups/<uuid:group_id>/edit/", school_admin_views.manager_group_edit, name="group_edit"),
    path("lesson-types/", school_admin_views.manager_lesson_types, name="lesson_types"),
    path("lesson-types/new/", school_admin_views.manager_lesson_type_create, name="lesson_type_create"),
    path("lesson-types/<uuid:lesson_type_id>/edit/", school_admin_views.manager_lesson_type_edit, name="lesson_type_edit"),
    path("venues/", school_admin_views.manager_venues, name="venues"),
    path("venues/new/", school_admin_views.manager_venue_create, name="venue_create"),
    path("venues/<uuid:venue_id>/edit/", school_admin_views.manager_venue_edit, name="venue_edit"),
    path("memberships/", school_admin_views.manager_memberships, name="memberships"),
    path("memberships/new/", school_admin_views.manager_membership_create, name="membership_create"),
    path("memberships/<uuid:membership_id>/edit/", school_admin_views.manager_membership_edit, name="membership_edit"),
]
