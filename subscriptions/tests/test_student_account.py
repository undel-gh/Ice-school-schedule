from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from core.time import make_school_aware
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    assign_attendance_coverage,
    issue_subscription,
)

User = get_user_model()


@pytest.fixture
def account_context(db):
    manager = User.objects.create_user(
        username="account-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    guardian = User.objects.create_user(
        username="account-guardian",
        password="test",
    )
    stranger = User.objects.create_user(
        username="account-stranger",
        password="test",
    )
    coach_user = User.objects.create_user(
        username="account-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Тренер Аккаунта",
    )
    group = TrainingGroup.objects.create(
        code="account-group",
        name="Группа аккаунта",
    )
    venue = Venue.objects.create(
        code="account-venue",
        name="Каток аккаунта",
    )
    ice = LessonType.objects.create(
        code="account-ice",
        name="Лёд",
        subscription_category=SubscriptionCategory.ICE,
    )
    hall = LessonType.objects.create(
        code="account-hall",
        name="Зал",
        subscription_category=SubscriptionCategory.HALL,
    )
    student = Student.objects.create(display_name="Анна")
    other_student = Student.objects.create(display_name="Борис")
    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    StudentAccess.objects.create(
        user=guardian,
        student=other_student,
        role=StudentAccess.Role.GUARDIAN,
    )

    scheme = SubscriptionPeriodScheme.objects.create(
        code="account-calendar",
        name="Календарный месяц",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = SubscriptionPlan.objects.create(
        code="account-8-4",
        name="8 льдов + 4 зала",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.HALL,
        visit_limit=4,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 10, 31),
        actor=manager,
    )

    return {
        "manager": manager,
        "guardian": guardian,
        "stranger": stranger,
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
        "hall": hall,
        "student": student,
        "other_student": other_student,
        "subscription": subscription,
    }


