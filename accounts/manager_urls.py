from django.urls import path

from . import manager_views

app_name = "accounts_manager"

urlpatterns = [
    path("invitations/", manager_views.manager_account_invitations, name="invitations"),
    path("invitations/new/", manager_views.manager_account_invitation_create, name="invitation_create"),
    path("invitations/<uuid:invitation_id>/revoke/", manager_views.manager_account_invitation_revoke, name="invitation_revoke"),
    path("students/", manager_views.manager_students, name="students"),
    path("students/new/", manager_views.manager_student_create, name="student_create"),
    path("students/<uuid:student_id>/", manager_views.manager_student_detail, name="student_detail"),
    path("students/<uuid:student_id>/edit/", manager_views.manager_student_edit, name="student_edit"),
    path("students/<uuid:student_id>/access/new/", manager_views.manager_student_access_create, name="student_access_create"),
    path("access/<uuid:access_id>/edit/", manager_views.manager_student_access_edit, name="student_access_edit"),
    path("coaches/", manager_views.manager_coaches, name="coaches"),
    path("coaches/new/", manager_views.manager_coach_create, name="coach_create"),
    path("coaches/<uuid:coach_id>/edit/", manager_views.manager_coach_edit, name="coach_edit"),
]
