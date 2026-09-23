from __future__ import annotations

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Student
from subscriptions.models import (
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import issue_subscription


@pytest.mark.django_db
def test_process_subscription_lifecycle_command_uses_explicit_date(django_user_model):
    actor = django_user_model.objects.create_user(
        username="command-admin",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    student = Student.objects.create(display_name="Command Student")
    plan = SubscriptionPlan.objects.create(code="command-plan", name="Command plan")
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=1,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 8, 1),
        valid_until=date(2026, 8, 31),
        actor=actor,
    )

    out = StringIO()
    call_command(
        "process_subscription_lifecycle",
        "--date",
        "2026-09-15",
        stdout=out,
    )

    output = out.getvalue()
    assert "Processed lifecycle for 2026-09-15" in output
    assert "expired=1" in output


@pytest.mark.django_db
def test_process_subscription_lifecycle_command_rejects_bad_date():
    with pytest.raises(CommandError):
        call_command(
            "process_subscription_lifecycle",
            "--date",
            "15.09.2026",
        )


@pytest.mark.django_db
def test_process_subscription_lifecycle_command_defaults_to_localdate(monkeypatch):
    monkeypatch.setattr(
        "subscriptions.management.commands.process_subscription_lifecycle.timezone.localdate",
        lambda: date(2026, 9, 23),
    )

    out = StringIO()
    call_command(
        "process_subscription_lifecycle",
        stdout=out,
    )

    assert "Processed lifecycle for 2026-09-23" in out.getvalue()



@pytest.mark.django_db
def test_issue_and_cancel_subscription_commands(django_user_model):
    actor = django_user_model.objects.create_user(
        username="ops-admin",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    student = Student.objects.create(display_name="Ops Student")
    plan = SubscriptionPlan.objects.create(
        code="ops-plan",
        name="Ops plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=2,
    )

    out = StringIO()
    call_command(
        "issue_subscription",
        "--student",
        str(student.id),
        "--plan",
        str(plan.id),
        "--valid-from",
        "2026-09-01",
        "--valid-until",
        "2026-09-30",
        "--actor",
        actor.username,
        stdout=out,
    )

    subscription = Subscription.objects.get(student=student, plan=plan)
    assert "Subscription issued" in out.getvalue()

    out = StringIO()
    call_command(
        "cancel_subscription",
        "--subscription",
        str(subscription.id),
        "--actor",
        actor.username,
        stdout=out,
    )

    subscription.refresh_from_db()
    assert subscription.cancelled_at is not None
    assert "Subscription cancelled" in out.getvalue()
