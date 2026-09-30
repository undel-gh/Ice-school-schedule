from __future__ import annotations

from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.services import record_event
from core.permissions import require_permission
from core.time import school_date

from .models import CoachProfile, Student, StudentAccess

User = get_user_model()


def _clean_display_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError({"display_name": "Display name is required."})
    return value


@transaction.atomic
def create_student(
    *,
    display_name: str,
    is_active: bool,
    actor: User,
) -> Student:
    require_permission(
        actor,
        "accounts.add_student",
        "Student creation permission is required.",
    )
    student = Student.objects.create(
        display_name=_clean_display_name(display_name),
        is_active=is_active,
    )
    record_event(
        event_type="StudentCreated",
        aggregate_type="Student",
        aggregate_id=student.id,
        actor=actor,
        payload={
            "display_name": student.display_name,
            "is_active": student.is_active,
        },
    )
    return student


@transaction.atomic
def update_student(
    *,
    student_id: UUID,
    display_name: str,
    is_active: bool,
    actor: User,
) -> Student:
    require_permission(
        actor,
        "accounts.change_student",
        "Student change permission is required.",
    )
    student = Student.objects.select_for_update().get(pk=student_id)
    previous = {
        "display_name": student.display_name,
        "is_active": student.is_active,
    }
    student.display_name = _clean_display_name(display_name)
    student.is_active = is_active
    student.save(update_fields=["display_name", "is_active", "updated_at"])
    record_event(
        event_type="StudentChanged",
        aggregate_type="Student",
        aggregate_id=student.id,
        actor=actor,
        payload={
            "previous": previous,
            "display_name": student.display_name,
            "is_active": student.is_active,
        },
    )
    return student


@transaction.atomic
def create_student_access(
    *,
    user_id: UUID,
    student_id: UUID,
    role: str,
    is_active: bool,
    actor: User,
) -> StudentAccess:
    require_permission(
        actor,
        "accounts.add_studentaccess",
        "Student access creation permission is required.",
    )
    if role not in StudentAccess.Role.values:
        raise ValidationError({"role": "Unsupported student access role."})
    User.objects.select_for_update().get(pk=user_id)
    Student.objects.select_for_update().get(pk=student_id)
    if StudentAccess.objects.filter(
        user_id=user_id,
        student_id=student_id,
    ).exists():
        raise ValidationError(
            {"access": "This user already has an access record for the student."}
        )
    access = StudentAccess.objects.create(
        user_id=user_id,
        student_id=student_id,
        role=role,
        is_active=is_active,
    )
    record_event(
        event_type="StudentAccessCreated",
        aggregate_type="StudentAccess",
        aggregate_id=access.id,
        actor=actor,
        payload={
            "user_id": str(user_id),
            "student_id": str(student_id),
            "role": role,
            "is_active": is_active,
        },
    )
    return access


@transaction.atomic
def update_student_access(
    *,
    access_id: UUID,
    role: str,
    is_active: bool,
    actor: User,
) -> StudentAccess:
    require_permission(
        actor,
        "accounts.change_studentaccess",
        "Student access change permission is required.",
    )
    if role not in StudentAccess.Role.values:
        raise ValidationError({"role": "Unsupported student access role."})
    access = StudentAccess.objects.select_for_update().get(pk=access_id)
    previous = {"role": access.role, "is_active": access.is_active}
    access.role = role
    access.is_active = is_active
    access.save(update_fields=["role", "is_active"])
    record_event(
        event_type="StudentAccessChanged",
        aggregate_type="StudentAccess",
        aggregate_id=access.id,
        actor=actor,
        payload={
            "user_id": str(access.user_id),
            "student_id": str(access.student_id),
            "previous": previous,
            "role": access.role,
            "is_active": access.is_active,
        },
    )
    return access


@transaction.atomic
def create_coach_profile(
    *,
    user_id: UUID,
    display_name: str,
    is_active: bool,
    actor: User,
) -> CoachProfile:
    require_permission(
        actor,
        "accounts.add_coachprofile",
        "Coach profile creation permission is required.",
    )
    User.objects.select_for_update().get(pk=user_id)
    if CoachProfile.objects.filter(user_id=user_id).exists():
        raise ValidationError(
            {"user": "This user already has a coach profile."}
        )
    coach = CoachProfile.objects.create(
        user_id=user_id,
        display_name=_clean_display_name(display_name),
        is_active=is_active,
    )
    record_event(
        event_type="CoachProfileCreated",
        aggregate_type="CoachProfile",
        aggregate_id=coach.id,
        actor=actor,
        payload={
            "user_id": str(user_id),
            "display_name": coach.display_name,
            "is_active": coach.is_active,
        },
    )
    return coach


@transaction.atomic
def update_coach_profile(
    *,
    coach_id: UUID,
    display_name: str,
    is_active: bool,
    actor: User,
) -> CoachProfile:
    require_permission(
        actor,
        "accounts.change_coachprofile",
        "Coach profile change permission is required.",
    )
    coach = CoachProfile.objects.select_for_update().get(pk=coach_id)
    previous = {
        "display_name": coach.display_name,
        "is_active": coach.is_active,
    }
    if coach.is_active and not is_active:
        from scheduling.models import Lesson, ScheduleTemplate

        now = timezone.now()
        today = school_date(now)
        has_active_template = (
            ScheduleTemplate.objects.select_for_update()
            .filter(coach=coach, is_active=True)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
            .exists()
        )
        if has_active_template:
            raise ValidationError(
                {
                    "is_active": (
                        "The coach cannot be deactivated while assigned to an "
                        "active current or future schedule template. Reassign or "
                        "version the template first."
                    )
                }
            )
        has_future_lesson = (
            Lesson.objects.select_for_update()
            .filter(coach=coach, starts_at__gte=now)
            .exclude(status=Lesson.Status.CANCELLED)
            .exists()
        )
        if has_future_lesson:
            raise ValidationError(
                {
                    "is_active": (
                        "The coach cannot be deactivated while assigned to future "
                        "non-cancelled lessons. Reassign, reschedule or cancel "
                        "them first."
                    )
                }
            )
    coach.display_name = _clean_display_name(display_name)
    coach.is_active = is_active
    coach.save(update_fields=["display_name", "is_active"])
    record_event(
        event_type="CoachProfileChanged",
        aggregate_type="CoachProfile",
        aggregate_id=coach.id,
        actor=actor,
        payload={
            "user_id": str(coach.user_id),
            "previous": previous,
            "display_name": coach.display_name,
            "is_active": coach.is_active,
        },
    )
    return coach
