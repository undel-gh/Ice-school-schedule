from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import resolve_actor
from core.time import make_school_aware
from ice_school.workflows import reschedule_lesson_with_entitlements
from scheduling.models import Lesson


def _datetime(value: str, option: str):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(
            f"{option} must use ISO 8601 date-time format."
        ) from exc
    if timezone.is_naive(parsed):
        parsed = make_school_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = "Cancel a lesson and create its replacement with make-up entitlements."

    def add_arguments(self, parser):
        parser.add_argument("--lesson", required=True)
        parser.add_argument("--starts-at", required=True)
        parser.add_argument("--ends-at", required=True)
        parser.add_argument("--reason", required=True, choices=Lesson.CancellationReason.values)
        parser.add_argument("--actor", required=True)

    def handle(self, *args, **options):
        try:
            lesson_id = UUID(options["lesson"])
        except ValueError as exc:
            raise CommandError("--lesson must be a UUID.") from exc
        replacement = reschedule_lesson_with_entitlements(
            lesson_id=lesson_id,
            new_starts_at=_datetime(options["starts_at"], "--starts-at"),
            new_ends_at=_datetime(options["ends_at"], "--ends-at"),
            actor=resolve_actor(options["actor"]),
            reason=options["reason"],
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Replacement lesson: {replacement.id}")
        )
