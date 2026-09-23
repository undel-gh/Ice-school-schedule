from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import resolve_actor
from subscriptions.services import cancel_subscription


class Command(BaseCommand):
    help = "Cancel one subscription."

    def add_arguments(self, parser):
        parser.add_argument("--subscription", required=True)
        parser.add_argument("--actor", required=True)

    def handle(self, *args, **options):
        try:
            subscription_id = UUID(options["subscription"])
        except ValueError as exc:
            raise CommandError("--subscription must be a UUID.") from exc
        subscription = cancel_subscription(
            subscription_id=subscription_id,
            actor=resolve_actor(options["actor"]),
            at=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Subscription cancelled: {subscription.id}")
        )
