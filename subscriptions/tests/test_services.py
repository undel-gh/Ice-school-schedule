from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as dt_timezone
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.selectors import allowance_balance, subscription_balances
from subscriptions.services import (
    adjust_allowance,
    assign_attendance_coverage,
    cancel_subscription,
    issue_subscription,
    reverse_attendance_coverage,
)

User = get_user_model()


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="admin", password="test")


@pytest.fixture
def student(db):
    return Student.objects.create(display_name="Маша")


@pytest.fixture
def school_context(db, actor):
    coach = CoachProfile.objects.create(user=actor, display_name="Coach")
    group = TrainingGroup.objects.create(code="group-a", name="Group A")
    venue = Venue.objects.create(code="rink", name="Rink")
    ice = LessonType.objects.create(
        code="ice",
        name="Ice",
        subscription_category=SubscriptionCategory.ICE,
    )
    hall = LessonType.objects.create(
        code="hall",
        name="Hall",
        subscription_category=SubscriptionCategory.HALL,
    )
    return {
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
        "hall": hall,
    }


def make_lesson(*, school_context, lesson_type, starts_at: datetime) -> Lesson:
    return Lesson.objects.create(
        group=school_context["group"],
        lesson_type=lesson_type,
        coach=school_context["coach"],
        venue=school_context["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )


def make_plan(
    *,
    code: str,
    ice: int | None = None,
    hall: int | None = None,
) -> SubscriptionPlan:
    plan = SubscriptionPlan.objects.create(code=code, name=code.upper())
    if ice is not None:
        SubscriptionPlanAllowance.objects.create(
            plan=plan,
            category=SubscriptionCategory.ICE,
            visit_limit=ice,
        )
    if hall is not None:
        SubscriptionPlanAllowance.objects.create(
            plan=plan,
            category=SubscriptionCategory.HALL,
            visit_limit=hall,
        )
    return plan


def make_present_attendance(
    *,
    lesson: Lesson,
    student: Student,
    actor,
) -> Attendance:
    return Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )


@pytest.mark.django_db
def test_issue_subscription_snapshots_allowances_and_grants(
    student,
    actor,
):
    plan = make_plan(code="8-ice-12-hall", ice=8, hall=12)

    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )

    assert subscription_balances(subscription.id) == {
        SubscriptionCategory.HALL: 12,
        SubscriptionCategory.ICE: 8,
    }
    assert SubscriptionLedgerEntry.objects.filter(
        allowance__subscription=subscription,
        entry_type=SubscriptionLedgerEntry.EntryType.GRANT,
    ).count() == 2

    source = plan.allowances.get(category=SubscriptionCategory.ICE)
    source.visit_limit = 99
    source.save(update_fields=["visit_limit"])

    ice_allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE
    )
    assert ice_allowance.visit_limit_snapshot == 8


@pytest.mark.django_db
def test_issue_subscription_rejects_plan_without_allowances(
    student,
    actor,
):
    plan = SubscriptionPlan.objects.create(code="empty", name="Empty")

    with pytest.raises(ValidationError):
        issue_subscription(
            student_id=student.id,
            plan_id=plan.id,
            valid_from=date(2026, 9, 1),
            valid_until=date(2026, 9, 30),
            actor=actor,
        )

    assert student.subscriptions.count() == 0


@pytest.mark.django_db
def test_one_time_entitlement_has_priority_over_monthly_allowance(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="8-ice", ice=8)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE
    )
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )
    entitlement = OneTimeEntitlement.objects.create(
        student=student,
        lesson=lesson,
        entitlement_type=OneTimeEntitlement.Type.TRIAL_ICE,
        category=SubscriptionCategory.ICE,
        created_by=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )
    repeated = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    assert coverage is not None
    assert repeated.id == coverage.id
    assert coverage.one_time_entitlement_id == entitlement.id
    assert coverage.subscription_allowance_id is None
    assert allowance_balance(allowance.id) == 8


