from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile
from audit.models import AuditEvent
from scheduling.forms import ManagerScheduleTemplateForm
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="reference-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_manager_creates_and_updates_lesson_type(client, manager):
    client.force_login(manager)

    created = client.post(
        reverse("school_scheduling:lesson_type_create"),
        {
            "code": "ice",
            "name": "Лёд",
            "subscription_category": "ice",
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    lesson_type = LessonType.objects.get(code="ice")
    assert lesson_type.name == "Лёд"
    assert lesson_type.subscription_category == "ice"
    assert AuditEvent.objects.filter(
        event_type="LessonTypeCreated",
        aggregate_type="LessonType",
        aggregate_id=lesson_type.id,
    ).exists()

    updated = client.post(
        reverse(
            "school_scheduling:lesson_type_edit",
            kwargs={"lesson_type_id": lesson_type.id},
        ),
        {
            "code": "ofp",
            "name": "ОФП",
            "subscription_category": "hall",
            "is_active": "on",
        },
    )
    assert updated.status_code == 302
    lesson_type.refresh_from_db()
    assert lesson_type.code == "ofp"
    assert lesson_type.name == "ОФП"
    assert lesson_type.subscription_category == "hall"
    assert AuditEvent.objects.filter(
        event_type="LessonTypeChanged",
        aggregate_id=lesson_type.id,
    ).exists()

    response = client.get(reverse("school_scheduling:lesson_types"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "ОФП" in body
    assert "Зал" in body


@pytest.mark.django_db
def test_manager_creates_and_updates_venue(client, manager):
    client.force_login(manager)

    created = client.post(
        reverse("school_scheduling:venue_create"),
        {
            "code": "dance-hall",
            "name": "Зал хореографии",
            "address": "Рига, Тестовая улица, 1",
            "floor": "2",
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    venue = Venue.objects.get(code="dance-hall")
    assert venue.name == "Зал хореографии"
    assert venue.address == "Рига, Тестовая улица, 1"
    assert venue.floor == "2"
    created_event = AuditEvent.objects.get(
        event_type="VenueCreated",
        aggregate_type="Venue",
        aggregate_id=venue.id,
    )
    assert created_event.payload["floor"] == "2"

    updated = client.post(
        reverse("school_scheduling:venue_edit", kwargs={"venue_id": venue.id}),
        {
            "code": "dance-hall",
            "name": "Зал хореографии A",
            "address": "Рига, Новый адрес, 2",
            "floor": "1A",
            "is_active": "on",
        },
    )
    assert updated.status_code == 302
    venue.refresh_from_db()
    assert venue.name == "Зал хореографии A"
    assert venue.address == "Рига, Новый адрес, 2"
    assert venue.floor == "1A"
    changed_event = AuditEvent.objects.get(
        event_type="VenueChanged",
        aggregate_id=venue.id,
    )
    assert changed_event.payload["current"]["floor"] == "1A"

    response = client.get(reverse("school_scheduling:venues"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Зал хореографии A" in body
    assert "Рига, Новый адрес, 2" in body
    assert "этаж 1A" in body


@pytest.mark.django_db
def test_schedule_template_venue_choice_shows_location_details():
    Venue.objects.create(
        code="hall-a",
        name="Зал хореографии",
        address="Brīvības iela 123, Rīga",
        floor="2",
    )

    form = ManagerScheduleTemplateForm()
    labels = [label for _value, label in form.fields["venue"].choices]

    assert "Зал хореографии — Brīvības iela 123, Rīga · этаж 2" in labels


@pytest.mark.django_db
def test_lesson_type_cannot_be_deactivated_while_active_template_uses_it(
    client,
    manager,
):
    coach_user = User.objects.create_user(username="reference-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Тренер")
    group = TrainingGroup.objects.create(code="reference-group", name="Группа")
    lesson_type = LessonType.objects.create(
        code="reference-ice",
        name="Лёд",
        subscription_category="ice",
    )
    venue = Venue.objects.create(code="reference-arena", name="Арена")
    ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=0,
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=timezone.localdate(),
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "school_scheduling:lesson_type_edit",
            kwargs={"lesson_type_id": lesson_type.id},
        ),
        {
            "code": lesson_type.code,
            "name": lesson_type.name,
            "subscription_category": "ice",
            "is_active": "",
        },
    )

    lesson_type.refresh_from_db()
    assert response.status_code == 200
    assert lesson_type.is_active is True
    assert "активным шаблоном расписания" in response.content.decode()


@pytest.mark.django_db
def test_venue_cannot_be_deactivated_while_future_lesson_uses_it(client, manager):
    coach_user = User.objects.create_user(username="venue-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Тренер")
    group = TrainingGroup.objects.create(code="venue-group", name="Группа")
    lesson_type = LessonType.objects.create(
        code="venue-ice",
        name="Лёд",
        subscription_category="ice",
    )
    venue = Venue.objects.create(
        code="future-arena",
        name="Будущая арена",
        floor="1",
    )
    starts_at = timezone.now() + timedelta(days=2)
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
    client.force_login(manager)

    response = client.post(
        reverse("school_scheduling:venue_edit", kwargs={"venue_id": venue.id}),
        {
            "code": venue.code,
            "name": venue.name,
            "address": venue.address,
            "floor": venue.floor,
            "is_active": "",
        },
    )

    venue.refresh_from_db()
    assert response.status_code == 200
    assert venue.is_active is True
    assert venue.floor == "1"
    assert "будущими неотменёнными занятиями" in response.content.decode()


@pytest.mark.django_db
def test_reference_pages_require_model_permissions(client):
    user = User.objects.create_user(username="reference-no-permissions", password="test")
    client.force_login(user)

    lesson_types = client.get(reverse("school_scheduling:lesson_types"))
    venues = client.get(reverse("school_scheduling:venues"))

    assert lesson_types.status_code == 403
    assert venues.status_code == 403
