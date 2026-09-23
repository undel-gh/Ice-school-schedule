from __future__ import annotations

from datetime import date, time
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.management import command_errors, resolve_actor
from scheduling.services import create_schedule_template


def _uuid(value: str, option: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise CommandError(f"{option} must be a UUID.") from exc


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD.") from exc


def _time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise CommandError("--start-time must use HH:MM[:SS].") from exc


class Command(BaseCommand):
    help = "Create the first version of a schedule template."

    def add_arguments(self, parser):
        parser.add_argument("--group", required=True)
        parser.add_argument("--lesson-type", required=True)
        parser.add_argument("--coach", required=True)
        parser.add_argument("--venue", required=True)
        parser.add_argument("--weekday", required=True, type=int)
        parser.add_argument("--start-time", required=True)
        parser.add_argument("--duration-minutes", required=True, type=int)
        parser.add_argument("--valid-from", required=True)
        parser.add_argument("--valid-until")
        parser.add_argument("--minimum-attendees", type=int)
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        template = create_schedule_template(
            group_id=_uuid(options["group"], "--group"),
            lesson_type_id=_uuid(
                options["lesson_type"],
                "--lesson-type",
            ),
            coach_id=_uuid(options["coach"], "--coach"),
            venue_id=_uuid(options["venue"], "--venue"),
            weekday=options["weekday"],
            start_time=_time(options["start_time"]),
            duration_minutes=options["duration_minutes"],
            valid_from=_date(options["valid_from"], "--valid-from"),
            valid_until=(
                _date(options["valid_until"], "--valid-until")
                if options["valid_until"]
                else None
            ),
            minimum_attendees_override=options["minimum_attendees"],
            actor=resolve_actor(options["actor"]),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Schedule template created: {template.id}"
            )
        )
