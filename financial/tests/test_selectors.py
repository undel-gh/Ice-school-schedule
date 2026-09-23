from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from financial.selectors import (
    get_closed_lesson_report,
    get_expired_unused_report,
    get_uncovered_attendance,
)
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    assign_attendance_coverage,
    issue_subscription,
    reverse_attendance_coverage,
)

User = get_user_model()


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="finance-admin", password="test")


@pytest.fixture
def school_context(db, actor):
    coach = CoachProfile.objects.create(user=actor, display_name="Coach")
    group = TrainingGroup.objects.create(code="finance-group", name="Finance Group")
    venue = Venue.objects.create(code="finance-rink", name="Finance Rink")
    ice = LessonType.objects.create(
        code="finance-ice",
        name="Ice",
        subscription_category=SubscriptionCategory.ICE,
    )
    return coach, group, venue, ice


def make_lesson(*, school_context, status=Lesson.Status.CLOSED, starts_at=None):
    coach, group, venue, ice = school_context
    starts_at = starts_at or datetime(
        2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc
    )
    return Lesson.objects.create(
        group=group,
        lesson_type=ice,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


def make_plan(*, code, visits):
    plan = SubscriptionPlan.objects.create(code=code, name=code)
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=visits,
    )
    return plan


@pytest.mark.django_db
def test_closed_lesson_report_counts_covered_and_uncovered(
    actor,
    school_context,
):
    covered_student = Student.objects.create(display_name="Covered")
    uncovered_student = Student.objects.create(display_name="Uncovered")
    absent_student = Student.objects.create(display_name="Absent")
    lesson = make_lesson(school_context=school_context)

    plan = make_plan(code="finance-plan", visits=1)
    issue_subscription(
        student_id=covered_student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )

    covered = Attendance.objects.create(
        lesson=lesson,
        student=covered_student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    Attendance.objects.create(
        lesson=lesson,
        student=uncovered_student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    Attendance.objects.create(
        lesson=lesson,
        student=absent_student,
        status=Attendance.Status.ABSENT,
        marked_by=actor,
    )
    assign_attendance_coverage(attendance_id=covered.id, actor=actor)

    report = get_closed_lesson_report(lesson_id=lesson.id)

    assert report.present_count == 2
    assert report.absent_count == 1
    assert report.covered_count == 1
    assert report.uncovered_count == 1
    assert {student.id for student in report.present_students} == {
        covered_student.id,
        uncovered_student.id,
    }
    assert {student.id for student in report.absent_students} == {
        absent_student.id,
    }


@pytest.mark.django_db
def test_closed_lesson_report_rejects_non_closed_lesson(school_context):
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.COMPLETED,
    )

    with pytest.raises(ValidationError):
        get_closed_lesson_report(lesson_id=lesson.id)


@pytest.mark.django_db
def test_uncovered_query_includes_reversed_coverage(
    actor,
    school_context,
):
    student = Student.objects.create(display_name="Student")
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.COMPLETED,
    )
    plan = make_plan(code="coverage-plan", visits=1)
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )
    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
    )

    ids = list(get_uncovered_attendance().values_list("id", flat=True))
    assert attendance.id in ids


@pytest.mark.django_db
def test_expired_unused_report_returns_positive_balances_and_makeups(
    actor,
    school_context,
):
    student = Student.objects.create(display_name="Expired")
    plan = make_plan(code="expired-plan", visits=2)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 8, 1),
        valid_until=date(2026, 8, 31),
        actor=actor,
    )
    allowance = subscription.allowances.get()
    source_lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.COMPLETED,
        starts_at=datetime(
            2026, 8, 20, 15, 0, tzinfo=dt_timezone.utc
        ),
    )
    MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        created_by=actor,
    )

    report = get_expired_unused_report(as_of=date(2026, 9, 15))

    assert len(report) == 1
    assert report[0].subscription.id == subscription.id
    assert len(report[0].balances) == 1
    assert report[0].balances[0].allowance.id == allowance.id
    assert report[0].balances[0].balance == 2
    assert len(report[0].available_makeups) == 1
