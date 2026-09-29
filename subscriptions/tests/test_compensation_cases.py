from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from accounts.models import CoachProfile, Student
from attendance.models import AbsenceJustification, Attendance
from attendance.services import (
    revoke_medical_absence,
    set_attendance,
    verify_medical_absence,
)
from audit.models import AuditEvent
from core.choices import SubscriptionCategory
from scheduling.models import (
    Lesson,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)
from subscriptions.models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    AttendanceCoverage,
    MakeupEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    activate_paid_makeup_grant,
    adjust_allowance,
    authorize_paid_makeup_from_case,
    cancel_absence_compensation_case,
    cancel_subscription,
    confirm_paid_makeup_fee,
    create_absence_compensation_case,
    issue_subscription,
    materialize_free_makeup_from_case,
    reverse_absence_compensation_case,
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
    hall = LessonType.objects.create(
        code="case-hall",
        name="Case Hall",
        subscription_category=SubscriptionCategory.HALL,
    )
    return {
        "student": student,
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
        "hall": hall,
    }


def make_absence(
    *,
    context,
    actor,
    starts_at=None,
    lesson_type=None,
) -> Attendance:
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
        lesson_type=lesson_type or context["ice"],
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


@pytest.mark.django_db
def test_case_keeps_fully_consumed_historical_source_allowance(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    plan = SubscriptionPlan.objects.create(
        code="case-consumed-source",
        name="Case consumed source",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=1,
    )
    subscription = issue_subscription(
        student_id=context["student"].id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get()
    adjust_allowance(
        allowance_id=allowance.id,
        delta=-1,
        reason="Consume source for provenance test",
        actor=actor,
    )

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert case.source_subscription_allowance_id == allowance.id


def issue_ice_subscription_for_period(*, actor, context, code="limit-sub"):
    plan = SubscriptionPlan.objects.create(
        code=code,
        name=code,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    return issue_subscription(
        student_id=context["student"].id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )


@pytest.mark.django_db
def test_limit_marks_first_four_eligible_and_fifth_exceeded(actor, context):
    make_policy()
    issue_ice_subscription_for_period(actor=actor, context=context)

    cases = []
    for day in range(1, 6):
        attendance = make_absence(
            context=context,
            actor=actor,
            starts_at=datetime(
                2026,
                9,
                day,
                15,
                0,
                tzinfo=dt_timezone.utc,
            ),
        )
        cases.append(
            create_absence_compensation_case(
                attendance_id=attendance.id,
                absence_reason=(
                    AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED
                ),
                actor=actor,
            )
        )

    for case in cases:
        case.refresh_from_db()

    assert [case.eligible_absence_ordinal for case in cases] == [1, 2, 3, 4, 5]
    assert [case.eligibility_status for case in cases[:4]] == [
        AbsenceCompensationCase.EligibilityStatus.ELIGIBLE,
    ] * 4
    assert (
        cases[4].eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.LIMIT_EXCEEDED
    )
    assert cases[4].eligibility_period_from == date(2026, 9, 1)
    assert cases[4].eligibility_period_until == date(2026, 9, 30)


@pytest.mark.django_db
def test_cancelling_earlier_case_releases_limit_slot(actor, context):
    make_policy()
    issue_ice_subscription_for_period(actor=actor, context=context)

    cases = []
    for day in range(1, 6):
        attendance = make_absence(
            context=context,
            actor=actor,
            starts_at=datetime(
                2026,
                9,
                day,
                15,
                0,
                tzinfo=dt_timezone.utc,
            ),
        )
        cases.append(
            create_absence_compensation_case(
                attendance_id=attendance.id,
                absence_reason=(
                    AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED
                ),
                actor=actor,
            )
        )

    cancel_absence_compensation_case(
        case_id=cases[0].id,
        actor=actor,
        at=datetime(2026, 9, 20, 12, 0, tzinfo=dt_timezone.utc),
    )

    cases[4].refresh_from_db()
    assert cases[4].eligible_absence_ordinal == 4
    assert (
        cases[4].eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )


@pytest.mark.django_db
def test_limited_case_without_source_period_is_undetermined(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert (
        case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.UNDETERMINED
    )
    assert case.eligible_absence_ordinal is None
    assert case.eligibility_period_from is None
    assert case.eligibility_period_until is None


@pytest.mark.django_db
def test_unlimited_policy_is_eligible_without_source_period(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    policy.max_eligible_absences = None
    policy.save(update_fields=["max_eligible_absences"])

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    assert (
        case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )
    assert case.eligible_absence_ordinal is None
    assert case.eligibility_period_from is None
    assert case.eligibility_period_until is None


@pytest.mark.django_db
def test_eligibility_evaluation_is_audited(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="audit-limit-sub",
    )

    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    event = AuditEvent.objects.get(
        event_type="AbsenceCompensationEvaluated",
        aggregate_id=case.id,
    )
    assert event.payload["eligibility_status"] == "eligible"
    assert event.payload["eligible_absence_ordinal"] == 1
    assert event.payload["max_eligible_absences"] == 4
    assert event.payload["period_from"] == "2026-09-01"
    assert event.payload["period_until"] == "2026-09-30"


@pytest.mark.django_db
def test_category_period_limit_counts_ice_and_hall_separately(actor, context):
    policy = make_policy()
    policy.max_eligible_absences = 1
    policy.limit_scope = (
        AbsenceCompensationPolicy.LimitScope.CATEGORY_PERIOD
    )
    policy.save(
        update_fields=["max_eligible_absences", "limit_scope"]
    )

    plan = SubscriptionPlan.objects.create(
        code="category-limit-sub",
        name="Category limit subscription",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=4,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.HALL,
        visit_limit=4,
    )
    issue_subscription(
        student_id=context["student"].id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )

    ice_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(
            2026,
            9,
            10,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        lesson_type=context["ice"],
    )
    hall_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(
            2026,
            9,
            11,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        lesson_type=context["hall"],
    )

    ice_case = create_absence_compensation_case(
        attendance_id=ice_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )
    hall_case = create_absence_compensation_case(
        attendance_id=hall_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
    )

    ice_case.refresh_from_db()
    hall_case.refresh_from_db()
    assert ice_case.eligible_absence_ordinal == 1
    assert hall_case.eligible_absence_ordinal == 1
    assert (
        ice_case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )
    assert (
        hall_case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )


@pytest.mark.django_db
def test_attendance_correction_to_present_cancels_open_case(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    LessonRosterEntry.objects.create(
        lesson=attendance.lesson,
        student=context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=actor,
    )

    set_attendance(
        lesson_id=attendance.lesson_id,
        student_id=context["student"].id,
        status=Attendance.Status.PRESENT,
        actor=actor,
        now=attendance.lesson.starts_at + timedelta(hours=2),
    )

    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.CANCELLED
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseCancelled",
        aggregate_id=case.id,
        payload__reason="attendance_corrected_to_present",
    ).exists()


@pytest.mark.django_db
def test_medical_justification_revocation_cancels_open_case(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy(
        reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        code="medical-revocation-policy",
        justification=(
            AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
        ),
    )
    justification = AbsenceJustification.objects.create(
        student=context["student"],
        lesson=attendance.lesson,
        status=AbsenceJustification.Status.VERIFIED,
        reviewed_at=attendance.marked_at,
        reviewed_by=actor,
        declared_by=actor,
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        actor=actor,
        source_justification_id=justification.id,
        now=attendance.marked_at,
    )

    revoke_medical_absence(
        justification_id=justification.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.CANCELLED
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseCancelled",
        aggregate_id=case.id,
        payload__reason="medical_justification_revoked",
    ).exists()


@pytest.mark.django_db
def test_student_period_limit_uses_source_subscription_identity(actor, context):
    policy = make_policy()
    policy.max_eligible_absences = 1
    policy.limit_scope = (
        AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD
    )
    policy.save(
        update_fields=["max_eligible_absences", "limit_scope"]
    )

    ice_plan = SubscriptionPlan.objects.create(
        code="identity-ice",
        name="Identity ICE",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=ice_plan,
        category=SubscriptionCategory.ICE,
        visit_limit=4,
    )
    issue_subscription(
        student_id=context["student"].id,
        plan_id=ice_plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )

    hall_plan = SubscriptionPlan.objects.create(
        code="identity-hall",
        name="Identity HALL",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=hall_plan,
        category=SubscriptionCategory.HALL,
        visit_limit=4,
    )
    issue_subscription(
        student_id=context["student"].id,
        plan_id=hall_plan.id,
        valid_from=date(2026, 9, 15),
        valid_until=date(2026, 10, 12),
        actor=actor,
    )

    ice_absence = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(
            2026,
            9,
            20,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        lesson_type=context["ice"],
    )
    hall_absence = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(
            2026,
            9,
            25,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        lesson_type=context["hall"],
    )

    ice_case = create_absence_compensation_case(
        attendance_id=ice_absence.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=ice_absence.marked_at,
    )
    hall_case = create_absence_compensation_case(
        attendance_id=hall_absence.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=hall_absence.marked_at,
    )

    ice_case.refresh_from_db()
    hall_case.refresh_from_db()
    assert ice_case.eligible_absence_ordinal == 1
    assert hall_case.eligible_absence_ordinal == 1
    assert (
        ice_case.source_subscription_allowance.subscription_id
        != hall_case.source_subscription_allowance.subscription_id
    )
    assert (
        ice_case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )
    assert (
        hall_case.eligibility_status
        == AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )


@pytest.mark.django_db
def test_referenced_policy_and_actions_are_immutable(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    assert case.policy_id == policy.id

    policy.max_eligible_absences = 7
    with pytest.raises(
        ValidationError,
        match="policy versions are immutable",
    ):
        policy.save(update_fields=["max_eligible_absences"])

    action = policy.actions.get()
    action.priority = 99
    with pytest.raises(
        ValidationError,
        match="referenced compensation policies are immutable",
    ):
        action.save(update_fields=["priority"])


@pytest.mark.django_db
def test_materialize_free_makeup_freezes_case_and_creates_grant(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialize-free",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    assert case.eligibility_status == (
        AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    )

    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    case.refresh_from_db()
    grant.refresh_from_db()
    entitlement = grant.makeup_entitlement
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert case.materialized_at == attendance.marked_at + timedelta(hours=1)
    assert grant.action_type == "free_makeup"
    assert entitlement is not None
    assert entitlement.reason == "absence_compensation"
    assert entitlement.student_id == context["student"].id
    assert entitlement.source_lesson_id == attendance.lesson_id
    assert (
        entitlement.source_subscription_allowance.subscription_id
        == subscription.id
    )
    assert entitlement.valid_from == attendance.lesson.starts_at.date()
    assert entitlement.valid_until == date(2026, 9, 30)
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationMaterialized",
        aggregate_id=case.id,
    ).count() == 1


@pytest.mark.django_db
def test_materialize_free_makeup_is_idempotent(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialize-idempotent",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )

    first = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )
    second = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=2),
    )

    assert second.id == first.id
    assert AbsenceCompensationActionGrant.objects.filter(case=case).count() == 1
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationMaterialized",
        aggregate_id=case.id,
    ).count() == 1


@pytest.mark.django_db
def test_materialize_rejects_limit_exceeded_case(actor, context):
    policy = make_policy()
    policy.max_eligible_absences = 1
    policy.save(update_fields=["max_eligible_absences"])
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialize-limit",
    )

    first_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(2026, 9, 10, 15, 0, tzinfo=dt_timezone.utc),
    )
    second_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(2026, 9, 11, 15, 0, tzinfo=dt_timezone.utc),
    )
    create_absence_compensation_case(
        attendance_id=first_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=first_attendance.marked_at,
    )
    second = create_absence_compensation_case(
        attendance_id=second_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=second_attendance.marked_at,
    )
    assert second.eligibility_status == (
        AbsenceCompensationCase.EligibilityStatus.LIMIT_EXCEEDED
    )

    with pytest.raises(ValidationError, match="must be ELIGIBLE"):
        materialize_free_makeup_from_case(
            case_id=second.id,
            actor=actor,
            now=second_attendance.marked_at + timedelta(hours=1),
        )


