from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.models import CoachProfile, Student
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from core.choices import SubscriptionCategory
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    cancel_absence_compensation_case,
    create_absence_compensation_case,
    issue_subscription,
)

User = get_user_model()


@pytest.fixture
def actor(db):
    return User.objects.create_user(
        username="comp-case-admin",
        password="test",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def context(db, actor):
    student = Student.objects.create(display_name="Case Student")
    coach = CoachProfile.objects.create(
        user=actor,
        display_name="Case Coach",
    )
    group = TrainingGroup.objects.create(
        code="case-group",
        name="Case Group",
    )
    venue = Venue.objects.create(code="case-rink", name="Case Rink")
    ice = LessonType.objects.create(
        code="case-ice",
        name="Case Ice",
        subscription_category=SubscriptionCategory.ICE,
    )
    return {
        "student": student,
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
    }


def make_absence(*, context, actor, starts_at=None) -> Attendance:
    starts_at = starts_at or datetime(
        2026,
        9,
        20,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = Lesson.objects.create(
        group=context["group"],
        lesson_type=context["ice"],
        coach=context["coach"],
        venue=context["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )
    return Attendance.objects.create(
        lesson=lesson,
        student=context["student"],
        status=Attendance.Status.ABSENT,
        marked_at=starts_at + timedelta(hours=1),
        marked_by=actor,
        updated_by=actor,
    )


def make_policy(
    *,
    reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
    code="case-policy",
    justification=AbsenceCompensationPolicy.JustificationRequirement.NONE,
):
    policy = AbsenceCompensationPolicy.objects.create(
        code=code,
        version=1,
        name="Case policy",
        absence_reason=reason,
        justification_requirement=justification,
        max_eligible_absences=4,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=date(2026, 1, 1),
    )
    AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
        priority=10,
    )
    return policy


@pytest.mark.django_db
def test_create_case_snapshots_policy_and_actions(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert case.policy == policy
    assert case.student == context["student"]
    assert case.source_lesson == attendance.lesson
    assert case.category == SubscriptionCategory.ICE
    assert case.source_date == date(2026, 9, 20)
    assert case.policy_code_snapshot == policy.code
    assert case.policy_version_snapshot == 1
    assert case.max_eligible_absences_snapshot == 4
    assert case.limit_scope_snapshot == (
        AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD
    )
    assert case.actions_snapshot == [
        {
            "action_id": str(policy.actions.get().id),
            "action_type": "free_makeup",
            "target_period_rule": "current_period",
            "requirement": "none",
            "validity_days": None,
            "priority": 10,
            "window_id": None,
            "window_name": None,
            "target_from": None,
            "target_until": None,
        }
    ]
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseCreated",
        aggregate_id=case.id,
    ).count() == 1


@pytest.mark.django_db
def test_create_case_is_idempotent_for_same_open_absence(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()

    first = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )
    second = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert second.id == first.id
    assert AbsenceCompensationCase.objects.filter(
        attendance=attendance,
        status=AbsenceCompensationCase.Status.OPEN,
    ).count() == 1
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseCreated",
        aggregate_id=first.id,
    ).count() == 1


@pytest.mark.django_db
def test_open_case_requires_cancel_before_reason_change(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy(
        reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        code="unexcused-case",
    )
    make_policy(
        reason=AbsenceCompensationPolicy.AbsenceReason.OTHER,
        code="other-case",
    )

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    with pytest.raises(
        ValidationError,
        match="different absence reason",
    ):
        create_absence_compensation_case(
            attendance_id=attendance.id,
            absence_reason=AbsenceCompensationPolicy.AbsenceReason.OTHER,
            actor=actor,
        )

    cancel_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        at=datetime(2026, 9, 21, 12, 0, tzinfo=dt_timezone.utc),
    )
    replacement = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.OTHER,
        actor=actor,
    )

    assert replacement.id != case.id
    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.CANCELLED
    assert replacement.status == AbsenceCompensationCase.Status.OPEN


@pytest.mark.django_db
def test_case_rejects_present_attendance(actor, context):
    attendance = make_absence(context=context, actor=actor)
    attendance.status = Attendance.Status.PRESENT
    attendance.save(update_fields=["status"])
    make_policy()

    with pytest.raises(
        ValidationError,
        match="Attendance=ABSENT",
    ):
        create_absence_compensation_case(
            attendance_id=attendance.id,
            absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
            actor=actor,
        )


@pytest.mark.django_db
def test_medical_case_requires_verified_justification(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy(
        reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        code="medical-case",
        justification=(
            AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
        ),
    )

    with pytest.raises(
        ValidationError,
        match="verified medical justification",
    ):
        create_absence_compensation_case(
            attendance_id=attendance.id,
            absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
            actor=actor,
        )

    justification = AbsenceJustification.objects.create(
        student=context["student"],
        lesson=attendance.lesson,
        status=AbsenceJustification.Status.VERIFIED,
        reviewed_at=datetime(
            2026,
            9,
            21,
            10,
            0,
            tzinfo=dt_timezone.utc,
        ),
        reviewed_by=actor,
        declared_by=actor,
    )

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        actor=actor,
    )
    assert case.source_justification == justification


@pytest.mark.django_db
def test_case_snapshots_source_allowance_when_available(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    plan = SubscriptionPlan.objects.create(
        code="case-subscription",
        name="Case subscription",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    subscription = issue_subscription(
        student_id=context["student"].id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert (
        case.source_subscription_allowance_id
        == subscription.allowances.get().id
    )


@pytest.mark.django_db
def test_cancel_case_is_idempotent(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )
    at = datetime(2026, 9, 21, 12, 0, tzinfo=dt_timezone.utc)

    first = cancel_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        at=at,
    )
    second = cancel_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        at=at + timedelta(hours=1),
    )

    assert first.cancelled_at == at
    assert second.cancelled_at == at
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseCancelled",
        aggregate_id=case.id,
    ).count() == 1
