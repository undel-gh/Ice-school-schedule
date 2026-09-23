from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors
from scheduling.services import evaluate_lesson_viability


class Command(BaseCommand):
    help = "Evaluate RSVP viability for a lesson after its decision deadline."

    def add_arguments(self, parser):
        parser.add_argument("--lesson", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            lesson_id = UUID(options["lesson"])
        except ValueError as exc:
            raise CommandError("--lesson must be a UUID.") from exc

        result = evaluate_lesson_viability(
            lesson_id=lesson_id,
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"yes={result.yes_count} no={result.no_count} "
                f"no_response={result.no_response_count} "
                f"minimum_met={result.minimum_met}"
            )
        )