@pytest.mark.django_db
def test_backdated_case_does_not_revoke_materialized_right(actor, context):
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialize-backdated",
    )

    cases = []
    for day in (10, 14, 17, 24):
        attendance = make_absence(
            context=context,
            actor=actor,
            starts_at=datetime(
                2026,
                9,
                day,
                15,
                0,
                tzinfo=dt_timezone.utc,
            ),
        )
        cases.append(
            create_absence_compensation_case(
                attendance_id=attendance.id,
                absence_reason=(
                    AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED
                ),
                actor=actor,
                now=attendance.marked_at,
            )
        )

    materialized = cases[-1]
    original_ordinal = materialized.eligible_absence_ordinal
    grant = materialize_free_makeup_from_case(
        case_id=materialized.id,
        actor=actor,
        now=datetime(
            2026,
            9,
            25,
            12,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )

    early_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(
            2026,
            9,
            3,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    early_case = create_absence_compensation_case(
        attendance_id=early_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=datetime(
            2026,
            9,
            26,
            12,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )

    materialized.refresh_from_db()
    early_case.refresh_from_db()
    assert materialized.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert materialized.eligible_absence_ordinal == original_ordinal
    assert grant.makeup_entitlement_id is not None
    assert (
        AbsenceCompensationCase.objects.filter(
            student=context["student"],
            status=AbsenceCompensationCase.Status.OPEN,
            eligibility_status=(
                AbsenceCompensationCase.EligibilityStatus.LIMIT_EXCEEDED
            ),
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_materialized_case_keeps_limit_slot(actor, context):
    policy = make_policy()
    policy.max_eligible_absences = 1
    policy.save(update_fields=["max_eligible_absences"])
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialized-slot",
    )

    first_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(2026, 9, 10, 15, 0, tzinfo=dt_timezone.utc),
    )
    first = create_absence_compensation_case(
        attendance_id=first_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=first_attendance.marked_at,
    )
    materialize_free_makeup_from_case(
        case_id=first.id,
        actor=actor,
        now=first_attendance.marked_at + timedelta(hours=1),
    )

    second_attendance = make_absence(
        context=context,
        actor=actor,
        starts_at=datetime(2026, 9, 11, 15, 0, tzinfo=dt_timezone.utc),
    )
    second = create_absence_compensation_case(
        attendance_id=second_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=second_attendance.marked_at,
    )

    assert second.eligible_absence_ordinal == 2
    assert second.eligibility_status == (
        AbsenceCompensationCase.EligibilityStatus.LIMIT_EXCEEDED
    )


@pytest.mark.django_db
def test_materialized_case_requires_explicit_reversal_before_cancel(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialized-cancel-guard",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="cannot be cancelled directly"):
        cancel_absence_compensation_case(
            case_id=case.id,
            actor=actor,
            at=attendance.marked_at + timedelta(hours=2),
        )

    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert case.cancelled_at is None


@pytest.mark.django_db
def test_create_case_returns_existing_materialized_case(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="materialized-create-idempotent",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    repeated = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=2),
    )

    assert repeated.id == case.id
    repeated.refresh_from_db()
    assert repeated.status == AbsenceCompensationCase.Status.MATERIALIZED


@pytest.mark.django_db
def test_present_correction_reverses_unused_materialized_compensation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="reverse-on-present",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )
    LessonRosterEntry.objects.create(
        lesson=attendance.lesson,
        student=context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=actor,
    )

    set_attendance(
        lesson_id=attendance.lesson_id,
        student_id=context["student"].id,
        status=Attendance.Status.PRESENT,
        actor=actor,
        now=attendance.lesson.starts_at + timedelta(hours=2),
    )

    case.refresh_from_db()
    grant.refresh_from_db()
    entitlement = MakeupEntitlement.objects.get(pk=grant.makeup_entitlement_id)
    attendance.refresh_from_db()
    assert attendance.status == Attendance.Status.PRESENT
    assert case.status == AbsenceCompensationCase.Status.REVERSED
    assert case.reversal_reason == "attendance_corrected_to_present"
    assert grant.reversed_at is not None
    assert grant.reversal_reason == "attendance_corrected_to_present"
    assert entitlement.cancelled_at is not None


@pytest.mark.django_db
def test_present_correction_rejects_used_materialized_compensation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="used-materialized-compensation",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )
    entitlement = grant.makeup_entitlement

    target_starts = datetime(2026, 9, 22, 15, 0, tzinfo=dt_timezone.utc)
    target_lesson = Lesson.objects.create(
        group=context["group"],
        lesson_type=context["ice"],
        coach=context["coach"],
        venue=context["venue"],
        starts_at=target_starts,
        ends_at=target_starts + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=target_starts - timedelta(hours=2),
        decision_deadline=target_starts - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )
    target_attendance = Attendance.objects.create(
        lesson=target_lesson,
        student=context["student"],
        status=Attendance.Status.PRESENT,
        marked_at=target_starts + timedelta(hours=1),
        marked_by=actor,
        updated_by=actor,
    )
    AttendanceCoverage.objects.create(
        attendance=target_attendance,
        subscription_allowance=subscription.allowances.get(),
        makeup_entitlement=entitlement,
        created_by=actor,
    )
    LessonRosterEntry.objects.create(
        lesson=attendance.lesson,
        student=context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=actor,
    )

    with pytest.raises(
        ValidationError,
        match="compensation make-up is already used",
    ):
        set_attendance(
            lesson_id=attendance.lesson_id,
            student_id=context["student"].id,
            status=Attendance.Status.PRESENT,
            actor=actor,
            now=attendance.lesson.starts_at + timedelta(hours=2),
        )

    attendance.refresh_from_db()
    case.refresh_from_db()
    entitlement.refresh_from_db()
    assert attendance.status == Attendance.Status.ABSENT
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert entitlement.cancelled_at is None


@pytest.mark.django_db
def test_medical_absence_cannot_receive_second_compensation_makeup(actor, context):
    attendance = make_absence(context=context, actor=actor)
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="medical-double-compensation",
    )
    make_policy(
        reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        code="medical-double-policy",
        justification=(
            AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
        ),
    )
    justification = AbsenceJustification.objects.create(
        student=context["student"],
        lesson=attendance.lesson,
        status=AbsenceJustification.Status.VERIFIED,
        reviewed_at=attendance.marked_at,
        reviewed_by=actor,
        declared_by=actor,
    )
    MakeupEntitlement.objects.create(
        student=context["student"],
        source_lesson=attendance.lesson,
        source_subscription_allowance=subscription.allowances.get(),
        source_justification=justification,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
        created_by=actor,
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        actor=actor,
        source_justification_id=justification.id,
        now=attendance.marked_at,
    )

    with pytest.raises(
        ValidationError,
        match="already has an active compensation make-up",
    ):
        materialize_free_makeup_from_case(
            case_id=case.id,
            actor=actor,
            now=attendance.marked_at + timedelta(hours=1),
        )

    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.OPEN
    assert MakeupEntitlement.objects.filter(
        student=context["student"],
        source_lesson=attendance.lesson,
        cancelled_at__isnull=True,
    ).count() == 1


