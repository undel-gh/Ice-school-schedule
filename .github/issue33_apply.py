from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


services_path = Path("scheduling/services.py")
services = services_path.read_text(encoding="utf-8")

services = replace_once(
    services,
    """class LessonGenerationConflict:\n    template_id: UUID\n    conflicting_lesson_id: UUID\n    expected_lesson_type_id: UUID\n    conflicting_lesson_type_id: UUID\n    starts_at: datetime\n    ends_at: datetime\n""",
    """class LessonGenerationConflict:\n    template_id: UUID\n    conflicting_lesson_id: UUID\n    expected_lesson_type_id: UUID\n    conflicting_lesson_type_id: UUID\n    starts_at: datetime\n    ends_at: datetime\n    conflict_resource: str | None = None\n""",
    label="generation conflict dataclass",
)

services = replace_once(
    services,
    """            current += timedelta(days=1)\n            continue\n\n        if not coach.is_active:\n""",
    """            current += timedelta(days=1)\n            continue\n\n        resource_conflicting_lesson = (\n            Lesson.objects.filter(\n                starts_at__lt=ends_at,\n                ends_at__gt=starts_at,\n            )\n            .exclude(group_id=template.group_id)\n            .exclude(status=Lesson.Status.CANCELLED)\n            .filter(\n                Q(coach_id=template.coach_id)\n                | Q(venue_id=template.venue_id)\n            )\n            .order_by(\"starts_at\", \"id\")\n            .first()\n        )\n        if resource_conflicting_lesson is not None:\n            same_coach = (\n                resource_conflicting_lesson.coach_id == template.coach_id\n            )\n            same_venue = (\n                resource_conflicting_lesson.venue_id == template.venue_id\n            )\n            if same_coach and same_venue:\n                conflict_resource = \"coach_and_venue\"\n            elif same_coach:\n                conflict_resource = \"coach\"\n            else:\n                conflict_resource = \"venue\"\n\n            conflict = LessonGenerationConflict(\n                template_id=template.id,\n                conflicting_lesson_id=resource_conflicting_lesson.id,\n                expected_lesson_type_id=template.lesson_type_id,\n                conflicting_lesson_type_id=(\n                    resource_conflicting_lesson.lesson_type_id\n                ),\n                starts_at=starts_at,\n                ends_at=ends_at,\n                conflict_resource=conflict_resource,\n            )\n            conflicts.append(conflict)\n            expected_starts_at = starts_at.isoformat()\n            if not event_exists_with_payload(\n                event_type=\"LessonGenerationConflict\",\n                aggregate_type=\"ScheduleTemplate\",\n                aggregate_id=template.id,\n                payload_filters={\n                    \"expected_starts_at\": expected_starts_at,\n                    \"conflicting_lesson_id\": str(\n                        resource_conflicting_lesson.id\n                    ),\n                },\n            ):\n                record_event(\n                    event_type=\"LessonGenerationConflict\",\n                    actor=actor,\n                    aggregate_type=\"ScheduleTemplate\",\n                    aggregate_id=template.id,\n                    payload={\n                        \"conflicting_lesson_id\": str(\n                            resource_conflicting_lesson.id\n                        ),\n                        \"expected_lesson_type_id\": str(\n                            template.lesson_type_id\n                        ),\n                        \"conflicting_lesson_type_id\": str(\n                            resource_conflicting_lesson.lesson_type_id\n                        ),\n                        \"conflict_resource\": conflict_resource,\n                        \"expected_starts_at\": expected_starts_at,\n                        \"expected_ends_at\": ends_at.isoformat(),\n                        \"conflicting_starts_at\": (\n                            resource_conflicting_lesson.starts_at.isoformat()\n                        ),\n                        \"conflicting_ends_at\": (\n                            resource_conflicting_lesson.ends_at.isoformat()\n                        ),\n                    },\n                )\n            current += timedelta(days=1)\n            continue\n\n        if not coach.is_active:\n""",
    label="generation resource guard",
)

services = replace_once(
    services,
    """    source_ref = Lesson.objects.only(\"group_id\").get(pk=lesson_id)\n    TrainingGroup.objects.select_for_update().get(pk=source_ref.group_id)\n    source = (\n""",
    """    source_ref = Lesson.objects.only(\n        \"group_id\",\n        \"coach_id\",\n        \"venue_id\",\n    ).get(pk=lesson_id)\n    TrainingGroup.objects.select_for_update().get(pk=source_ref.group_id)\n    CoachProfile.objects.select_for_update().get(pk=source_ref.coach_id)\n    Venue.objects.select_for_update().get(pk=source_ref.venue_id)\n    source = (\n""",
    label="reschedule resource locks",
)

