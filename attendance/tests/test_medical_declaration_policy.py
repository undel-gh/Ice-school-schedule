from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.medical_policy import medical_declaration_is_available
from attendance.medical_registration import manager_medical_registration_candidates
from attendance.models import AbsenceJustification, Attendance
from attendance.services import (
    declare_medical_absence,
    declare_medical_absence_by_manager,
)
from core.time import school_date
from scheduling.models import (
    Lesson,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)
from scheduling.selectors import get_student_schedule

User = get_user_model()


POLICY_CASES = (
    ("none", True, "new"),
    ("pending", False, "same"),
    ("verified", False, "same"),
    ("rejected", False, "error"),
    ("revoked_administrative", False, "error"),
    ("revoked_attendance_correction", True, "new"),
)


def _make_context():
    guardian = User.objects.create_user(
        username="medical-policy-guardian",
        password="test",
    )
    manager = User.objects.create_superuser(
        username="medical-policy-manager",
        password="test",
    )
    coach_user = User.objects.create_user(
        username="medical-policy-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Тренер policy",
    )
    student = Student.objects.create(display_name="Ученик policy")
    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    group = TrainingGroup.objects.create(
        code="medical-policy-group",
        name="Группа policy",
    )
    venue = Venue.objects.create(
        code="medical-policy-venue",
        name="Лёд policy",
    )
    lesson_type = LessonType.objects.create(
        code="medical-policy-ice",
        name="Лёд",
        subscription_category="ice",
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
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=manager,
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_at=lesson.ends_at,
        marked_by=coach_user,
        updated_by=coach_user,
    )
    return {
        "guardian": guardian,
        "manager": manager,
        "student": student,
        "lesson": lesson,
        "attendance": attendance,
    }


def _make_justification(*, ctx, state: str):
    if state == "none":
        return None

    now = timezone.now()
    values = {
        "student": ctx["student"],
        "lesson": ctx["lesson"],
        "type": AbsenceJustification.Type.MEDICAL,
        "status": state,
        "declared_by": ctx["guardian"],
    }
    if state == "pending":
        values["status"] = AbsenceJustification.Status.PENDING
    elif state == "verified":
        values.update(
            status=AbsenceJustification.Status.VERIFIED,
            reviewed_at=now,
            reviewed_by=ctx["manager"],
        )
    elif state == "rejected":
        values.update(
            status=AbsenceJustification.Status.REJECTED,
            reviewed_at=now,
            reviewed_by=ctx["manager"],
        )
    elif state == "revoked_administrative":
        values.update(
            status=AbsenceJustification.Status.REVOKED,
            reviewed_at=now - timedelta(minutes=1),
            reviewed_by=ctx["manager"],
            revoked_at=now,
            revoked_by=ctx["manager"],
            revocation_reason=(
                AbsenceJustification.RevocationReason.ADMINISTRATIVE
            ),
        )
    elif state == "revoked_attendance_correction":
        values.update(
            status=AbsenceJustification.Status.REVOKED,
            reviewed_at=now - timedelta(minutes=1),
            reviewed_by=ctx["manager"],
            revoked_at=now,
            revoked_by=ctx["manager"],
            revocation_reason=(
                AbsenceJustification.RevocationReason.ATTENDANCE_CORRECTION
            ),
        )
    else:
        raise AssertionError(f"Unsupported test state: {state}")
    return AbsenceJustification.objects.create(**values)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("state", "expected_available", "_service_outcome"),
    POLICY_CASES,
)
def test_medical_declaration_policy_matches_all_ui_queries(
    client,
    state,
    expected_available,
    _service_outcome,
):
    ctx = _make_context()
    latest = _make_justification(ctx=ctx, state=state)

    assert medical_declaration_is_available(latest) is expected_available

    lesson_date = school_date(ctx["lesson"].starts_at)
    schedule = get_student_schedule(
        student_id=ctx["student"].id,
        from_date=lesson_date,
        until_date=lesson_date,
    )
    assert len(schedule) == 1
    assert schedule[0].can_declare_medical_absence is expected_available

    client.force_login(ctx["guardian"])
    account = client.get(
        reverse("student_account:account"),
        {"student": str(ctx["student"].id)},
    )
    assert account.status_code == 200
    history_attendance = next(
        row
        for row in account.context["history_page"].object_list
        if row.id == ctx["attendance"].id
    )
    assert (
        history_attendance.account_can_declare_medical_absence
        is expected_available
    )

    manager_candidate_ids = set(
        manager_medical_registration_candidates().values_list("id", flat=True)
    )
    assert (
        ctx["attendance"].id in manager_candidate_ids
    ) is expected_available


@pytest.mark.django_db
@pytest.mark.parametrize("entrypoint", ("student", "manager"))
@pytest.mark.parametrize(
    ("state", "_expected_available", "service_outcome"),
    POLICY_CASES,
)
def test_medical_declaration_entrypoints_share_state_semantics(
    entrypoint,
    state,
    _expected_available,
    service_outcome,
):
    ctx = _make_context()
    latest = _make_justification(ctx=ctx, state=state)

    def call_service():
        if entrypoint == "student":
            return declare_medical_absence(
                student_id=ctx["student"].id,
                lesson_id=ctx["lesson"].id,
                actor=ctx["guardian"],
            )
        return declare_medical_absence_by_manager(
            attendance_id=ctx["attendance"].id,
            actor=ctx["manager"],
        )

    if service_outcome == "error":
        with pytest.raises(ValidationError):
            call_service()
        assert AbsenceJustification.objects.filter(
            student=ctx["student"],
            lesson=ctx["lesson"],
            type=AbsenceJustification.Type.MEDICAL,
        ).count() == 1
        return

    result = call_service()
    if service_outcome == "same":
        assert latest is not None
        assert result.id == latest.id
        assert AbsenceJustification.objects.filter(
            student=ctx["student"],
            lesson=ctx["lesson"],
            type=AbsenceJustification.Type.MEDICAL,
        ).count() == 1
        return

    assert service_outcome == "new"
    assert result.status == AbsenceJustification.Status.PENDING
    if latest is None:
        assert AbsenceJustification.objects.filter(
            student=ctx["student"],
            lesson=ctx["lesson"],
            type=AbsenceJustification.Type.MEDICAL,
        ).count() == 1
    else:
        assert result.id != latest.id
        assert AbsenceJustification.objects.filter(
            student=ctx["student"],
            lesson=ctx["lesson"],
            type=AbsenceJustification.Type.MEDICAL,
        ).count() == 2