@pytest.mark.django_db
def test_medical_verification_supersedes_unused_materialized_compensation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="medical-supersedes-compensation",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )
    old_entitlement = grant.makeup_entitlement
    justification = AbsenceJustification.objects.create(
        student=context["student"],
        lesson=attendance.lesson,
        status=AbsenceJustification.Status.PENDING,
        declared_by=actor,
    )

    verified = verify_medical_absence(
        justification_id=justification.id,
        actor=actor,
        valid_until=date(2026, 10, 31),
        now=attendance.marked_at + timedelta(days=1),
    )

    case.refresh_from_db()
    grant.refresh_from_db()
    old_entitlement.refresh_from_db()
    medical = MakeupEntitlement.objects.get(
        source_justification=verified,
        reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
    )
    assert case.status == AbsenceCompensationCase.Status.REVERSED
    assert case.reversal_reason == "superseded_by_medical"
    assert grant.reversed_at is not None
    assert old_entitlement.cancelled_at is not None
    assert medical.cancelled_at is None
    assert medical.source_subscription_allowance.subscription_id == subscription.id


@pytest.mark.django_db
def test_medical_verification_rejects_used_materialized_compensation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="medical-used-compensation",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )
    entitlement = grant.makeup_entitlement

    target_starts = datetime(2026, 9, 22, 15, 0, tzinfo=dt_timezone.utc)
    target_lesson = Lesson.objects.create(
        group=context["group"],
        lesson_type=context["ice"],
        coach=context["coach"],
        venue=context["venue"],
        starts_at=target_starts,
        ends_at=target_starts + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=target_starts - timedelta(hours=2),
        decision_deadline=target_starts - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )
    target_attendance = Attendance.objects.create(
        lesson=target_lesson,
        student=context["student"],
        status=Attendance.Status.PRESENT,
        marked_at=target_starts + timedelta(hours=1),
        marked_by=actor,
        updated_by=actor,
    )
    AttendanceCoverage.objects.create(
        attendance=target_attendance,
        subscription_allowance=subscription.allowances.get(),
        makeup_entitlement=entitlement,
        created_by=actor,
    )
    justification = AbsenceJustification.objects.create(
        student=context["student"],
        lesson=attendance.lesson,
        status=AbsenceJustification.Status.PENDING,
        declared_by=actor,
    )

    with pytest.raises(
        ValidationError,
        match="compensation make-up is already used",
    ):
        verify_medical_absence(
            justification_id=justification.id,
            actor=actor,
            valid_until=date(2026, 10, 31),
            now=attendance.marked_at + timedelta(days=1),
        )

    justification.refresh_from_db()
    case.refresh_from_db()
    entitlement.refresh_from_db()
    assert justification.status == AbsenceJustification.Status.PENDING
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert entitlement.cancelled_at is None


