from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.management import command_errors, resolve_actor
from scheduling.services import create_group_membership


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Create a non-overlapping group membership interval."

    def add_arguments(self, parser):
        parser.add_argument("--student", required=True)
        parser.add_argument("--group", required=True)
        parser.add_argument("--starts-on", required=True)
        parser.add_argument("--ends-on")
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            student_id = UUID(options["student"])
            group_id = UUID(options["group"])
        except ValueError as exc:
            raise CommandError("--student and --group must be UUIDs.") from exc

        membership = create_group_membership(
            student_id=student_id,
            group_id=group_id,
            starts_on=_date(options["starts_on"], "--starts-on"),
            ends_on=(
                _date(options["ends_on"], "--ends-on")
                if options["ends_on"]
                else None
            ),
            actor=resolve_actor(options["actor"]),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Group membership created: {membership.id}")
        )
