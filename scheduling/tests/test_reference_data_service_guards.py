from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.models import CoachProfile
from core.time import school_date
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.reference_data_services import (
    create_lesson_type,
    create_venue,
    update_lesson_type,
    update_venue,
)
from scheduling.services import (
    create_schedule_template,
    generate_lessons,
    publish_lesson,
    version_schedule_template,
)

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="reference-service-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def scheduling_context(db):
    coach_user = User.objects.create_user(
        username="reference-service-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Тренер",
    )
    group = TrainingGroup.objects.create(
        code="reference-service-group",
        name="Группа",
    )
    lesson_type = LessonType.objects.create(
        code="reference-service-ice",
        name="Лёд",
        subscription_category="ice",
    )
    venue = Venue.objects.create(
        code="reference-service-venue",
        name="Лёд A",
        address="Rīga",
        floor="1",
    )
    return group, coach, lesson_type, venue


@pytest.mark.django_db
def test_reference_data_services_enforce_model_permissions():
    actor = User.objects.create_user(
        username="reference-service-no-permissions",
        password="test",
    )
    lesson_type = LessonType.objects.create(
        code="existing-type",
        name="Существующий тип",
        subscription_category="ice",
    )
    venue = Venue.objects.create(
        code="existing-venue",
        name="Существующая площадка",
    )

    with pytest.raises(PermissionDenied):
        create_lesson_type(
            code="forbidden-type",
            name="Запрещённый тип",
            subscription_category="ice",
            is_active=True,
            actor=actor,
        )
    with pytest.raises(PermissionDenied):
        update_lesson_type(
            lesson_type_id=lesson_type.id,
            code="renamed-type",
            name="Переименованный тип",
            subscription_category="ice",
            is_active=True,
            actor=actor,
            now=timezone.now(),
        )
    with pytest.raises(PermissionDenied):
        create_venue(
            code="forbidden-venue",
            name="Запрещённая площадка",
            address="",
            floor="",
            is_active=True,
            actor=actor,
        )
    with pytest.raises(PermissionDenied):
        update_venue(
            venue_id=venue.id,
            code="renamed-venue",
            name="Переименованная площадка",
            address="",
            floor="",
            is_active=True,
            actor=actor,
            now=timezone.now(),
        )

    lesson_type.refresh_from_db()
    venue.refresh_from_db()
    assert lesson_type.code == "existing-type"
    assert venue.code == "existing-venue"
    assert not LessonType.objects.filter(code="forbidden-type").exists()
    assert not Venue.objects.filter(code="forbidden-venue").exists()


@pytest.mark.django_db
def test_lesson_type_category_cannot_change_after_any_lesson_exists(
    manager,
    scheduling_context,
):
    group, coach, lesson_type, venue = scheduling_context
    now = timezone.now()
    starts_at = now - timedelta(days=2)
    Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="Создайте новый тип занятия"):
        update_lesson_type(
            lesson_type_id=lesson_type.id,
            code=lesson_type.code,
            name=lesson_type.name,
            subscription_category="hall",
            is_active=True,
            actor=manager,
            now=now,
        )

    lesson_type.refresh_from_db()
    assert lesson_type.subscription_category == "ice"


@pytest.mark.django_db
def test_reference_deactivation_uses_supplied_now(manager, scheduling_context):
    group, coach, lesson_type, venue = scheduling_context
    actual_now = timezone.now()
    supplied_now = actual_now - timedelta(days=10)
    starts_at = supplied_now + timedelta(days=1)
    assert starts_at < actual_now
    Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="будущими неотменёнными занятиями"):
        update_venue(
            venue_id=venue.id,
            code=venue.code,
            name=venue.name,
            address=venue.address,
            floor=venue.floor,
            is_active=False,
            actor=manager,
            now=supplied_now,
        )

    venue.refresh_from_db()
    assert venue.is_active is True


