from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.time import school_date

from accounts.models import CoachProfile, Student
from attendance.models import AbsenceJustification, Attendance
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    assign_attendance_coverage,
    issue_subscription,
)

User = get_user_model()


@pytest.mark.django_db
def test_manager_verifies_pending_medical_absence(client):
    manager = User.objects.create_user(
        username="medical-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(username="medical-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="medical-group", name="Medical Group")
    venue = Venue.objects.create(code="medical-venue", name="Medical Venue")
    lesson_type = LessonType.objects.create(
        code="medical-ice",
        name="ICE",
        subscription_category="ice",
    )
    student = Student.objects.create(display_name="Medical Student")
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
    Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_at=lesson.ends_at,
        marked_by=manager,
        updated_by=manager,
    )
    justification = AbsenceJustification.objects.create(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
        declared_by=manager,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "attendance_manager:medical_verify",
            kwargs={"justification_id": justification.id},
        ),
        {"valid_until": (school_date(timezone.now()) + timedelta(days=60)).isoformat()},
    )

    assert response.status_code == 302
    justification.refresh_from_db()
    assert justification.status == AbsenceJustification.Status.VERIFIED
    assert justification.reviewed_by == manager


@pytest.mark.django_db
def test_manager_post_checks_permission_before_medical_lookup(client):
    import uuid

    outsider = User.objects.create_user(
        username="medical-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "attendance_manager:medical_reject",
            kwargs={"justification_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 403



def _make_completed_present_attendance(*, actor, suffix: str):
    coach_user = User.objects.create_user(
        username=f"coverage-coach-{suffix}",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name=f"Coverage Coach {suffix}",
    )
    group = TrainingGroup.objects.create(
        code=f"coverage-group-{suffix}",
        name=f"Coverage Group {suffix}",
    )
    venue = Venue.objects.create(
        code=f"coverage-venue-{suffix}",
        name=f"Coverage Venue {suffix}",
    )
    lesson_type = LessonType.objects.create(
        code=f"coverage-ice-{suffix}",
        name="ICE",
        subscription_category="ice",
    )
    student = Student.objects.create(
        display_name=f"Coverage Student {suffix}"
    )
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
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=lesson.ends_at,
        marked_by=actor,
        updated_by=actor,
    )
    return attendance


def _issue_ice_subscription(*, student, actor, starts_at, suffix: str):
    plan = SubscriptionPlan.objects.create(
        code=f"coverage-plan-{suffix}",
        name=f"Coverage Plan {suffix}",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=1,
    )
    lesson_date = school_date(starts_at)
    return issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=lesson_date - timedelta(days=10),
        valid_until=lesson_date + timedelta(days=10),
        actor=actor,
    )


@pytest.mark.django_db
def test_manager_coverage_report_lists_uncovered_present_attendance(client):
    manager = User.objects.create_user(
        username="coverage-report-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    attendance = _make_completed_present_attendance(
        actor=manager,
        suffix="report",
    )
    client.force_login(manager)

    response = client.get(
        reverse("attendance_manager:coverage_report")
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert attendance.student.display_name in body
    assert "без покрытия" in body.lower()


@pytest.mark.django_db
def test_manager_recovers_uncovered_attendance_from_web(client):
    manager = User.objects.create_user(
        username="coverage-recovery-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    attendance = _make_completed_present_attendance(
        actor=manager,
        suffix="recovery",
    )
    entitlement = OneTimeEntitlement.objects.create(
        student=attendance.student,
        lesson=attendance.lesson,
        entitlement_type=OneTimeEntitlement.Type.SINGLE_ICE,
        category="ice",
        created_by=manager,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "attendance_manager:coverage_recover",
            kwargs={"attendance_id": attendance.id},
        )
    )

    assert response.status_code == 302
    coverage = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )
    assert coverage.one_time_entitlement_id == entitlement.id


@pytest.mark.django_db
def test_manager_rebinds_attendance_coverage_from_web(client):
    manager = User.objects.create_user(
        username="coverage-rebind-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    attendance = _make_completed_present_attendance(
        actor=manager,
        suffix="rebind",
    )
    first = _issue_ice_subscription(
        student=attendance.student,
        actor=manager,
        starts_at=attendance.lesson.starts_at,
        suffix="rebind-a",
    )
    second = _issue_ice_subscription(
        student=attendance.student,
        actor=manager,
        starts_at=attendance.lesson.starts_at,
        suffix="rebind-b",
    )
    old_coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=manager,
    )
    second_allowance = second.allowances.get()
    assert old_coverage.subscription_allowance.subscription_id == first.id
    client.force_login(manager)

    response = client.post(
        reverse(
            "attendance_manager:coverage_rebind",
            kwargs={"attendance_id": attendance.id},
        ),
        {"source": f"allowance:{second_allowance.id}"},
    )

    assert response.status_code == 302
    old_coverage.refresh_from_db()
    assert old_coverage.reversed_at is not None
    active = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )
    assert active.subscription_allowance_id == second_allowance.id


@pytest.mark.django_db
def test_manager_coverage_change_checks_permission_before_lookup(client):
    import uuid

    outsider = User.objects.create_user(
        username="coverage-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "attendance_manager:coverage_recover",
            kwargs={"attendance_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 403



@pytest.mark.django_db
def test_manager_rebind_uses_restore_credit_for_same_allowance_makeup(
    client,
):
    manager = User.objects.create_user(
        username="coverage-same-allowance-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    attendance = _make_completed_present_attendance(
        actor=manager,
        suffix="same-allowance",
    )
    subscription = _issue_ice_subscription(
        student=attendance.student,
        actor=manager,
        starts_at=attendance.lesson.starts_at,
        suffix="same-allowance",
    )
    allowance = subscription.allowances.get()
    old_coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=manager,
    )
    assert old_coverage.subscription_allowance_id == allowance.id

    lesson_date = school_date(attendance.lesson.starts_at)
    makeup = MakeupEntitlement.objects.create(
        student=attendance.student,
        source_lesson=attendance.lesson,
        source_subscription_allowance=allowance,
        category="ice",
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=lesson_date - timedelta(days=1),
        valid_until=lesson_date + timedelta(days=1),
        created_by=manager,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "attendance_manager:coverage_rebind",
            kwargs={"attendance_id": attendance.id},
        ),
        {"source": f"makeup:{makeup.id}"},
    )

    assert response.status_code == 302
    old_coverage.refresh_from_db()
    assert old_coverage.reversed_at is not None
    active = AttendanceCoverage.objects.get(
        attendance=attendance,
        reversed_at__isnull=True,
    )
    assert active.subscription_allowance_id == allowance.id
    assert active.makeup_entitlement_id == makeup.id
