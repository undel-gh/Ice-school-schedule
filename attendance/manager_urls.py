from django.urls import path

from . import manager_medical_registration_views, manager_views

app_name = "attendance_manager"

urlpatterns = [
    path("coverage/", manager_views.manager_coverage_report, name="coverage_report"),
    path(
        "coverage/<uuid:attendance_id>/",
        manager_views.manager_coverage_detail,
        name="coverage_detail",
    ),
    path(
        "coverage/<uuid:attendance_id>/rebind/",
        manager_views.manager_coverage_rebind,
        name="coverage_rebind",
    ),
    path(
        "coverage/<uuid:attendance_id>/recover/",
        manager_views.manager_coverage_recover,
        name="coverage_recover",
    ),
    path("medical/", manager_views.manager_medical_absences, name="medical"),
    path(
        "medical/register/",
        manager_medical_registration_views.manager_medical_registration,
        name="medical_register",
    ),
    path(
        "medical/register/<uuid:attendance_id>/",
        manager_medical_registration_views.manager_medical_register_absence,
        name="medical_register_absence",
    ),
    path("medical/<uuid:justification_id>/", manager_views.manager_medical_detail, name="medical_detail"),
    path("medical/<uuid:justification_id>/verify/", manager_views.manager_medical_verify, name="medical_verify"),
    path("medical/<uuid:justification_id>/reject/", manager_views.manager_medical_reject, name="medical_reject"),
    path("medical/<uuid:justification_id>/revoke/", manager_views.manager_medical_revoke, name="medical_revoke"),
]
