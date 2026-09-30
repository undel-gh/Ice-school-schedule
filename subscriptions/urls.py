from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path(
        "reports/subscriptions/",
        views.manager_subscription_report_view,
        name="manager_subscription_report",
    ),
]