def make_lesson(
    *,
    ctx,
    starts_at,
    lesson_type=None,
    status=Lesson.Status.COMPLETED,
):
    return Lesson.objects.create(
        group=ctx["group"],
        lesson_type=lesson_type or ctx["ice"],
        coach=ctx["coach"],
        venue=ctx["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


@pytest.mark.django_db
def test_student_account_shows_balances_rights_and_coverage_history(
    client,
    account_context,
    monkeypatch,
):
    ctx = account_context
    fixed_now = make_school_aware(datetime(2026, 9, 30, 12, 0))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now)

    ordinary_lesson = make_lesson(
        ctx=ctx,
        starts_at=make_school_aware(datetime(2026, 9, 20, 18, 0)),
    )
    ordinary_attendance = Attendance.objects.create(
        lesson=ordinary_lesson,
        student=ctx["student"],
        status=Attendance.Status.PRESENT,
        marked_by=ctx["manager"],
    )
    ordinary_coverage = assign_attendance_coverage(
        attendance_id=ordinary_attendance.id,
        actor=ctx["manager"],
        now=fixed_now,
    )
    assert ordinary_coverage is not None
    assert ordinary_coverage.subscription_allowance.category == SubscriptionCategory.ICE

    used_one_time_lesson = make_lesson(
        ctx=ctx,
        starts_at=make_school_aware(datetime(2026, 9, 18, 18, 0)),
    )
    used_one_time = OneTimeEntitlement.objects.create(
        student=ctx["student"],
        lesson=used_one_time_lesson,
        entitlement_type=OneTimeEntitlement.Type.SINGLE_ICE,
        category=SubscriptionCategory.ICE,
        created_by=ctx["manager"],
    )
    used_attendance = Attendance.objects.create(
        lesson=used_one_time_lesson,
        student=ctx["student"],
        status=Attendance.Status.PRESENT,
        marked_by=ctx["manager"],
    )
    used_coverage = assign_attendance_coverage(
        attendance_id=used_attendance.id,
        actor=ctx["manager"],
        now=fixed_now,
    )
    assert used_coverage.one_time_entitlement_id == used_one_time.id

    future_one_time_lesson = make_lesson(
        ctx=ctx,
        starts_at=make_school_aware(datetime(2026, 10, 5, 18, 0)),
        status=Lesson.Status.CONFIRMED,
    )
    OneTimeEntitlement.objects.create(
        student=ctx["student"],
        lesson=future_one_time_lesson,
        entitlement_type=OneTimeEntitlement.Type.TRIAL_ICE,
        category=SubscriptionCategory.ICE,
        created_by=ctx["manager"],
    )

    ice_allowance = ctx["subscription"].allowances.get(
        category=SubscriptionCategory.ICE
    )
    source_lesson = make_lesson(
        ctx=ctx,
        starts_at=make_school_aware(datetime(2026, 9, 15, 18, 0)),
    )
    MakeupEntitlement.objects.create(
        student=ctx["student"],
        source_lesson=source_lesson,
        source_subscription_allowance=ice_allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 9, 25),
        valid_until=date(2026, 10, 15),
        created_by=ctx["manager"],
    )

    absent_lesson = make_lesson(
        ctx=ctx,
        starts_at=make_school_aware(datetime(2026, 9, 22, 18, 0)),
        lesson_type=ctx["hall"],
    )
    Attendance.objects.create(
        lesson=absent_lesson,
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_by=ctx["manager"],
    )

    client.force_login(ctx["guardian"])
    response = client.get(
        reverse("student_account:account"),
        {"student": str(ctx["student"].id)},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "8 льдов + 4 зала" in body
    assert "Лёд" in body
    assert "Зал" in body

    snapshot = response.context["account"]
    subscription = snapshot.subscriptions[0]
    balances = {
        item.allowance.category: item.balance
        for item in subscription.allowances
    }
    assert balances == {
        SubscriptionCategory.ICE: 7,
        SubscriptionCategory.HALL: 4,
    }

    one_time_statuses = {
        right.entitlement.id: right.status
        for right in snapshot.one_time_rights
    }
    assert one_time_statuses[used_one_time.id] == "used"
    assert "available" in one_time_statuses.values()
    assert snapshot.makeup_rights[0].status == "available"

    assert "Разовое право" in body
    assert "Отработка" in body
    assert "Абонемент · Лёд" in body
    assert "Отсутствовал" in body


@pytest.mark.django_db
def test_student_account_cannot_select_inaccessible_student(
    client,
    account_context,
):
    ctx = account_context
    inaccessible = Student.objects.create(display_name="Чужой ученик")
    client.force_login(ctx["guardian"])

    response = client.get(
        reverse("student_account:account"),
        {"student": str(inaccessible.id)},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_student_account_switches_between_accessible_children(
    client,
    account_context,
):
    ctx = account_context
    client.force_login(ctx["guardian"])

    response = client.get(
        reverse("student_account:account"),
        {"student": str(ctx["other_student"].id)},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert response.context["selected_student"] == ctx["other_student"]
    assert "Борис" in body


@pytest.mark.django_db
def test_inactive_student_keeps_historical_account_access(
    client,
    account_context,
):
    ctx = account_context
    ctx["student"].is_active = False
    ctx["student"].save(update_fields=["is_active"])
    client.force_login(ctx["guardian"])

    account = client.get(
        reverse("student_account:account"),
        {"student": str(ctx["student"].id)},
    )
    assert account.status_code == 200
    assert "Профиль неактивен" in account.content.decode()

    schedule = client.get(
        reverse("scheduling:student_schedule"),
        {"student": str(ctx["student"].id)},
    )
    assert schedule.status_code == 404

    ctx["other_student"].is_active = False
    ctx["other_student"].save(update_fields=["is_active"])
    home = client.get(reverse("scheduling:home"))
    assert home.status_code == 302
    assert home.url == reverse("student_account:account")


@pytest.mark.django_db
def test_student_account_requires_student_access(client, account_context):
    client.force_login(account_context["stranger"])

    response = client.get(reverse("student_account:account"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_student_attendance_history_is_paginated(
    client,
    account_context,
    monkeypatch,
):
    ctx = account_context
    fixed_now = make_school_aware(datetime(2026, 9, 30, 12, 0))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now)

    for index in range(26):
        lesson = make_lesson(
            ctx=ctx,
            starts_at=make_school_aware(
                datetime(2026, 8, 1, 18, 0)
                + timedelta(days=index)
            ),
        )
        Attendance.objects.create(
            lesson=lesson,
            student=ctx["student"],
            status=Attendance.Status.ABSENT,
            marked_by=ctx["manager"],
        )

    client.force_login(ctx["guardian"])
    first = client.get(
        reverse("student_account:account"),
        {"student": str(ctx["student"].id)},
    )
    second = client.get(
        reverse("student_account:account"),
        {
            "student": str(ctx["student"].id),
            "page": "2",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.context["history_page"].paginator.count == 26
    assert len(first.context["history_page"].object_list) == 25
    assert len(second.context["history_page"].object_list) == 1
    assert f"student={ctx['student'].id}" in second.content.decode()


@pytest.mark.django_db
def test_student_navigation_links_account_from_schedule(
    client,
    account_context,
):
    ctx = account_context
    client.force_login(ctx["guardian"])

    response = client.get(
        reverse("scheduling:student_schedule"),
        {"student": str(ctx["student"].id)},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert reverse("student_account:account") in body
    assert "Абонемент" in body