@pytest.mark.django_db
def test_database_prevents_two_active_absence_makeups_for_same_source(actor, context):
    attendance = make_absence(context=context, actor=actor)
    subscription = issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="db-absence-makeup-unique",
    )
    allowance = subscription.allowances.get()
    MakeupEntitlement.objects.create(
        student=context["student"],
        source_lesson=attendance.lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
        source_justification=AbsenceJustification.objects.create(
            student=context["student"],
            lesson=attendance.lesson,
            status=AbsenceJustification.Status.VERIFIED,
            reviewed_at=attendance.marked_at,
            reviewed_by=actor,
            declared_by=actor,
        ),
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
        created_by=actor,
    )

    with pytest.raises(IntegrityError):
        MakeupEntitlement.objects.create(
            student=context["student"],
            source_lesson=attendance.lesson,
            source_subscription_allowance=allowance,
            category=SubscriptionCategory.ICE,
            reason=MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
            valid_from=date(2026, 10, 1),
            valid_until=date(2026, 10, 31),
            created_by=actor,
        )


@pytest.mark.django_db
def test_admin_can_reverse_unused_materialized_compensation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    make_policy()
    issue_ice_subscription_for_period(
        actor=actor,
        context=context,
        code="manual-compensation-reversal",
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = materialize_free_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    reversed_case = reverse_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        reason="issued_by_mistake",
        now=attendance.marked_at + timedelta(hours=2),
    )
    repeated = reverse_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        reason="ignored_on_retry",
        now=attendance.marked_at + timedelta(hours=3),
    )

    grant.refresh_from_db()
    entitlement = MakeupEntitlement.objects.get(pk=grant.makeup_entitlement_id)
    assert reversed_case.status == AbsenceCompensationCase.Status.REVERSED
    assert repeated.id == case.id
    assert repeated.reversal_reason == "issued_by_mistake"
    assert grant.reversal_reason == "issued_by_mistake"
    assert entitlement.cancelled_at is not None
    assert AuditEvent.objects.filter(
        event_type="AbsenceCompensationCaseReversed",
        aggregate_id=case.id,
    ).count() == 1


