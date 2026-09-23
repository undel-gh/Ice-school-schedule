from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from attendance.services import (
    mark_remaining_absent,
    reopen_attendance,
    set_attendance,
    submit_attendance,
)
from core.choices import SubscriptionCategory
from scheduling.models import (
    Lesson,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)
from subscriptions.models import (
    AttendanceCoverage,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.selectors import allowance_balance
from subscriptions.services import issue_subscription

User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="coach",
        password="test",
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="admin",
        password="test",
        is_staff=True,
    )


@pytest.fixture
def outsider(db):
    return User.objects.create_user(
        username="outsider",
        password="test",
    )


@pytest.fixture
def student(db):
    return Student.objects.create(display_name="Маша")


@pytest.fixture
def second_student(db):
    return Student.objects.create(display_name="Саша")


@pytest.fixture
def school_context(db, coach_user):
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Coach",
    )
    group = TrainingGroup.objects.create(
        code="group-a",
        name="Group A",
    )
    venue = Venue.objects.create(
        code="rink",
        name="Rink",
    )
    ice = LessonType.objects.create(
        code="ice",
        name="Ice",
        subscription_category=SubscriptionCategory.ICE,
    )
    return {
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
    }


def make_lesson(
    *,
    school_context,
    starts_at: datetime,
    status: str = Lesson.Status.COMPLETED,
) -> Lesson:
    return Lesson.objects.create(
        group=school_context["group"],
        lesson_type=school_context["ice"],
        coach=school_context["coach"],
        venue=school_context["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


def add_to_roster(
    *,
    lesson: Lesson,
    student: Student,
    actor,
) -> LessonRosterEntry:
    return LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=actor,
    )


def issue_one_ice(
    *,
    student: Student,
    actor,
):
    plan = SubscriptionPlan.objects.create(
        code=f"one-ice-{student.id}",
        name="One ICE",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=1,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    return subscription.allowances.get()


@pytest.mark.django_db
def test_unmarked_to_present_creates_coverage_and_consumes(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    allowance = issue_one_ice(
        student=student,
        actor=coach_user,
    )

    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    coverage = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )
    assert attendance.status == Attendance.Status.PRESENT
    assert coverage.subscription_allowance_id == allowance.id
    assert allowance_balance(allowance.id) == 0


@pytest.mark.django_db
def test_unmarked_to_absent_does_not_consume(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    allowance = issue_one_ice(
        student=student,
        actor=coach_user,
    )

    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    assert attendance.status == Attendance.Status.ABSENT
    assert not AttendanceCoverage.objects.filter(
        attendance=attendance,
        reversed_at__isnull=True,
    ).exists()
    assert allowance_balance(allowance.id) == 1


@pytest.mark.django_db
def test_present_to_absent_reverses_same_coverage(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    allowance = issue_one_ice(
        student=student,
        actor=coach_user,
    )
    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    original_coverage = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )

    corrected = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=10),
    )

    original_coverage.refresh_from_db()
    assert corrected.status == Attendance.Status.ABSENT
    assert original_coverage.reversed_at is not None
    assert allowance_balance(allowance.id) == 1
    assert SubscriptionLedgerEntry.objects.filter(
        coverage=original_coverage,
        entry_type=SubscriptionLedgerEntry.EntryType.RESTORE,
    ).count() == 1


@pytest.mark.django_db
def test_absent_to_present_assigns_coverage(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    allowance = issue_one_ice(
        student=student,
        actor=coach_user,
    )
    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    corrected = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=10),
    )

    assert corrected.id == attendance.id
    assert corrected.status == Attendance.Status.PRESENT
    assert AttendanceCoverage.objects.filter(
        attendance=attendance,
        reversed_at__isnull=True,
    ).count() == 1
    assert allowance_balance(allowance.id) == 0


@pytest.mark.django_db
def test_same_status_is_idempotent(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    allowance = issue_one_ice(
        student=student,
        actor=coach_user,
    )

    first = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    second = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=6),
    )

    assert second.id == first.id
    assert allowance_balance(allowance.id) == 0
    assert SubscriptionLedgerEntry.objects.filter(
        allowance=allowance,
        entry_type=SubscriptionLedgerEntry.EntryType.CONSUME,
    ).count() == 1


@pytest.mark.django_db
def test_staff_admin_can_mark_attendance(
    student,
    admin_user,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )

    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=admin_user,
        now=starts_at + timedelta(minutes=5),
    )

    assert attendance.status == Attendance.Status.ABSENT


@pytest.mark.django_db
def test_non_coach_non_admin_is_rejected(
    student,
    outsider,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )

    with pytest.raises(PermissionDenied):
        set_attendance(
            lesson_id=lesson.id,
            student_id=student.id,
            status=Attendance.Status.ABSENT,
            actor=outsider,
            now=starts_at + timedelta(minutes=5),
        )

    assert Attendance.objects.count() == 0


