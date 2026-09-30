from django.urls import path

from . import manager_views

app_name = "attendance_manager"

urlpatterns = [
    path("medical/", manager_views.manager_medical_absences, name="medical"),
    path("medical/<uuid:justification_id>/", manager_views.manager_medical_detail, name="medical_detail"),
    path("medical/<uuid:justification_id>/verify/", manager_views.manager_medical_verify, name="medical_verify"),
    path("medical/<uuid:justification_id>/reject/", manager_views.manager_medical_reject, name="medical_reject"),
    path("medical/<uuid:justification_id>/revoke/", manager_views.manager_medical_revoke, name="medical_revoke"),
]