def add_paid_action(
    policy,
    *,
    target_rule=(
        AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
    ),
    requirement=(
        AbsenceCompensationPolicyAction.Requirement.FEE_AND_TARGET_SUBSCRIPTION_REQUIRED
    ),
):
    return AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        target_period_rule=target_rule,
        requirement=requirement,
        priority=20,
    )


def issue_ice_subscription_range(
    *,
    actor,
    context,
    code,
    valid_from,
    valid_until,
):
    plan = SubscriptionPlan.objects.create(
        code=code,
        name=code,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    return issue_subscription(
        student_id=context["student"].id,
        plan_id=plan.id,
        valid_from=valid_from,
        valid_until=valid_until,
        actor=actor,
    )


@pytest.mark.django_db
def test_authorize_paid_makeup_freezes_case_without_entitlement(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(policy)
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )

    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    case.refresh_from_db()
    grant.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert grant.action_type == (
        AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
    )
    assert grant.makeup_entitlement_id is None
    assert grant.activated_at is None
    assert grant.fee_confirmed_at is None
    assert grant.target_subscription_id is None
    assert AuditEvent.objects.filter(
        event_type="PaidFreezeAuthorized",
        aggregate_id=grant.id,
    ).count() == 1


@pytest.mark.django_db
def test_paid_makeup_requires_fee_and_target_subscription_before_activation(
    actor,
    context,
):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(policy)
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-requirements-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="fee has not been confirmed"):
        activate_paid_makeup_grant(
            grant_id=grant.id,
            actor=actor,
            now=attendance.marked_at + timedelta(hours=2),
        )

    first_confirmation = confirm_paid_makeup_fee(
        grant_id=grant.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=2),
    )
    second_confirmation = confirm_paid_makeup_fee(
        grant_id=grant.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=3),
    )
    assert second_confirmation.fee_confirmed_at == first_confirmation.fee_confirmed_at

    with pytest.raises(ValidationError, match="Target subscription is required"):
        activate_paid_makeup_grant(
            grant_id=grant.id,
            actor=actor,
            now=attendance.marked_at + timedelta(hours=3),
        )

    target = issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-requirements-target",
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
    )
    activated = activate_paid_makeup_grant(
        grant_id=grant.id,
        actor=actor,
        target_subscription_id=target.id,
        now=attendance.marked_at + timedelta(hours=4),
    )
    repeated = activate_paid_makeup_grant(
        grant_id=grant.id,
        actor=actor,
        target_subscription_id=target.id,
        now=attendance.marked_at + timedelta(hours=5),
    )

    activated.refresh_from_db()
    entitlement = activated.makeup_entitlement
    assert repeated.id == activated.id
    assert activated.target_subscription_id == target.id
    assert activated.activated_at is not None
    assert entitlement is not None
    assert entitlement.valid_from == date(2026, 10, 1)
    assert entitlement.valid_until == date(2026, 10, 31)
    assert entitlement.reason == MakeupEntitlement.Reason.ABSENCE_COMPENSATION
    assert AuditEvent.objects.filter(
        event_type="PaidFreezeActivated",
        aggregate_id=grant.id,
    ).count() == 1


