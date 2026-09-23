from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from scheduling.services import publish_daily_schedule


class Command(BaseCommand):
    help = "Publish DRAFT lessons for one school date."

    def add_arguments(self, parser):
        parser.add_argument("--date", dest="school_date")

    def handle(self, *args, **options):
        raw = options["school_date"]
        if raw:
            try:
                school_date = date.fromisoformat(raw)
            except ValueError as exc:
                raise CommandError("--date must use YYYY-MM-DD format.") from exc
        else:
            school_date = timezone.localdate()

        lessons = publish_daily_schedule(
            school_date=school_date,
            now=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Published lessons: {len(lessons)}")
        )
