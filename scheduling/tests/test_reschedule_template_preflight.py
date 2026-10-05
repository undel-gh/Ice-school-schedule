from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models import CoachProfile
from core.time import make_school_aware
from ice_school.workflows import reschedule_lesson_with_entitlements
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import skip_template_occurrence

User = get_user_model()


@pytest.fixture
def reschedule_context(db):
    manager = User.objects.create_user(
        username="reschedule-template-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(
        username="reschedule-template-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Template Coach",
    )
    group = TrainingGroup.objects.create(
        code="reschedule-template-group",
        name="Template Group",
    )
    venue = Venue.objects.create(
        code="reschedule-template-venue",
        name="Template Venue",
    )
    ice = LessonType.objects.create(
        code="reschedule-template-ice",
        name="Лёд",
        subscription_category="ice",
    )
    hall = LessonType.objects.create(
        code="reschedule-template-hall",
        name="Зал",
        subscription_category="hall",
    )
    now = make_school_aware(datetime(2099, 1, 1, 12, 0))
    source_start = make_school_aware(datetime(2099, 1, 5, 12, 0))
    source = Lesson.objects.create(
        group=group,
        lesson_type=ice,
        coach=coach,
        venue=venue,
        starts_at=source_start,
        ends_at=source_start + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    target_date = date(2099, 1, 12)
    return {
        "manager": manager,
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
        "hall": hall,
        "source": source,
        "now": now,
        "target_date": target_date,
    }


def _template(ctx, *, lesson_type):
    return ScheduleTemplate.objects.create(
        group=ctx["group"],
        lesson_type=lesson_type,
        coach=ctx["coach"],
        venue=ctx["venue"],
        weekday=ctx["target_date"].weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=ctx["target_date"],
        valid_until=ctx["target_date"],
        is_active=True,
    )


def _target_interval(ctx):
    starts_at = make_school_aware(
        datetime.combine(ctx["target_date"], time(18, 30))
    )
    return starts_at, starts_at + timedelta(hours=1)


@pytest.mark.django_db
def test_reschedule_rejects_unmaterialized_cross_type_template_occurrence(
    reschedule_context,
):
    ctx = reschedule_context
    _template(ctx, lesson_type=ctx["hall"])
    new_starts_at, new_ends_at = _target_interval(ctx)

    with pytest.raises(ValidationError) as exc_info:
        reschedule_lesson_with_entitlements(
            lesson_id=ctx["source"].id,
            new_starts_at=new_starts_at,
            new_ends_at=new_ends_at,
            actor=ctx["manager"],
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=ctx["now"],
        )

    assert "unmaterialized active schedule template occurrence" in str(
        exc_info.value
    )
    ctx["source"].refresh_from_db()
    assert ctx["source"].status == Lesson.Status.DRAFT
    assert ctx["source"].replacement_lesson_id is None
    assert Lesson.objects.filter(replaced_lesson=ctx["source"]).count() == 0


@pytest.mark.django_db
def test_reschedule_allows_unmaterialized_same_type_template_occurrence(
    reschedule_context,
):
    ctx = reschedule_context
    _template(ctx, lesson_type=ctx["ice"])
    new_starts_at, new_ends_at = _target_interval(ctx)

    replacement = reschedule_lesson_with_entitlements(
        lesson_id=ctx["source"].id,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        actor=ctx["manager"],
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=ctx["now"],
    )

    assert replacement.lesson_type_id == ctx["ice"].id
    assert replacement.starts_at == new_starts_at
    ctx["source"].refresh_from_db()
    assert ctx["source"].replacement_lesson_id == replacement.id


@pytest.mark.django_db
def test_reschedule_allows_explicitly_skipped_cross_type_template_occurrence(
    reschedule_context,
):
    ctx = reschedule_context
    template = _template(ctx, lesson_type=ctx["hall"])
    skipped = skip_template_occurrence(
        template_id=template.id,
        occurrence_date=ctx["target_date"],
        actor=ctx["manager"],
        now=ctx["now"],
    )
    new_starts_at, new_ends_at = _target_interval(ctx)

    replacement = reschedule_lesson_with_entitlements(
        lesson_id=ctx["source"].id,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        actor=ctx["manager"],
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=ctx["now"],
    )

    assert skipped.status == Lesson.Status.CANCELLED
    assert skipped.source_template_id == template.id
    assert replacement.starts_at == new_starts_at


@pytest.mark.django_db
def test_manager_reschedule_shows_localized_unmaterialized_template_conflict(
    client,
    reschedule_context,
):
    ctx = reschedule_context
    _template(ctx, lesson_type=ctx["hall"])
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_reschedule",
            kwargs={"lesson_id": ctx["source"].id},
        ),
        {
            "new_starts_at": f"{ctx['target_date'].isoformat()}T18:30",
            "new_ends_at": f"{ctx['target_date'].isoformat()}T19:30",
            "reason": Lesson.CancellationReason.ADMINISTRATIVE,
        },
        follow=True,
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "ещё не созданным регулярным занятием другого типа" in body
    assert "явно пропустите конфликтующее занятие шаблона" in body
    ctx["source"].refresh_from_db()
    assert ctx["source"].status == Lesson.Status.DRAFT
    assert ctx["source"].replacement_lesson_id is None
