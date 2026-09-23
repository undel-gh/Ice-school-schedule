from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.management import command_errors, resolve_actor
from subscriptions.services import issue_subscription


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Issue a subscription to one student."

    def add_arguments(self, parser):
        parser.add_argument("--student", required=True)
        parser.add_argument("--plan", required=True)
        parser.add_argument("--valid-from", required=True)
        parser.add_argument("--valid-until", required=True)
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            student_id = UUID(options["student"])
            plan_id = UUID(options["plan"])
        except ValueError as exc:
            raise CommandError("--student and --plan must be UUIDs.") from exc
        subscription = issue_subscription(
            student_id=student_id,
            plan_id=plan_id,
            valid_from=_date(options["valid_from"], "--valid-from"),
            valid_until=_date(options["valid_until"], "--valid-until"),
            actor=resolve_actor(options["actor"]),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Subscription issued: {subscription.id}")
        )
