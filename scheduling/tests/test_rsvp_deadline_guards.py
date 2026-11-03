from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile
from core.time import school_timezone
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from scheduling.services import publish_lesson, reschedule_lesson


User = get_user_model()


@pytest.fixture
def deadline_context(db):
    manager = User.objects.create_superuser(
        username="deadline-manager",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=User.objects.create_user(
            username="deadline-coach",
            password="test",
        ),
        display_name="Deadline Coach",
    )
    group = TrainingGroup.objects.create(
        code="deadline-group",
        name="Deadline Group",
    )
    venue = Venue.objects.create(
        code="deadline-venue",
        name="Deadline Venue",
    )
    lesson_type = LessonType.objects.create(
        code="deadline-ice",
        name="Deadline ICE",
        subscription_category="ice",
    )
    return {
        "manager": manager,
        "coach": coach,
        "group": group,
        "venue": venue,
        "lesson_type": lesson_type,
    }


def _lesson(
    ctx,
    *,
    starts_at,
    rsvp_deadline,
    decision_deadline,
) -> Lesson:
    return Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=rsvp_deadline,
        decision_deadline=decision_deadline,
        status=Lesson.Status.DRAFT,
    )


@pytest.mark.django_db
def test_publish_rejects_lesson_after_rsvp_deadline(deadline_context):
    ctx = deadline_context
    now = timezone.now()
    starts_at = now + timedelta(hours=1)
    lesson = _lesson(
        ctx,
        starts_at=starts_at,
        rsvp_deadline=now - timedelta(minutes=1),
        decision_deadline=now + timedelta(minutes=30),
    )

    with pytest.raises(
        ValidationError,
        match="Lesson cannot be published after its RSVP deadline",
    ):
        publish_lesson(
            lesson_id=lesson.id,
            actor=ctx["manager"],
            now=now,
        )

    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.DRAFT
    assert lesson.published_at is None
    assert lesson.roster_entries.count() == 0


@pytest.mark.django_db
def test_publish_allows_exact_rsvp_deadline(deadline_context):
    ctx = deadline_context
    now = timezone.now()
    starts_at = now + timedelta(hours=2)
    lesson = _lesson(
        ctx,
        starts_at=starts_at,
        rsvp_deadline=now,
        decision_deadline=now + timedelta(hours=1),
    )

    published = publish_lesson(
        lesson_id=lesson.id,
        actor=ctx["manager"],
        now=now,
    )

    assert published.status == Lesson.Status.RSVP_OPEN
    assert published.published_at == now


@pytest.mark.django_db
@pytest.mark.parametrize("minutes_until_start", [90, 120])
def test_reschedule_rejects_when_replacement_rsvp_window_is_not_future(
    deadline_context,
    minutes_until_start,
):
    ctx = deadline_context
    now = timezone.now()
    source_start = now + timedelta(days=1)
    source = _lesson(
        ctx,
        starts_at=source_start,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
    )
    target_start = now + timedelta(minutes=minutes_until_start)

    with pytest.raises(
        ValidationError,
        match="Replacement lesson RSVP deadline must remain in the future",
    ):
        reschedule_lesson(
            lesson_id=source.id,
            new_starts_at=target_start,
            new_ends_at=target_start + timedelta(hours=1),
            actor=ctx["manager"],
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=now,
        )

    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None
    assert Lesson.objects.count() == 1


@pytest.mark.django_db
def test_reschedule_keeps_future_rsvp_and_decision_deadlines(deadline_context):
    ctx = deadline_context
    now = timezone.now()
    source_start = now + timedelta(days=1)
    source = _lesson(
        ctx,
        starts_at=source_start,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
    )
    target_start = now + timedelta(hours=3)

    replacement = reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=target_start,
        new_ends_at=target_start + timedelta(hours=1),
        actor=ctx["manager"],
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=now,
    )

    assert replacement.rsvp_deadline == target_start - timedelta(hours=2)
    assert replacement.decision_deadline == target_start - timedelta(hours=1)
    assert replacement.rsvp_deadline > now
    assert replacement.decision_deadline > now


@pytest.mark.django_db
def test_manager_explains_late_publish_rejection(client, deadline_context):
    ctx = deadline_context
    now = timezone.now()
    starts_at = now + timedelta(hours=1)
    lesson = _lesson(
        ctx,
        starts_at=starts_at,
        rsvp_deadline=now - timedelta(minutes=1),
        decision_deadline=now + timedelta(minutes=30),
    )
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_publish",
            kwargs={"lesson_id": lesson.id},
        ),
        follow=True,
    )

    assert response.status_code == 200
    assert "Нельзя опубликовать занятие: срок ответа уже истёк." in response.content.decode()
    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.DRAFT


@pytest.mark.django_db
def test_manager_explains_near_term_reschedule_rejection(client, deadline_context):
    ctx = deadline_context
    now = timezone.now()
    source_start = now + timedelta(days=1)
    source = _lesson(
        ctx,
        starts_at=source_start,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
    )
    target_start = now + timedelta(minutes=90)
    target_end = target_start + timedelta(hours=1)
    local_target = target_start.astimezone(school_timezone())
    local_end = target_end.astimezone(school_timezone())
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_reschedule",
            kwargs={"lesson_id": source.id},
        ),
        {
            "new_starts_at": local_target.strftime("%Y-%m-%dT%H:%M"),
            "new_ends_at": local_end.strftime("%Y-%m-%dT%H:%M"),
            "reason": Lesson.CancellationReason.ADMINISTRATIVE,
        },
        follow=True,
    )

    assert response.status_code == 200
    assert (
        "После переноса срок ответа должен оставаться в будущем. "
        "Выберите более позднее время начала занятия."
        in response.content.decode()
    )
    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None