@pytest.mark.django_db
def test_student_must_be_active_roster_participant(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )

    with pytest.raises(ValidationError):
        set_attendance(
            lesson_id=lesson.id,
            student_id=student.id,
            status=Attendance.Status.ABSENT,
            actor=coach_user,
            now=starts_at + timedelta(minutes=5),
        )

    assert Attendance.objects.count() == 0


@pytest.mark.django_db
def test_attendance_cannot_be_marked_before_start(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )

    with pytest.raises(ValidationError):
        set_attendance(
            lesson_id=lesson.id,
            student_id=student.id,
            status=Attendance.Status.ABSENT,
            actor=coach_user,
            now=starts_at - timedelta(minutes=1),
        )


@pytest.mark.django_db
def test_closed_lesson_requires_reopen(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.CLOSED,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )

    with pytest.raises(ValidationError):
        set_attendance(
            lesson_id=lesson.id,
            student_id=student.id,
            status=Attendance.Status.ABSENT,
            actor=coach_user,
            now=starts_at + timedelta(minutes=5),
        )


@pytest.mark.django_db
def test_mark_remaining_absent_only_marks_unmarked_active_roster(
    student,
    second_student,
    coach_user,
    school_context,
):
    third_student = Student.objects.create(display_name="Катя")
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    add_to_roster(
        lesson=lesson,
        student=second_student,
        actor=coach_user,
    )
    inactive = add_to_roster(
        lesson=lesson,
        student=third_student,
        actor=coach_user,
    )
    inactive.is_active = False
    inactive.deactivated_at = starts_at - timedelta(days=1)
    inactive.deactivated_by = coach_user
    inactive.save(
        update_fields=[
            "is_active",
            "deactivated_at",
            "deactivated_by",
        ]
    )

    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    count = mark_remaining_absent(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at + timedelta(minutes=10),
    )

    assert count == 1
    assert Attendance.objects.get(
        lesson=lesson,
        student=second_student,
    ).status == Attendance.Status.ABSENT
    assert not Attendance.objects.filter(
        lesson=lesson,
        student=third_student,
    ).exists()



@pytest.mark.django_db
def test_submit_attendance_closes_completed_lesson(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.COMPLETED,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    closed = submit_attendance(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at + timedelta(hours=2),
    )

    assert closed.status == Lesson.Status.CLOSED
    assert closed.attendance_submitted_by_id == coach_user.id
    assert closed.attendance_submitted_at is not None


@pytest.mark.django_db
def test_submit_attendance_rejects_unmarked_active_roster(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.COMPLETED,
    )
    add_to_roster(
        lesson=lesson,
        student=student,
        actor=coach_user,
    )

    with pytest.raises(ValidationError):
        submit_attendance(
            lesson_id=lesson.id,
            actor=coach_user,
            now=starts_at + timedelta(hours=2),
        )

    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.COMPLETED


@pytest.mark.django_db
def test_reopen_attendance_requires_admin(
    student,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.CLOSED,
    )
    lesson.attendance_submitted_at = starts_at + timedelta(hours=2)
    lesson.attendance_submitted_by = coach_user
    lesson.save(
        update_fields=[
            "attendance_submitted_at",
            "attendance_submitted_by",
        ]
    )

    with pytest.raises(PermissionDenied):
        reopen_attendance(
            lesson_id=lesson.id,
            actor=coach_user,
            reason="Correction needed",
        )


@pytest.mark.django_db
def test_admin_can_reopen_closed_attendance(
    student,
    admin_user,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.CLOSED,
    )
    lesson.attendance_submitted_at = starts_at + timedelta(hours=2)
    lesson.attendance_submitted_by = coach_user
    lesson.save(
        update_fields=[
            "attendance_submitted_at",
            "attendance_submitted_by",
        ]
    )

    reopened = reopen_attendance(
        lesson_id=lesson.id,
        actor=admin_user,
        reason="Coach correction",
    )

    assert reopened.status == Lesson.Status.COMPLETED
    assert reopened.attendance_submitted_at is None
    assert reopened.attendance_submitted_by_id is None


@pytest.mark.django_db
def test_reopen_attendance_requires_reason(
    admin_user,
    coach_user,
    school_context,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        starts_at=starts_at,
        status=Lesson.Status.CLOSED,
    )
    lesson.attendance_submitted_at = starts_at + timedelta(hours=2)
    lesson.attendance_submitted_by = coach_user
    lesson.save(
        update_fields=[
            "attendance_submitted_at",
            "attendance_submitted_by",
        ]
    )

    with pytest.raises(ValidationError):
        reopen_attendance(
            lesson_id=lesson.id,
            actor=admin_user,
            reason="   ",
        )
