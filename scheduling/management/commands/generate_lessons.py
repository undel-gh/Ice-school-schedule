from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.management import command_errors
from scheduling.services import generate_lessons


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Generate concrete lessons from one schedule template."

    def add_arguments(self, parser):
        parser.add_argument("--template", required=True)
        parser.add_argument("--from-date", required=True)
        parser.add_argument("--until-date", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            template_id = UUID(options["template"])
        except ValueError as exc:
            raise CommandError("--template must be a UUID.") from exc

        lessons = generate_lessons(
            template_id=template_id,
            from_date=_date(options["from_date"], "--from-date"),
            until_date=_date(options["until_date"], "--until-date"),
            actor=None,
        )
        self.stdout.write(self.style.SUCCESS(f"Lessons returned: {len(lessons)}"))
