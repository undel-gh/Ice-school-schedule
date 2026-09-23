from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from attendance.services import (
    declare_medical_absence,
    mark_expected_present,
    mark_remaining_absent,
    reject_medical_absence,
    reopen_attendance,
    revoke_medical_absence,
    set_attendance,
    submit_attendance,
    verify_medical_absence,
)
from core.choices import SubscriptionCategory
from scheduling.models import (
    Lesson,
    LessonResponse,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
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



@pytest.mark.django_db
def test_declare_medical_absence_requires_student_access(
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
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    with pytest.raises(PermissionDenied):
        declare_medical_absence(
            student_id=student.id,
            lesson_id=lesson.id,
            actor=outsider,
        )


@pytest.mark.django_db
def test_declare_medical_absence_requires_absent_attendance(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )

    with pytest.raises(ValidationError):
        declare_medical_absence(
            student_id=student.id,
            lesson_id=lesson.id,
            actor=admin_user,
        )


@pytest.mark.django_db
def test_verify_medical_absence_creates_makeup_from_matching_allowance(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    plan = SubscriptionPlan.objects.create(
        code="medical-ice",
        name="Medical ICE",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=2,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin_user,
    )

    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )
    verified = verify_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        valid_until=date(2026, 10, 15),
        now=starts_at + timedelta(days=1),
    )

    entitlement = MakeupEntitlement.objects.get(
        source_justification=verified,
    )
    assert attendance.status == Attendance.Status.ABSENT
    assert verified.status == "verified"
    assert entitlement.reason == MakeupEntitlement.Reason.MEDICAL_VERIFIED
    assert entitlement.source_subscription_allowance.subscription_id == subscription.id
    assert entitlement.valid_from == date(2026, 10, 1)
    assert entitlement.valid_until == date(2026, 10, 15)


@pytest.mark.django_db
def test_verify_medical_absence_without_allowance_creates_no_makeup(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )
    verified = verify_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        valid_until=date(2026, 10, 15),
        now=starts_at + timedelta(days=1),
    )

    assert verified.status == "verified"
    assert not MakeupEntitlement.objects.filter(
        source_justification=verified,
    ).exists()


@pytest.mark.django_db
def test_reject_medical_absence_moves_pending_to_rejected(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )

    rejected = reject_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        now=starts_at + timedelta(days=1),
    )

    assert rejected.status == "rejected"
    assert rejected.reviewed_by_id == admin_user.id


@pytest.mark.django_db
def test_revoke_medical_absence_cancels_active_makeup(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    plan = SubscriptionPlan.objects.create(
        code="medical-revoke",
        name="Medical revoke",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=1,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin_user,
    )
    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )
    verify_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        valid_until=date(2026, 10, 15),
        now=starts_at + timedelta(days=1),
    )
    entitlement = MakeupEntitlement.objects.get(
        source_justification=justification,
    )

    revoked = revoke_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        now=starts_at + timedelta(days=2),
    )

    entitlement.refresh_from_db()
    assert revoked.status == "revoked"
    assert revoked.revoked_by_id == admin_user.id
    assert entitlement.cancelled_at is not None
    assert entitlement.cancelled_by_id == admin_user.id