@pytest.mark.django_db
def test_mixed_subscription_consumes_only_matching_category(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="mixed-one-one", ice=1, hall=1)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    ice_allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE
    )
    hall_allowance = subscription.allowances.get(
        category=SubscriptionCategory.HALL
    )
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["hall"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    assert coverage is not None
    assert coverage.subscription_allowance_id == hall_allowance.id
    assert allowance_balance(hall_allowance.id) == 0
    assert allowance_balance(ice_allowance.id) == 1


@pytest.mark.django_db
def test_target_specific_makeup_precedes_generic_makeup(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="ice-source", ice=2)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE
    )
    source_lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            20,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    target_lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            10,
            5,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=target_lesson,
        student=student,
        actor=actor,
    )

    generic = MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 10),
        created_by=actor,
    )
    targeted = MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.SCHOOL_RESCHEDULE,
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 15),
        target_lesson=target_lesson,
        created_by=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    assert coverage is not None
    assert coverage.makeup_entitlement_id == targeted.id
    assert coverage.makeup_entitlement_id != generic.id
    assert allowance_balance(allowance.id) == 1


@pytest.mark.django_db
def test_ordinary_allowance_uses_earliest_expiry(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="one-ice", ice=1)
    first = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 20),
        actor=actor,
    )
    second = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    assert coverage is not None
    assert coverage.subscription_allowance.subscription_id == first.id
    assert allowance_balance(first.allowances.get().id) == 0
    assert allowance_balance(second.allowances.get().id) == 1


@pytest.mark.django_db
def test_uncovered_present_is_preserved(
    student,
    actor,
    school_context,
):
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    assert coverage is None
    attendance.refresh_from_db()
    assert attendance.status == Attendance.Status.PRESENT


@pytest.mark.django_db
def test_reverse_restores_same_allowance_and_is_idempotent(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="reverse-ice", ice=1)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get()
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
    )
    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
    )

    assert allowance_balance(allowance.id) == 1
    assert SubscriptionLedgerEntry.objects.filter(
        coverage=coverage,
        entry_type=SubscriptionLedgerEntry.EntryType.RESTORE,
    ).count() == 1


@pytest.mark.django_db
def test_adjustment_cannot_make_balance_negative(
    student,
    actor,
):
    plan = make_plan(code="adjust-ice", ice=2)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get()

    with pytest.raises(ValidationError):
        adjust_allowance(
            allowance_id=allowance.id,
            delta=-3,
            reason="bad",
            actor=actor,
        )

    adjust_allowance(
        allowance_id=allowance.id,
        delta=-1,
        reason="Correction",
        actor=actor,
    )
    assert allowance_balance(allowance.id) == 1


@pytest.mark.django_db
def test_cancelled_subscription_is_not_used(
    student,
    actor,
    school_context,
):
    plan = make_plan(code="cancel-ice", ice=1)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    cancel_subscription(
        subscription_id=subscription.id,
        actor=actor,
    )
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = make_present_attendance(
        lesson=lesson,
        student=student,
        actor=actor,
    )

    assert assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    ) is None


@pytest.mark.django_db
def test_absent_attendance_cannot_receive_coverage(
    student,
    actor,
    school_context,
):
    lesson = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_by=actor,
    )

    with pytest.raises(ValidationError):
        assign_attendance_coverage(
            attendance_id=attendance.id,
            actor=actor,
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_visit_is_consumed_at_most_once(
    student,
    actor,
    school_context,
):
    if connection.vendor != "postgresql":
        pytest.skip(
            "Row-locking concurrency test requires PostgreSQL."
        )

    plan = make_plan(code="last-ice", ice=1)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    allowance = subscription.allowances.get()

    lesson_a = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    lesson_b = make_lesson(
        school_context=school_context,
        lesson_type=school_context["ice"],
        starts_at=datetime(
            2026,
            9,
            16,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance_a = make_present_attendance(
        lesson=lesson_a,
        student=student,
        actor=actor,
    )
    attendance_b = make_present_attendance(
        lesson=lesson_b,
        student=student,
        actor=actor,
    )

    barrier = threading.Barrier(2)

    def worker(attendance_id):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            result = assign_attendance_coverage(
                attendance_id=attendance_id,
                actor=None,
            )
            return result.id if result else None
        finally:
            # Worker threads own their own Django connection wrappers.
            # close_old_connections() only closes unusable/obsolete
            # connections; healthy PostgreSQL sessions may otherwise stay
            # attached to the temporary test database until process exit.
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                worker,
                [attendance_a.id, attendance_b.id],
            )
        )

    assert sum(result is not None for result in results) == 1
    assert allowance_balance(allowance.id) == 0
    assert SubscriptionLedgerEntry.objects.filter(
        allowance=allowance,
        entry_type=SubscriptionLedgerEntry.EntryType.CONSUME,
    ).count() == 1
