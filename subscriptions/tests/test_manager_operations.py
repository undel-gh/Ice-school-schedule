from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import issue_subscription

User = get_user_model()


@pytest.fixture
def operations_context(db):
    manager = User.objects.create_user(
        username="operations-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(username="operations-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="operations-group", name="Operations Group")
    venue = Venue.objects.create(code="operations-venue", name="Operations Venue")
    lesson_type = LessonType.objects.create(
        code="operations-ice",
        name="ICE",
        subscription_category=SubscriptionCategory.ICE,
    )
    student = Student.objects.create(display_name="Operations Student")
    starts_at = timezone.now() - timedelta(days=2)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )
    return {
        "manager": manager,
        "student": student,
        "lesson": lesson,
        "lesson_type": lesson_type,
    }


@pytest.mark.django_db
def test_manager_grants_and_cancels_one_time_entitlement(client, operations_context):
    ctx = operations_context
    client.force_login(ctx["manager"])

    created = client.post(
        reverse("subscriptions:manager_one_time_create"),
        {
            "student": str(ctx["student"].id),
            "lesson": str(ctx["lesson"].id),
            "entitlement_type": OneTimeEntitlement.Type.SINGLE_ICE,
        },
    )
    assert created.status_code == 302
    entitlement = OneTimeEntitlement.objects.get(
        student=ctx["student"],
        lesson=ctx["lesson"],
    )

    cancelled = client.post(
        reverse(
            "subscriptions:manager_one_time_cancel",
            kwargs={"entitlement_id": entitlement.id},
        )
    )
    assert cancelled.status_code == 302
    entitlement.refresh_from_db()
    assert entitlement.cancelled_at is not None


@pytest.mark.django_db
def test_manager_creates_and_materializes_free_compensation(client, operations_context):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="operations-plan",
        name="Operations plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date - timedelta(days=5),
        valid_until=source_date + timedelta(days=5),
        actor=ctx["manager"],
    )
    policy = AbsenceCompensationPolicy.objects.create(
        code="operations-unexcused",
        version=1,
        name="Operations unexcused",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        effective_from=source_date.replace(day=1),
    )
    AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD,
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
    )
    attendance = Attendance.objects.create(
        lesson=ctx["lesson"],
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=ctx["lesson"].ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )

    client.force_login(ctx["manager"])
    created = client.post(
        reverse("subscriptions:manager_compensation_case_create"),
        {
            "attendance": str(attendance.id),
            "absence_reason": AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
            "policy_code": policy.code,
        },
    )
    assert created.status_code == 302
    case = AbsenceCompensationCase.objects.get(attendance=attendance)

    materialized = client.post(
        reverse(
            "subscriptions:manager_compensation_materialize_free",
            kwargs={"case_id": case.id},
        )
    )
    assert materialized.status_code == 302
    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert MakeupEntitlement.objects.filter(
        student=ctx["student"],
        source_lesson=ctx["lesson"],
        reason=MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
    ).exists()


@pytest.mark.django_db
def test_manager_grants_administrative_makeup(client, operations_context):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="admin-makeup-plan",
        name="Administrative makeup plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=4,
    )
    subscription = issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date.replace(day=1),
        valid_until=source_date.replace(day=28),
        actor=ctx["manager"],
    )
    allowance = subscription.allowances.get(category=SubscriptionCategory.ICE)
    client.force_login(ctx["manager"])

    response = client.post(
        reverse("subscriptions:manager_administrative_makeup_create"),
        {
            "source_subscription_allowance": str(allowance.id),
            "source_lesson": str(ctx["lesson"].id),
            "valid_from": source_date.isoformat(),
            "valid_until": (source_date + timedelta(days=14)).isoformat(),
            "target_lesson": "",
            "reason": "manager correction",
        },
    )

    assert response.status_code == 302
    assert MakeupEntitlement.objects.filter(
        source_subscription_allowance=allowance,
        source_lesson=ctx["lesson"],
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
    ).exists()
