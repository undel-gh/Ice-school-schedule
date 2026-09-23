from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.management import command_errors, resolve_actor
from subscriptions.services import grant_administrative_makeup


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Grant an administrative make-up entitlement backed by an allowance."

    def add_arguments(self, parser):
        parser.add_argument("--allowance", required=True)
        parser.add_argument("--source-lesson", required=True)
        parser.add_argument("--valid-from", required=True)
        parser.add_argument("--valid-until", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--target-lesson")
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            allowance_id = UUID(options["allowance"])
            source_lesson_id = UUID(options["source_lesson"])
            target_lesson_id = (
                UUID(options["target_lesson"])
                if options["target_lesson"]
                else None
            )
        except ValueError as exc:
            raise CommandError(
                "--allowance, --source-lesson and --target-lesson must be UUIDs."
            ) from exc

        entitlement = grant_administrative_makeup(
            source_subscription_allowance_id=allowance_id,
            source_lesson_id=source_lesson_id,
            valid_from=_date(options["valid_from"], "--valid-from"),
            valid_until=_date(options["valid_until"], "--valid-until"),
            actor=resolve_actor(options["actor"]),
            reason=options["reason"],
            target_lesson_id=target_lesson_id,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Administrative make-up granted: {entitlement.id}"
            )
        )