@pytest.mark.django_db
def test_present_attendance_audit_events_share_correlation_id(
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
    plan = SubscriptionPlan.objects.create(
        code="audit-correlation",
        name="Audit correlation",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=1,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
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
    events = AuditEvent.objects.filter(
        event_type__in=[
            "AttendanceMarkedPresent",
            "AttendanceCoverageAssigned",
            "SubscriptionAllowanceConsumed",
        ],
    ).filter(
        models.Q(aggregate_id=attendance.id)
        | models.Q(aggregate_id=coverage.id)
        | models.Q(
            aggregate_id=coverage.subscription_allowance_id
        )
    )

    correlation_ids = set(
        events.values_list("correlation_id", flat=True)
    )
    assert events.count() == 3
    assert len(correlation_ids) == 1


@pytest.mark.django_db
def test_uncovered_attendance_emits_correlated_uncovered_event(
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

    attendance = set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )

    marked = AuditEvent.objects.get(
        event_type="AttendanceMarkedPresent",
        aggregate_id=attendance.id,
    )
    uncovered = AuditEvent.objects.get(
        event_type="AttendanceUncovered",
        aggregate_id=attendance.id,
    )
    assert marked.correlation_id == uncovered.correlation_id



@pytest.mark.django_db
def test_mark_expected_present_requires_confirmation(
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
    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=coach_user,
    )

    with pytest.raises(ValidationError):
        mark_expected_present(
            lesson_id=lesson.id,
            actor=coach_user,
            now=starts_at + timedelta(minutes=5),
            confirmed=False,
        )

    assert not Attendance.objects.filter(
        lesson=lesson,
        student=student,
    ).exists()


@pytest.mark.django_db
def test_mark_expected_present_only_marks_unmarked_yes_responses(
    student,
    second_student,
    coach_user,
    school_context,
):
    third = Student.objects.create(display_name="Student C")
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

    for s in (student, second_student, third):
        add_to_roster(
            lesson=lesson,
            student=s,
            actor=coach_user,
        )

    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=coach_user,
    )
    LessonResponse.objects.create(
        lesson=lesson,
        student=second_student,
        status=LessonResponse.Status.YES,
        updated_by=coach_user,
    )
    LessonResponse.objects.create(
        lesson=lesson,
        student=third,
        status=LessonResponse.Status.NO,
        updated_by=coach_user,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=second_student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=1),
    )

    changed = mark_expected_present(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
        confirmed=True,
    )

    assert changed == 1
    assert Attendance.objects.get(
        lesson=lesson,
        student=student,
    ).status == Attendance.Status.PRESENT
    assert Attendance.objects.get(
        lesson=lesson,
        student=second_student,
    ).status == Attendance.Status.ABSENT
    assert not Attendance.objects.filter(
        lesson=lesson,
        student=third,
    ).exists()



@pytest.mark.django_db
def test_absent_to_present_cancels_unused_verified_medical_makeup(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    plan = SubscriptionPlan.objects.create(
        code="medical-correct-present",
        name="Medical correction",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=2,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin_user,
    )
    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )
    verify_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        valid_until=date(2026, 10, 15),
        now=starts_at + timedelta(days=1),
    )
    makeup = MakeupEntitlement.objects.get(
        source_justification=justification,
    )

    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=starts_at + timedelta(days=2),
    )

    makeup.refresh_from_db()
    assert makeup.cancelled_at is not None
    assert Attendance.objects.get(
        lesson=lesson,
        student=student,
    ).status == Attendance.Status.PRESENT


@pytest.mark.django_db
def test_revoke_medical_absence_rejects_used_makeup(
    student,
    coach_user,
    admin_user,
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
    StudentAccess.objects.create(
        user=admin_user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    set_attendance(
        lesson_id=lesson.id,
        student_id=student.id,
        status=Attendance.Status.ABSENT,
        actor=coach_user,
        now=starts_at + timedelta(minutes=5),
    )
    plan = SubscriptionPlan.objects.create(
        code="medical-used",
        name="Medical used",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=2,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin_user,
    )
    justification = declare_medical_absence(
        student_id=student.id,
        lesson_id=lesson.id,
        actor=admin_user,
    )
    verify_medical_absence(
        justification_id=justification.id,
        actor=admin_user,
        valid_until=date(2026, 10, 15),
        now=starts_at + timedelta(days=1),
    )
    makeup = MakeupEntitlement.objects.get(
        source_justification=justification,
    )
    replacement = make_lesson(
        school_context=school_context,
        starts_at=datetime(
            2026,
            10,
            5,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    replacement.status = Lesson.Status.COMPLETED
    replacement.save(update_fields=["status"])
    add_to_roster(
        lesson=replacement,
        student=student,
        actor=coach_user,
    )
    attendance = set_attendance(
        lesson_id=replacement.id,
        student_id=student.id,
        status=Attendance.Status.PRESENT,
        actor=coach_user,
        now=replacement.starts_at + timedelta(minutes=5),
    )
    coverage = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )
    assert coverage.makeup_entitlement_id == makeup.id

    with pytest.raises(ValidationError):
        revoke_medical_absence(
            justification_id=justification.id,
            actor=admin_user,
            now=replacement.starts_at + timedelta(days=1),
        )

    makeup.refresh_from_db()
    justification.refresh_from_db()
    assert makeup.cancelled_at is None
    assert justification.status == AbsenceJustification.Status.VERIFIED
