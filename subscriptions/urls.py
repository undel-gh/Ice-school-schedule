from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path(
        "reports/subscriptions/",
        views.manager_subscription_report_view,
        name="manager_subscription_report",
    ),
    path(
        "subscriptions/new/",
        views.manager_subscription_issue_view,
        name="manager_subscription_issue",
    ),
    path(
        "place-holds/",
        views.manager_place_holds_view,
        name="manager_place_holds",
    ),
    path(
        "place-holds/new/",
        views.manager_place_hold_create_view,
        name="manager_place_hold_create",
    ),
    path(
        "place-holds/<uuid:hold_id>/confirm/",
        views.manager_place_hold_confirm_view,
        name="manager_place_hold_confirm",
    ),
    path(
        "place-holds/<uuid:hold_id>/cancel/",
        views.manager_place_hold_cancel_view,
        name="manager_place_hold_cancel",
    ),
]