group_conflict = """    conflicting_lesson = (\n        Lesson.objects.filter(\n            group_id=source.group_id,\n            starts_at__lt=new_ends_at,\n            ends_at__gt=new_starts_at,\n        )\n        .exclude(pk=source.id)\n        .exclude(status=Lesson.Status.CANCELLED)\n        .order_by(\"starts_at\", \"id\")\n        .first()\n    )\n    if conflicting_lesson is not None:\n        raise ValidationError(\n            {\n                \"new_starts_at\": (\n                    \"Replacement interval overlaps another non-cancelled \"\n                    \"lesson of this group: \"\n                    f\"{conflicting_lesson.id}.\"\n                )\n            }\n        )\n"""
resource_conflicts = group_conflict + """\n    conflicting_coach_lesson = (\n        Lesson.objects.filter(\n            coach_id=source.coach_id,\n            starts_at__lt=new_ends_at,\n            ends_at__gt=new_starts_at,\n        )\n        .exclude(pk=source.id)\n        .exclude(status=Lesson.Status.CANCELLED)\n        .order_by(\"starts_at\", \"id\")\n        .first()\n    )\n    if conflicting_coach_lesson is not None:\n        raise ValidationError(\n            {\n                \"new_starts_at\": (\n                    \"The selected coach has another non-cancelled lesson \"\n                    \"overlapping this time: \"\n                    f\"{conflicting_coach_lesson.id}.\"\n                )\n            }\n        )\n\n    conflicting_venue_lesson = (\n        Lesson.objects.filter(\n            venue_id=source.venue_id,\n            starts_at__lt=new_ends_at,\n            ends_at__gt=new_starts_at,\n        )\n        .exclude(pk=source.id)\n        .exclude(status=Lesson.Status.CANCELLED)\n        .order_by(\"starts_at\", \"id\")\n        .first()\n    )\n    if conflicting_venue_lesson is not None:\n        raise ValidationError(\n            {\n                \"new_starts_at\": (\n                    \"The selected venue has another non-cancelled lesson \"\n                    \"overlapping this time: \"\n                    f\"{conflicting_venue_lesson.id}.\"\n                )\n            }\n        )\n"""
services = replace_once(
    services,
    group_conflict,
    resource_conflicts,
    label="reschedule resource conflicts",
)
services_path.write_text(services, encoding="utf-8")


ui_path = Path("core/ui_messages.py")
ui = ui_path.read_text(encoding="utf-8")
coach_pattern = """    (\n        re.compile(r'^The selected coach has another non-cancelled lesson overlapping this time: (?P<id>[0-9a-f-]+)\\.$'),\n        lambda m: (\n            f\"У выбранного тренера уже есть {_lesson_nominative(m.group('id'))}, \"\n            \"пересекающееся по времени.\"\n        ),\n    ),\n"""
venue_pattern = coach_pattern + """    (\n        re.compile(r'^The selected venue has another non-cancelled lesson overlapping this time: (?P<id>[0-9a-f-]+)\\.$'),\n        lambda m: (\n            f\"На выбранной площадке уже есть {_lesson_nominative(m.group('id'))}, \"\n            \"пересекающееся по времени.\"\n        ),\n    ),\n"""
ui = replace_once(ui, coach_pattern, venue_pattern, label="venue localization")
ui_path.write_text(ui, encoding="utf-8")


coverage_path = Path("core/tests/test_error_localization_coverage.py")
coverage = coverage_path.read_text(encoding="utf-8")
coach_example = """    \"The selected coach has another non-cancelled lesson overlapping this time: {}.\": (\n        \"The selected coach has another non-cancelled lesson overlapping this time: 11111111-1111-1111-1111-111111111111.\"\n    ),\n"""
venue_example = coach_example + """    \"The selected venue has another non-cancelled lesson overlapping this time: {}.\": (\n        \"The selected venue has another non-cancelled lesson overlapping this time: 11111111-1111-1111-1111-111111111111.\"\n    ),\n"""
coverage = replace_once(
    coverage,
    coach_example,
    venue_example,
    label="venue localization coverage",
)
coverage_path.write_text(coverage, encoding="utf-8")
