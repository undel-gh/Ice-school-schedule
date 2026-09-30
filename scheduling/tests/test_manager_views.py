from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile
from audit.models import AuditEvent
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue

User = get_user_model()


@pytest.fixture
def manager_schedule_context(db):
    manager = User.objects.create_user(
        username="schedule-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(username="manager-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Manager Coach")
    group = TrainingGroup.objects.create(code="manager-group", name="Manager Group")
    venue = Venue.objects.create(code="manager-venue", name="Manager Venue")
    lesson_type = LessonType.objects.create(
        code="manager-ice",
        name="Manager ICE",
        subscription_category="ice",
    )
    return {
        "manager": manager,
        "coach": coach,
        "group": group,
        "venue": venue,
        "lesson_type": lesson_type,
    }


@pytest.mark.django_db
def test_manager_creates_schedule_template_through_service(client, manager_schedule_context):
    ctx = manager_schedule_context
    valid_from = timezone.localdate() + timedelta(days=14)
    client.force_login(ctx["manager"])

    response = client.post(
        reverse("scheduling_manager:template_create"),
        {
            "group": str(ctx["group"].id),
            "lesson_type": str(ctx["lesson_type"].id),
            "coach": str(ctx["coach"].id),
            "venue": str(ctx["venue"].id),
            "weekday": str(valid_from.weekday()),
            "start_time": "18:00",
            "duration_minutes": "60",
            "valid_from": valid_from.isoformat(),
            "valid_until": "",
            "minimum_attendees_override": "3",
        },
    )

    assert response.status_code == 302
    template = ScheduleTemplate.objects.get(group=ctx["group"])
    assert template.lesson_type == ctx["lesson_type"]
    assert template.weekday == valid_from.weekday()
    assert template.minimum_attendees_override == 3
    assert AuditEvent.objects.filter(
        event_type="ScheduleTemplateCreated",
        aggregate_id=template.id,
    ).exists()


@pytest.mark.django_db
def test_manager_resolves_generation_conflict_with_skip(client, manager_schedule_context):
    ctx = manager_schedule_context
    occurrence_date = timezone.localdate() + timedelta(days=21)
    template = ScheduleTemplate.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        weekday=occurrence_date.weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=occurrence_date,
        valid_until=occurrence_date,
        is_active=True,
    )
    starts_at = timezone.make_aware(datetime.combine(occurrence_date, time(18, 0)))
    conflicting = Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    event = AuditEvent.objects.create(
        event_type="LessonGenerationConflict",
        actor=ctx["manager"],
        aggregate_type="ScheduleTemplate",
        aggregate_id=template.id,
        payload={
            "conflicting_lesson_id": str(conflicting.id),
            "expected_lesson_type_id": str(template.lesson_type_id),
            "conflicting_lesson_type_id": str(conflicting.lesson_type_id),
            "expected_starts_at": starts_at.isoformat(),
            "expected_ends_at": (starts_at + timedelta(hours=1)).isoformat(),
        },
    )
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:conflict_skip",
            kwargs={"event_id": event.id},
        )
    )

    assert response.status_code == 302
    skipped = Lesson.objects.get(source_template=template, starts_at=starts_at)
    assert skipped.status == Lesson.Status.CANCELLED
    assert AuditEvent.objects.filter(
        event_type="ScheduleTemplateOccurrenceSkipped",
        aggregate_id=template.id,
    ).exists()


@pytest.mark.django_db
def test_manager_publishes_draft_lesson(client, manager_schedule_context):
    ctx = manager_schedule_context
    starts_at = timezone.now() + timedelta(days=10)
    lesson = Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_publish",
            kwargs={"lesson_id": lesson.id},
        )
    )

    assert response.status_code == 302
    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.RSVP_OPEN


