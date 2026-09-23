from __future__ import annotations

from datetime import date, time
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors, resolve_actor
from scheduling.services import version_schedule_template


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError("--effective-from must use YYYY-MM-DD.") from exc


def _time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise CommandError("--start-time must use HH:MM[:SS].") from exc


def _uuid(value: str, option: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise CommandError(f"{option} must be a UUID.") from exc


class Command(BaseCommand):
    help = "Create a new future version of a schedule template."

    def add_arguments(self, parser):
        parser.add_argument("--template", required=True)
        parser.add_argument("--effective-from", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--group")
        parser.add_argument("--lesson-type")
        parser.add_argument("--coach")
        parser.add_argument("--venue")
        parser.add_argument("--weekday", type=int)
        parser.add_argument("--start-time")
        parser.add_argument("--duration-minutes", type=int)
        parser.add_argument("--minimum-attendees", type=int)
        parser.add_argument(
            "--clear-minimum-attendees",
            action="store_true",
        )

    @command_errors
    def handle(self, *args, **options):
        kwargs = {}
        for option, key in (
            ("group", "group_id"),
            ("lesson_type", "lesson_type_id"),
            ("coach", "coach_id"),
            ("venue", "venue_id"),
        ):
            if options[option]:
                kwargs[key] = _uuid(
                    options[option],
                    f"--{option.replace('_', '-')}",
                )
        if options["weekday"] is not None:
            kwargs["weekday"] = options["weekday"]
        if options["start_time"]:
            kwargs["start_time"] = _time(options["start_time"])
        if options["duration_minutes"] is not None:
            kwargs["duration_minutes"] = options["duration_minutes"]
        if (
            options["minimum_attendees"] is not None
            and options["clear_minimum_attendees"]
        ):
            raise CommandError(
                "Use either --minimum-attendees or "
                "--clear-minimum-attendees, not both."
            )
        if options["minimum_attendees"] is not None:
            kwargs["minimum_attendees_override"] = options[
                "minimum_attendees"
            ]
        elif options["clear_minimum_attendees"]:
            kwargs["minimum_attendees_override"] = None

        replacement = version_schedule_template(
            template_id=_uuid(options["template"], "--template"),
            effective_from=_date(options["effective_from"]),
            actor=resolve_actor(options["actor"]),
            now=timezone.now(),
            **kwargs,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Schedule template version created: {replacement.id}"
            )
        )
