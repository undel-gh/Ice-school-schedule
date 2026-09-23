from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.services import reject_medical_absence
from core.management import command_errors, resolve_actor


class Command(BaseCommand):
    help = "Reject a pending medical absence justification."

    def add_arguments(self, parser):
        parser.add_argument("--justification", required=True)
        parser.add_argument("--actor", required=True)

    @command_errors
    def handle(self, *args, **options):
        try:
            justification_id = UUID(options["justification"])
        except ValueError as exc:
            raise CommandError("--justification must be a UUID.") from exc

        justification = reject_medical_absence(
            justification_id=justification_id,
            actor=resolve_actor(options["actor"]),
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Medical justification {justification.id}: "
                f"{justification.status}"
            )
        )
