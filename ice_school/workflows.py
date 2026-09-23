from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction

from scheduling.models import Lesson
from scheduling.services import reschedule_lesson
from subscriptions.services import apply_school_reschedule_entitlements

User = get_user_model()


@transaction.atomic
def reschedule_lesson_with_entitlements(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor: User,
    reason: str,
    now: datetime,
) -> Lesson:
    replacement = reschedule_lesson(
        lesson_id=lesson_id,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        actor=actor,
        reason=reason,
        now=now,
    )
    apply_school_reschedule_entitlements(
        source_lesson_id=lesson_id,
        replacement_lesson_id=replacement.id,
        actor=actor,
    )
    return replacement
