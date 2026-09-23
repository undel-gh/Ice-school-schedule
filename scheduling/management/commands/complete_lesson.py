from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors
from scheduling.services import complete_lesson


class Command(BaseCommand):
    help = "Move one finished CONFIRMED lesson to COMPLETED."

    def add_arguments(self, parser):
        parser.add_argument("--lesson", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            lesson_id = UUID(options["lesson"])
        except ValueError as exc:
            raise CommandError("--lesson must be a UUID.") from exc

        lesson = complete_lesson(
            lesson_id=lesson_id,
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Lesson {lesson.id} is {lesson.status}.")
        )
