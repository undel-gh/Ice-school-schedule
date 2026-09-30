from django.urls import path

from . import views

app_name = "audit_manager"

urlpatterns = [
    path("", views.manager_audit_events, name="events"),
]
