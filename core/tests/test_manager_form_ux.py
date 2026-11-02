from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.forms import ModelChoiceField
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.forms import AccountInvitationForm, CoachProfileForm, StudentAccessForm
from accounts.models import CoachProfile, Student, StudentAccess
from core.choices import SubscriptionCategory
from core.templatetags.manager_forms import (
    MANAGER_CHOICE_EMPTY_STATES,
    manager_choice_empty_state,
)
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


@pytest.mark.parametrize(
    ("form_class", "field_name"),
    list(MANAGER_CHOICE_EMPTY_STATES),
    ids=[
        f"{form_class.__name__}.{field_name}"
        for form_class, field_name in MANAGER_CHOICE_EMPTY_STATES
    ],
)
def test_empty_state_registry_points_to_model_choice_fields(form_class, field_name):
    assert field_name in form_class.base_fields
    assert isinstance(form_class.base_fields[field_name], ModelChoiceField)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("form_class", "field_name", "expected"),
    [
        (form_class, field_name, message)
        for (form_class, field_name), message in MANAGER_CHOICE_EMPTY_STATES.items()
    ],
    ids=[
        f"{form_class.__name__}.{field_name}"
        for form_class, field_name in MANAGER_CHOICE_EMPTY_STATES
    ],
)
def test_every_registered_empty_state_resolves(form_class, field_name, expected):
    form = form_class()
    form.fields[field_name].queryset = form.fields[field_name].queryset.none()

    assert manager_choice_empty_state(form[field_name]) == expected


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


@pytest.mark.django_db
def test_student_access_empty_state_points_to_existing_access():
    student = Student.objects.create(display_name="Ученица с доступом")
    user = User.objects.create_user(username="existing-access", password="test")
    StudentAccess.objects.create(
        user=user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
        is_active=False,
    )
    form = StudentAccessForm(student=student)

    body = render_to_string(
        "accounts/manager_student_access_form.html",
        {
            "form": form,
            "title": "Новый доступ",
            "student": student,
        },
    )

    assert not form.fields["user"].queryset.exists()
    assert "измените или включите существующий доступ" in body


@pytest.mark.django_db
def test_invitation_empty_states_are_field_specific():
    body = render_to_string(
        "accounts/manager_invitation_form.html",
        {"form": AccountInvitationForm()},
    )

    assert "Нет активных учеников для приглашения" in body
    assert "Нет неактивных непривилегированных аккаунтов для восстановления" in body
    assert body.count('class="field-empty-state"') >= 2


@pytest.mark.django_db
def test_invitation_empty_states_disappear_when_candidates_exist():
    Student.objects.create(display_name="Ученица для приглашения")
    User.objects.create_user(
        username="recovery-candidate",
        password="test",
        is_active=False,
    )

    body = render_to_string(
        "accounts/manager_invitation_form.html",
        {"form": AccountInvitationForm()},
    )

    assert "Нет активных учеников для приглашения" not in body
    assert "Нет неактивных непривилегированных аккаунтов для восстановления" not in body
    assert "staff и superuser через этот сценарий не восстанавливаются" in body


@pytest.mark.django_db
def test_invitation_help_text_keeps_django_aria_description_target():
    body = render_to_string(
        "accounts/manager_invitation_form.html",
        {"form": AccountInvitationForm()},
    )

    assert 'aria-describedby="id_recovery_user_helptext"' in body
    assert 'id="id_recovery_user_helptext"' in body
    assert 'aria-describedby="id_account_display_name_helptext"' in body
    assert 'id="id_account_display_name_helptext"' in body