@pytest.mark.django_db
def test_paid_makeup_explicit_window_can_require_fee_without_target_subscription(
    actor,
    context,
):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    action = add_paid_action(
        policy,
        target_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED,
    )
    AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="September to October",
        source_from=date(2026, 9, 1),
        source_until=date(2026, 9, 30),
        target_from=date(2026, 10, 1),
        target_until=date(2026, 10, 31),
    )
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-window-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )

    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        fee_confirmed=True,
        now=attendance.marked_at + timedelta(hours=1),
    )
    activated = activate_paid_makeup_grant(
        grant_id=grant.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=2),
    )

    entitlement = activated.makeup_entitlement
    assert activated.fee_confirmed_at is not None
    assert activated.target_subscription_id is None
    assert entitlement.valid_from == date(2026, 10, 1)
    assert entitlement.valid_until == date(2026, 10, 31)


@pytest.mark.django_db
def test_paid_makeup_explicit_window_intersects_required_target_subscription(
    actor,
    context,
):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    action = add_paid_action(
        policy,
        target_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
        ),
    )
    AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="June to August",
        source_from=date(2026, 9, 1),
        source_until=date(2026, 9, 30),
        target_from=date(2026, 10, 1),
        target_until=date(2026, 10, 31),
    )
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-window-target-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    target = issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-window-target",
        valid_from=date(2026, 10, 10),
        valid_until=date(2026, 11, 6),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        fee_confirmed=True,
        target_subscription_id=target.id,
        now=attendance.marked_at + timedelta(hours=1),
    )

    activated = activate_paid_makeup_grant(
        grant_id=grant.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=2),
    )

    entitlement = activated.makeup_entitlement
    assert entitlement.valid_from == date(2026, 10, 10)
    assert entitlement.valid_until == date(2026, 10, 31)


