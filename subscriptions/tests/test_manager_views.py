from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Student
from core.choices import SubscriptionCategory
from subscriptions.models import (
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    attach_subscription_period,
    issue_subscription,
)

User = get_user_model()


@pytest.mark.django_db
def test_manager_subscription_report_requires_permission(client):
    user = User.objects.create_user(
        username="report-no-permission",
        password="test",
    )
    client.force_login(user)

    response = client.get(
        reverse("subscriptions:manager_subscription_report")
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_manager_subscription_report_renders_subscription_summary(client):
    manager = User.objects.create_user(
        username="report-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    student = Student.objects.create(display_name="Отчётный ученик")
    scheme = SubscriptionPeriodScheme.objects.create(
        code="report-view-calendar",
        name="Календарный месяц",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = SubscriptionPlan.objects.create(
        code="report-view-plan",
        name="8 льдов",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=manager,
    )
    attach_subscription_period(
        subscription_id=subscription.id,
        scheme_id=scheme.id,
        reference_date=date(2026, 9, 1),
        actor=manager,
    )

    client.force_login(manager)
    response = client.get(
        reverse("subscriptions:manager_subscription_report"),
        {
            "student": str(student.id),
            "from": "2026-09-01",
            "until": "2026-09-30",
        },
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert "Отчёт по абонементам" in body
    assert "Отчётный ученик" in body
    assert "8 льдов" in body
    assert "отходил" in body
    assert "остаток" in body
    assert "переносов доступно" in body


@pytest.mark.django_db
def test_home_routes_manager_to_report(client):
    manager = User.objects.create_user(
        username="manager-home",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    client.force_login(manager)

    response = client.get(reverse("scheduling:home"))

    assert response.status_code == 302
    assert response.url == reverse(
        "subscriptions:manager_subscription_report"
    )