@pytest.mark.django_db
def test_create_schedule_template_rejects_inactive_reference_data(
    manager,
    scheduling_context,
):
    group, coach, lesson_type, venue = scheduling_context
    today = timezone.localdate()

    lesson_type.is_active = False
    lesson_type.save(update_fields=["is_active"])
    with pytest.raises(ValidationError, match="Inactive lesson types"):
        create_schedule_template(
            group_id=group.id,
            lesson_type_id=lesson_type.id,
            coach_id=coach.id,
            venue_id=venue.id,
            weekday=today.weekday(),
            start_time=time(10, 0),
            duration_minutes=60,
            valid_from=today,
            valid_until=today + timedelta(days=30),
            minimum_attendees_override=None,
            actor=manager,
        )

    lesson_type.is_active = True
    lesson_type.save(update_fields=["is_active"])
    venue.is_active = False
    venue.save(update_fields=["is_active"])
    with pytest.raises(ValidationError, match="Inactive venues"):
        create_schedule_template(
            group_id=group.id,
            lesson_type_id=lesson_type.id,
            coach_id=coach.id,
            venue_id=venue.id,
            weekday=today.weekday(),
            start_time=time(10, 0),
            duration_minutes=60,
            valid_from=today,
            valid_until=today + timedelta(days=30),
            minimum_attendees_override=None,
            actor=manager,
        )


@pytest.mark.django_db
def test_version_schedule_template_rejects_inactive_target_reference_data(
    manager,
    scheduling_context,
):
    group, coach, lesson_type, venue = scheduling_context
    now = timezone.now()
    today = school_date(now)
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=today.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=today,
        valid_until=today + timedelta(days=30),
    )
    inactive_type = LessonType.objects.create(
        code="inactive-version-type",
        name="Неактивный тип",
        subscription_category="ice",
        is_active=False,
    )
    inactive_venue = Venue.objects.create(
        code="inactive-version-venue",
        name="Неактивная площадка",
        is_active=False,
    )

    with pytest.raises(ValidationError, match="Inactive lesson types"):
        version_schedule_template(
            template_id=template.id,
            effective_from=today + timedelta(days=7),
            actor=manager,
            now=now,
            lesson_type_id=inactive_type.id,
        )
    with pytest.raises(ValidationError, match="Inactive venues"):
        version_schedule_template(
            template_id=template.id,
            effective_from=today + timedelta(days=7),
            actor=manager,
            now=now,
            venue_id=inactive_venue.id,
        )

    template.refresh_from_db()
    assert template.valid_until == today + timedelta(days=30)


@pytest.mark.django_db
def test_generation_rechecks_lesson_type_and_venue_activity(
    scheduling_context,
):
    group, coach, lesson_type, venue = scheduling_context
    today = timezone.localdate()
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=today.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=today,
        valid_until=today + timedelta(days=30),
    )

    LessonType.objects.filter(pk=lesson_type.id).update(is_active=False)
    with pytest.raises(ValidationError, match="Inactive lesson types"):
        generate_lessons(
            template_id=template.id,
            from_date=today,
            until_date=today + timedelta(days=7),
        )

    LessonType.objects.filter(pk=lesson_type.id).update(is_active=True)
    Venue.objects.filter(pk=venue.id).update(is_active=False)
    with pytest.raises(ValidationError, match="Inactive venues"):
        generate_lessons(
            template_id=template.id,
            from_date=today,
            until_date=today + timedelta(days=7),
        )

    assert not Lesson.objects.filter(source_template=template).exists()


@pytest.mark.django_db
def test_publish_rechecks_lesson_type_and_venue_activity(
    scheduling_context,
):
    group, coach, lesson_type, venue = scheduling_context
    now = timezone.now()
    starts_at = now + timedelta(days=2)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
    )

    LessonType.objects.filter(pk=lesson_type.id).update(is_active=False)
    with pytest.raises(ValidationError, match="inactive lesson type"):
        publish_lesson(lesson_id=lesson.id, actor=None, now=now)

    LessonType.objects.filter(pk=lesson_type.id).update(is_active=True)
    Venue.objects.filter(pk=venue.id).update(is_active=False)
    with pytest.raises(ValidationError, match="inactive venue"):
        publish_lesson(lesson_id=lesson.id, actor=None, now=now)

    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.DRAFT
