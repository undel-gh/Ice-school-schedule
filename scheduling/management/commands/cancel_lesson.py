from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors, resolve_actor
from scheduling.models import Lesson
from scheduling.services import cancel_lesson


class Command(BaseCommand):
    help = "Cancel one DRAFT, RSVP_OPEN or CONFIRMED lesson."

    def add_arguments(self, parser):
        parser.add_argument("--lesson", required=True)
        parser.add_argument("--reason", required=True, choices=Lesson.CancellationReason.values)
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            lesson_id = UUID(options["lesson"])
        except ValueError as exc:
            raise CommandError("--lesson must be a UUID.") from exc
        lesson = cancel_lesson(
            lesson_id=lesson_id,
            actor=resolve_actor(options["actor"]),
            reason=options["reason"],
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Lesson {lesson.id} is {lesson.status}.")
        )