@pytest.mark.django_db
def test_pending_paid_makeup_can_be_reversed_before_activation(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(policy)
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-pending-reverse",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        now=attendance.marked_at + timedelta(hours=1),
    )

    reversed_case = reverse_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        reason="paid_freeze_cancelled",
        now=attendance.marked_at + timedelta(hours=2),
    )

    grant.refresh_from_db()
    assert reversed_case.status == AbsenceCompensationCase.Status.REVERSED
    assert grant.reversed_at is not None
    assert grant.makeup_entitlement_id is None
    assert grant.activated_at is None
    assert AuditEvent.objects.filter(
        event_type="PaidFreezeCancelled",
        aggregate_id=grant.id,
    ).count() == 1


@pytest.mark.django_db
def test_paid_makeup_rejects_non_fee_policy_requirement(actor, context):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(
        policy,
        target_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
    )
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-invalid-requirement",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )

    with pytest.raises(
        ValidationError,
        match="PAID_MAKEUP requires FEE_REQUIRED",
    ):
        authorize_paid_makeup_from_case(
            case_id=case.id,
            actor=actor,
            now=attendance.marked_at + timedelta(hours=1),
        )


@pytest.mark.django_db
def test_paid_makeup_next_period_rejects_overlapping_target_subscription(
    actor,
    context,
):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(policy)
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-overlap-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    target = issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-overlap-target",
        valid_from=date(2026, 9, 20),
        valid_until=date(2026, 10, 17),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    grant = authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        fee_confirmed=True,
        target_subscription_id=target.id,
        now=attendance.marked_at + timedelta(hours=1),
    )

    with pytest.raises(
        ValidationError,
        match="must start after the source subscription ends",
    ):
        activate_paid_makeup_grant(
            grant_id=grant.id,
            actor=actor,
            now=attendance.marked_at + timedelta(hours=2),
        )

    grant.refresh_from_db()
    assert grant.activated_at is None
    assert grant.makeup_entitlement_id is None


