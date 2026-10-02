from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
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
    assert "Календарный месяц" in body
    assert "Истёк" in body
    assert "Лёд" in body


@pytest.mark.django_db
def test_home_routes_manager_to_operations(client):
    manager = User.objects.create_user(
        username="manager-home",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    client.force_login(manager)

    response = client.get(reverse("scheduling:home"))

    assert response.status_code == 302
    assert response.url == reverse("subscriptions:manager_operations")


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
    from scheduling.models import GroupMembership, TrainingGroup

    group = TrainingGroup.objects.create(
        code="hold-web-group",
        name="Hold Web Group",
    )
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-web-calendar",
        name="Calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=date(2026, 9, 1),
        ends_on=None,
        created_by=manager,
    )

    client.force_login(manager)
    created = client.post(
        reverse("subscriptions:manager_place_hold_create"),
        {
            "student": str(student.id),
            "group": str(group.id),
            "period_scheme": str(scheme.id),
            "reference_date": "2026-11-10",
        },
    )
    assert created.status_code == 302

    hold = GroupPlaceHold.objects.get(student=student, group=group)
    assert hold.period_from == date(2026, 11, 1)
    assert hold.period_until == date(2026, 11, 30)
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
    assert hold.suspended_membership_id is not None
    assert hold.seat_reservation_id is not None

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


@pytest.mark.django_db
def test_manager_can_restore_group_place_hold_from_web(client):
    manager = User.objects.create_user(
        username="hold-restore-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    student = Student.objects.create(display_name="Hold restore student")
    from scheduling.models import GroupMembership, TrainingGroup

    group = TrainingGroup.objects.create(
        code="hold-restore-web-group",
        name="Hold Restore Web Group",
        capacity=1,
    )
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-restore-web-calendar",
        name="Calendar restore",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    original_membership = GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=date(2026, 9, 1),
        ends_on=None,
        created_by=manager,
    )

    client.force_login(manager)
    created = client.post(
        reverse("subscriptions:manager_place_hold_create"),
        {
            "student": str(student.id),
            "group": str(group.id),
            "period_scheme": str(scheme.id),
            "reference_date": "2026-11-10",
        },
    )
    assert created.status_code == 302

    hold = GroupPlaceHold.objects.get(student=student, group=group)
    confirmed = client.post(
        reverse(
            "subscriptions:manager_place_hold_confirm",
            kwargs={"hold_id": hold.id},
        )
    )
    assert confirmed.status_code == 302

    original_membership.refresh_from_db()
    hold.refresh_from_db()
    assert original_membership.ends_on == date(2026, 10, 31)
    assert hold.status == GroupPlaceHold.Status.ACTIVE

    restored = client.post(
        reverse(
            "subscriptions:manager_place_hold_restore",
            kwargs={"hold_id": hold.id},
        )
    )
    assert restored.status_code == 302

    hold.refresh_from_db()
    assert hold.status == GroupPlaceHold.Status.RESTORED
    assert hold.restored_at is not None
    assert hold.restored_membership_id is not None
    assert hold.restored_membership.starts_on == date(2026, 12, 1)
    assert hold.restored_membership.ends_on is None


@pytest.mark.django_db
def test_manager_subscription_detail_adjusts_and_cancels(client):
    manager = User.objects.create_user(
        username="detail-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    student = Student.objects.create(display_name="Detail student")
    scheme = SubscriptionPeriodScheme.objects.create(
        code="detail-calendar",
        name="Calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = SubscriptionPlan.objects.create(
        code="detail-plan",
        name="Detail 4 ICE",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=4,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
        actor=manager,
    )
    attach_subscription_period(
        subscription_id=subscription.id,
        scheme_id=scheme.id,
        reference_date=date(2026, 10, 1),
        actor=manager,
    )
    allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE,
    )

    client.force_login(manager)
    detail = client.get(
        reverse(
            "subscriptions:manager_subscription_detail",
            kwargs={"subscription_id": subscription.id},
        )
    )
    body = detail.content.decode()
    assert detail.status_code == 200
    assert "Detail student" in body
    assert "Detail 4 ICE" in body
    assert "История списаний и начислений" in body

    adjusted = client.post(
        reverse(
            "subscriptions:manager_allowance_adjust",
            kwargs={"allowance_id": allowance.id},
        ),
        {
            "delta": "1",
            "reason": "manager correction",
        },
    )
    assert adjusted.status_code == 302

    from subscriptions.balances import ledger_balance

    assert ledger_balance(allowance.id) == 5

    cancelled = client.post(
        reverse(
            "subscriptions:manager_subscription_cancel",
            kwargs={"subscription_id": subscription.id},
        ),
        {"confirm": "on"},
    )
    assert cancelled.status_code == 302
    subscription.refresh_from_db()
    assert subscription.cancelled_at is not None


@pytest.mark.django_db
def test_manager_subscription_detail_missing_returns_404(client):
    import uuid

    manager = User.objects.create_user(
        username="detail-404-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    client.force_login(manager)

    response = client.get(
        reverse(
            "subscriptions:manager_subscription_detail",
            kwargs={"subscription_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_makeup_cancel_checks_permission_before_lookup(client):
    import uuid

    user = User.objects.create_user(
        username="makeup-cancel-no-permission",
        password="test",
    )
    client.force_login(user)

    response = client.post(
        reverse(
            "subscriptions:manager_makeup_cancel",
            kwargs={"makeup_id": uuid.uuid4()},
        ),
        {"reason": "should not reveal existence"},
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_manager_subscription_report_rejects_excessive_date_range(client):
    manager = User.objects.create_user(
        username="report-range-manager",
        password="test",
        is_superuser=True,
        is_staff=True,
    )
    client.force_login(manager)

    response = client.get(
        reverse("subscriptions:manager_subscription_report"),
        {
            "from": "2026-01-01",
            "until": "2027-01-03",
        },
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_scoped_schedule_manager_uses_operations_dashboard(client):
    manager = User.objects.create_user(
        username="scoped-schedule-manager",
        password="test",
        is_staff=True,
    )
    manager.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="scheduling",
            codename="view_lesson",
        )
    )
    client.force_login(manager)

    home = client.get(reverse("scheduling:home"))
    assert home.status_code == 302
    assert home.url == reverse("subscriptions:manager_operations")

    dashboard = client.get(reverse("subscriptions:manager_operations"))
    body = dashboard.content.decode()
    assert dashboard.status_code == 200
    assert "Занятия" in body
    assert "Абонементы" not in body