@pytest.mark.django_db
def test_manager_versions_schedule_template(client, manager_schedule_context):
    ctx = manager_schedule_context
    valid_from = timezone.localdate() + timedelta(days=14)
    effective_from = valid_from + timedelta(days=7)
    template = ScheduleTemplate.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        weekday=valid_from.weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=valid_from,
        is_active=True,
    )
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:template_version",
            kwargs={"template_id": template.id},
        ),
        {
            "group": str(ctx["group"].id),
            "lesson_type": str(ctx["lesson_type"].id),
            "coach": str(ctx["coach"].id),
            "venue": str(ctx["venue"].id),
            "weekday": str(effective_from.weekday()),
            "start_time": "19:00",
            "duration_minutes": "75",
            "minimum_attendees_override": "2",
            "effective_from": effective_from.isoformat(),
        },
    )

    assert response.status_code == 302
    template.refresh_from_db()
    assert template.valid_until == effective_from - timedelta(days=1)
    replacement = ScheduleTemplate.objects.exclude(pk=template.id).get(
        group=ctx["group"]
    )
    assert replacement.valid_from == effective_from
    assert replacement.start_time == time(19, 0)
    assert replacement.duration_minutes == 75
    assert replacement.minimum_attendees_override == 2


@pytest.mark.django_db
def test_generation_conflict_is_resolved_when_template_occurrence_materializes(
    client,
    manager_schedule_context,
):
    ctx = manager_schedule_context
    occurrence_date = timezone.localdate() + timedelta(days=28)
    template = ScheduleTemplate.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        weekday=occurrence_date.weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=occurrence_date,
        valid_until=occurrence_date,
        is_active=True,
    )
    starts_at = timezone.make_aware(
        datetime.combine(occurrence_date, time(18, 0))
    )
    conflicting = Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.CANCELLED,
        cancelled_at=timezone.now(),
        cancelled_by=ctx["manager"],
        cancellation_reason=Lesson.CancellationReason.ADMINISTRATIVE,
    )
    Lesson.objects.create(
        source_template=template,
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    event = AuditEvent.objects.create(
        event_type="LessonGenerationConflict",
        actor=ctx["manager"],
        aggregate_type="ScheduleTemplate",
        aggregate_id=template.id,
        payload={
            "conflicting_lesson_id": str(conflicting.id),
            "expected_starts_at": starts_at.isoformat(),
            "expected_ends_at": (starts_at + timedelta(hours=1)).isoformat(),
        },
    )
    client.force_login(ctx["manager"])

    response = client.get(reverse("scheduling_manager:conflicts"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "решён" in body
    assert reverse(
        "scheduling_manager:conflict_skip",
        kwargs={"event_id": event.id},
    ) not in body


@pytest.mark.django_db
def test_manager_reschedule_datetime_local_uses_school_timezone(
    client,
    manager_schedule_context,
    settings,
):
    ctx = manager_schedule_context
    settings.TIME_ZONE = "UTC"
    settings.SCHOOL_TIME_ZONE = "Asia/Tokyo"

    source_start = timezone.now() + timedelta(days=10)
    lesson = Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=source_start,
        ends_at=source_start + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    school_target_date = (
        timezone.now().astimezone(ZoneInfo("Asia/Tokyo"))
        + timedelta(days=20)
    ).date()
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_reschedule",
            kwargs={"lesson_id": lesson.id},
        ),
        {
            "new_starts_at": f"{school_target_date.isoformat()}T18:00",
            "new_ends_at": f"{school_target_date.isoformat()}T19:00",
            "reason": Lesson.CancellationReason.ADMINISTRATIVE,
        },
    )

    assert response.status_code == 302
    lesson.refresh_from_db()
    replacement = lesson.replacement_lesson
    local_start = replacement.starts_at.astimezone(
        ZoneInfo("Asia/Tokyo")
    )
    local_end = replacement.ends_at.astimezone(
        ZoneInfo("Asia/Tokyo")
    )
    assert (local_start.hour, local_start.minute) == (18, 0)
    assert (local_end.hour, local_end.minute) == (19, 0)


@pytest.mark.django_db
def test_manager_post_checks_permission_before_lesson_lookup(client):
    import uuid

    outsider = User.objects.create_user(
        username="manager-lesson-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "scheduling_manager:lesson_publish",
            kwargs={"lesson_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 403
