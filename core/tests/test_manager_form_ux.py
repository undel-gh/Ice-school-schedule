from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.forms import CoachProfileForm
from accounts.models import CoachProfile, Student
from core.choices import SubscriptionCategory
from scheduling.forms import ManagerScheduleTemplateForm
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.forms import ManagerSubscriptionIssueForm
from subscriptions.models import SubscriptionPeriodScheme, SubscriptionPlan


User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager-form-ux",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_one_time_form_get_explains_empty_dependencies(client, manager):
    client.force_login(manager)

    response = client.get(reverse("subscriptions:manager_one_time_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Нет активных учеников" in body
    assert "Нет доступных занятий в окне ±60 дней" in body
    assert "Показываются занятия в окне ±60 дней" in body


@pytest.mark.django_db
def test_one_time_form_get_lists_eligible_lesson(client, manager):
    student = Student.objects.create(display_name="Ученица UX")
    coach_user = User.objects.create_user(username="coach-form-ux", password="test")
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Тренер UX",
    )
    group = TrainingGroup.objects.create(code="ux-group", name="Группа UX")
    venue = Venue.objects.create(code="ux-venue", name="Лёд UX")
    lesson_type = LessonType.objects.create(
        code="ux-ice",
        name="Лёд",
        subscription_category=SubscriptionCategory.ICE,
    )
    starts_at = timezone.now() + timedelta(days=1)
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
        status=Lesson.Status.DRAFT,
    )
    client.force_login(manager)

    response = client.get(reverse("subscriptions:manager_one_time_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert str(student.id) in body
    assert str(lesson.id) in body
    assert "Группа UX" in body
    assert "Нет доступных занятий в окне ±60 дней" not in body


@pytest.mark.django_db
def test_schedule_template_form_explains_empty_reference_data():
    body = render_to_string(
        "scheduling/manager_schedule_template_form.html",
        {
            "form": ManagerScheduleTemplateForm(),
            "title": "Новый шаблон",
        },
    )

    assert "Нет активных групп" in body
    assert "Нет активных типов занятий" in body
    assert "Нет активных тренеров" in body
    assert "Нет активных площадок" in body


@pytest.mark.django_db
def test_subscription_issue_form_explains_empty_catalog():
    body = render_to_string(
        "subscriptions/manager_subscription_issue.html",
        {"form": ManagerSubscriptionIssueForm()},
    )

    assert "Нет активных учеников" in body
    assert "Нет тарифов, доступных для выдачи" in body


@pytest.mark.django_db
def test_subscription_issue_form_hides_empty_state_when_choices_exist():
    Student.objects.create(display_name="Ученица с тарифом")
    scheme = SubscriptionPeriodScheme.objects.create(
        code="ux-period",
        name="UX period",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        code="ux-plan",
        name="UX plan",
        period_scheme=scheme,
        is_active=True,
    )

    body = render_to_string(
        "subscriptions/manager_subscription_issue.html",
        {"form": ManagerSubscriptionIssueForm()},
    )

    assert str(plan.id) in body
    assert "Нет тарифов, доступных для выдачи" not in body


@pytest.mark.django_db
def test_coach_form_explains_missing_account_candidates():
    body = render_to_string(
        "accounts/manager_coach_form.html",
        {
            "form": CoachProfileForm(),
            "title": "Новый тренер",
        },
    )

    assert "Нет активных пользователей без профиля тренера" in body