@pytest.mark.django_db
def test_target_subscription_cannot_be_cancelled_while_paid_grant_active(
    actor,
    context,
):
    attendance = make_absence(context=context, actor=actor)
    policy = make_policy()
    add_paid_action(policy)
    issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-cancel-source",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )
    target = issue_ice_subscription_range(
        actor=actor,
        context=context,
        code="paid-cancel-target",
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
    )
    case = create_absence_compensation_case(
        attendance_id=attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=actor,
        now=attendance.marked_at,
    )
    authorize_paid_makeup_from_case(
        case_id=case.id,
        actor=actor,
        target_subscription_id=target.id,
        now=attendance.marked_at + timedelta(hours=1),
    )

    with pytest.raises(
        ValidationError,
        match="required by an active PAID_MAKEUP grant",
    ):
        cancel_subscription(
            subscription_id=target.id,
            actor=actor,
            at=attendance.marked_at + timedelta(hours=2),
        )

    reverse_absence_compensation_case(
        case_id=case.id,
        actor=actor,
        reason="paid_freeze_cancelled",
        now=attendance.marked_at + timedelta(hours=3),
    )
    cancelled = cancel_subscription(
        subscription_id=target.id,
        actor=actor,
        at=attendance.marked_at + timedelta(hours=4),
    )

    assert cancelled.cancelled_at is not None
