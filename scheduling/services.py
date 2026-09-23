from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEvent

from .models import Lesson


@transaction.atomic
def complete_lesson(
    *,
    lesson_id: UUID,
    now: datetime,
) -> Lesson:
    """Move a finished confirmed lesson into attendance-entry state."""
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)

    if lesson.status != Lesson.Status.CONFIRMED:
        raise ValidationError(
            {
                "lesson": (
                    "Only a CONFIRMED lesson can be completed."
                )
            }
        )
    if now < lesson.ends_at:
        raise ValidationError(
            {"lesson": "Lesson cannot be completed before it ends."}
        )

    lesson.status = Lesson.Status.COMPLETED
    lesson.completed_at = now
    lesson.save(
        update_fields=["status", "completed_at", "updated_at"]
    )

    AuditEvent.objects.create(
        event_type="LessonCompleted",
        actor=None,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload={"completed_at": now.isoformat()},
    )
    return lesson
