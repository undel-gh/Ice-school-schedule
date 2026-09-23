from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.utils import timezone

from core.management import command_errors
from core.time import school_date
from scheduling.models import ScheduleTemplate
from scheduling.services import generate_lessons


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Generate concrete lessons from one template or all active templates."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--template")
        mode.add_argument("--all-active", action="store_true")
        parser.add_argument("--from-date")
        parser.add_argument("--until-date")
        parser.add_argument("--horizon-days", type=int, default=60)

    @command_errors
    def handle(self, *args, **options):
        from_date = (
            _date(options["from_date"], "--from-date")
            if options["from_date"]
            else school_date(timezone.now())
        )
        if options["until_date"]:
            until_date = _date(options["until_date"], "--until-date")
        else:
            horizon_days = options["horizon_days"]
            if horizon_days < 0:
                raise CommandError("--horizon-days must be non-negative.")
            until_date = from_date + timedelta(days=horizon_days)

        if options["template"]:
            try:
                template_ids = [UUID(options["template"])]
            except ValueError as exc:
                raise CommandError("--template must be a UUID.") from exc
        else:
            template_ids = list(
                ScheduleTemplate.objects.filter(
                    is_active=True,
                    valid_from__lte=until_date,
                )
                .filter(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gte=from_date)
                )
                .order_by("id")
                .values_list("id", flat=True)
            )

        total = 0
        errors = []
        for template_id in template_ids:
            try:
                lessons = generate_lessons(
                    template_id=template_id,
                    from_date=from_date,
                    until_date=until_date,
                    actor=None,
                )
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors.append(f"{template_id}: {exc}")
                continue
            total += len(lessons)

        self.stdout.write(
            self.style.SUCCESS(
                f"Templates processed: {len(template_ids)}; "
                f"lessons returned: {total}; errors: {len(errors)}"
            )
        )
        if errors:
            raise CommandError(
                "Some schedule templates failed: " + " | ".join(errors)
            )
