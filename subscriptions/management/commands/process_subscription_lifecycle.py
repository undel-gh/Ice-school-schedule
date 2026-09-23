from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.management import command_errors
from core.time import school_date

from subscriptions.services import process_subscription_lifecycle


class Command(BaseCommand):
    help = (
        "Emit idempotent subscription and make-up lifecycle audit events "
        "for a business date."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="as_of",
            help="Business date in YYYY-MM-DD format. Defaults to local date.",
        )

    @command_errors
    def handle(self, *args, **options):
        raw_date = options["as_of"]
        if raw_date:
            try:
                as_of = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CommandError(
                    "--date must use YYYY-MM-DD format."
                ) from exc
        else:
            as_of = school_date(timezone.now())

        counts = process_subscription_lifecycle(
            as_of=as_of,
            actor=None,
        )
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Processed lifecycle for {as_of.isoformat()}: "
                    f"activated={counts['activated']}, "
                    f"expired={counts['expired']}, "
                    f"expired_with_unused="
                    f"{counts['expired_with_unused']}, "
                    f"makeup_expired={counts['makeup_expired']}"
                )
            )
        )
