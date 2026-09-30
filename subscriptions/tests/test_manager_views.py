from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Student
from core.choices import SubscriptionCategory
from subscriptions.models import (
    GroupPlaceHold,
    Subscription,
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


@pytest.mark.django_db
def test_manager_can_issue_period_subscription_from_web(client):
    manager = User.objects.create_user(
        username="issue-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    student = Student.objects.create(display_name="Web issue student")
    scheme = SubscriptionPeriodScheme.objects.create(
        code="web-issue-calendar",
        name="Calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = SubscriptionPlan.objects.create(
        code="web-issue-plan",
        name="Web 8 ICE",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )

    client.force_login(manager)
    response = client.post(
        reverse("subscriptions:manager_subscription_issue"),
        {
            "student": str(student.id),
            "plan": str(plan.id),
            "reference_date": "2026-10-15",
        },
    )

    assert response.status_code == 302
    subscription = Subscription.objects.get(student=student, plan=plan)
    assert subscription.valid_from == date(2026, 10, 1)
    assert subscription.valid_until == date(2026, 10, 31)
    assert subscription.billing_period.starts_on == date(2026, 10, 1)
    assert subscription.billing_period.ends_on == date(2026, 10, 31)


@pytest.mark.django_db
def test_manager_can_manage_group_place_hold_from_web(client):
    manager = User.objects.create_user(
        username="hold-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    student = Student.objects.create(display_name="Hold student")
    from scheduling.models import TrainingGroup

    group = TrainingGroup.objects.create(
        code="hold-web-group",
        name="Hold Web Group",
    )
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-web-calendar",
        name="Calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )

    client.force_login(manager)
    created = client.post(
        reverse("subscriptions:manager_place_hold_create"),
        {
            "student": str(student.id),
            "group": str(group.id),
            "period_scheme": str(scheme.id),
            "reference_date": "2026-10-10",
        },
    )
    assert created.status_code == 302

    hold = GroupPlaceHold.objects.get(student=student, group=group)
    assert hold.period_from == date(2026, 10, 1)
    assert hold.period_until == date(2026, 10, 31)
    assert hold.status == GroupPlaceHold.Status.PENDING_PAYMENT

    confirmed = client.post(
        reverse(
            "subscriptions:manager_place_hold_confirm",
            kwargs={"hold_id": hold.id},
        )
    )
    assert confirmed.status_code == 302
    hold.refresh_from_db()
    assert hold.status == GroupPlaceHold.Status.ACTIVE
    assert hold.fee_confirmed_at is not None

    cancelled = client.post(
        reverse(
            "subscriptions:manager_place_hold_cancel",
            kwargs={"hold_id": hold.id},
        ),
        {"reason": "family request"},
    )
    assert cancelled.status_code == 302
    hold.refresh_from_db()
    assert hold.status == GroupPlaceHold.Status.CANCELLED
    assert hold.cancellation_reason == "family request"
