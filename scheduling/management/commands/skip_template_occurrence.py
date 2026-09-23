from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors, resolve_actor
from scheduling.services import skip_template_occurrence


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError("--date must use YYYY-MM-DD.") from exc


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise CommandError("--template must be a UUID.") from exc


class Command(BaseCommand):
    help = "Skip one regular template occurrence by creating it as CANCELLED."

    def add_arguments(self, parser):
        parser.add_argument("--template", required=True)
        parser.add_argument("--date", required=True)
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        lesson = skip_template_occurrence(
            template_id=_uuid(options["template"]),
            occurrence_date=_date(options["date"]),
            actor=resolve_actor(options["actor"]),
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Template occurrence skipped as lesson {lesson.id}."
            )
        )
