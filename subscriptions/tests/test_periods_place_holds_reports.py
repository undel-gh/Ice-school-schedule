from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    GroupPlaceHold,
    MakeupEntitlement,
    SubscriptionPeriod,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.selectors import manager_subscription_report
from subscriptions.services import (
    activate_rolling_subscription_period,
    assign_attendance_coverage,
    attach_subscription_period,
    confirm_group_place_hold_fee,
    create_group_place_hold,
    issue_subscription,
    issue_subscription_for_period,
    resolve_subscription_period_window,
)

User = get_user_model()


@pytest.fixture
def actor(db):
    return User.objects.create_user(
        username="period-admin",
        password="test",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def student(db):
    return Student.objects.create(display_name="Period Student")


@pytest.fixture
def context(db, actor):
    coach = CoachProfile.objects.create(
        user=actor,
        display_name="Period Coach",
    )
    group = TrainingGroup.objects.create(
        code="period-group",
        name="Period Group",
    )
    venue = Venue.objects.create(
        code="period-rink",
        name="Period Rink",
    )
    ice = LessonType.objects.create(
        code="period-ice",
        name="Period Ice",
        subscription_category=SubscriptionCategory.ICE,
    )
    return {
        "coach": coach,
        "group": group,
        "venue": venue,
        "ice": ice,
    }


def make_plan(*, code: str, scheme=None, ice: int = 3):
    plan = SubscriptionPlan.objects.create(
        code=code,
        name=code,
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=ice,
    )
    return plan


def make_lesson(*, context, starts_at):
    return Lesson.objects.create(
        group=context["group"],
        lesson_type=context["ice"],
        coach=context["coach"],
        venue=context["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )


@pytest.mark.django_db
def test_calendar_month_period_resolution():
    scheme = SubscriptionPeriodScheme.objects.create(
        code="calendar",
        name="Calendar month",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )

    assert resolve_subscription_period_window(
        scheme=scheme,
        reference_date=date(2026, 9, 15),
    ) == (date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.django_db
def test_fixed_28_period_resolution_uses_school_anchor():
    scheme = SubscriptionPeriodScheme.objects.create(
        code="fixed-28",
        name="Fixed 28",
        mode=SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
        fixed_anchor_date=date(2026, 9, 3),
    )

    assert resolve_subscription_period_window(
        scheme=scheme,
        reference_date=date(2026, 9, 30),
    ) == (date(2026, 9, 3), date(2026, 9, 30))
    assert resolve_subscription_period_window(
        scheme=scheme,
        reference_date=date(2026, 10, 1),
    ) == (date(2026, 10, 1), date(2026, 10, 28))


@pytest.mark.django_db
def test_rolling_28_period_stays_pending_until_first_lesson(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-28",
        name="Rolling 28",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-plan", scheme=scheme)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 12, 31),
        actor=actor,
    )

    period = attach_subscription_period(
        subscription_id=subscription.id,
        scheme_id=scheme.id,
        reference_date=date(2026, 9, 1),
        actor=actor,
        now=datetime(2026, 9, 1, 12, tzinfo=dt_timezone.utc),
    )

    assert period.state == SubscriptionPeriod.State.PENDING
    assert period.starts_on is None
    assert period.ends_on is None

    lesson = make_lesson(
        context=context,
        starts_at=datetime(
            2026,
            9,
            12,
            15,
            tzinfo=dt_timezone.utc,
        ),
    )
    activated = activate_rolling_subscription_period(
        subscription_id=subscription.id,
        lesson_id=lesson.id,
        actor=actor,
        now=datetime(2026, 9, 12, 16, tzinfo=dt_timezone.utc),
    )

    assert activated.state == SubscriptionPeriod.State.ACTIVE
    assert activated.starts_on == date(2026, 9, 12)
    assert activated.ends_on == date(2026, 10, 9)
    assert activated.activation_lesson_id == lesson.id


@pytest.mark.django_db
def test_group_place_hold_requires_full_scheme_period(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-calendar",
        name="Hold calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )

    hold = create_group_place_hold(
        student_id=student.id,
        group_id=context["group"].id,
        period_scheme_id=scheme.id,
        period_from=date(2026, 10, 1),
        period_until=date(2026, 10, 31),
        actor=actor,
    )
    assert hold.status == GroupPlaceHold.Status.PENDING_PAYMENT

    active = confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=actor,
        now=datetime(2026, 9, 25, 12, tzinfo=dt_timezone.utc),
    )
    assert active.status == GroupPlaceHold.Status.ACTIVE
    assert active.fee_confirmed_at is not None


@pytest.mark.django_db
def test_manager_subscription_report_separates_direct_and_makeup_visits(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="report-calendar",
        name="Report calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = make_plan(code="report-plan", scheme=scheme, ice=3)
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=actor,
    )
    attach_subscription_period(
        subscription_id=subscription.id,
        scheme_id=scheme.id,
        reference_date=date(2026, 9, 1),
        actor=actor,
        now=datetime(2026, 9, 1, 10, tzinfo=dt_timezone.utc),
    )
    allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE,
    )

    direct_lesson = make_lesson(
        context=context,
        starts_at=datetime(
            2026,
            9,
            10,
            15,
            tzinfo=dt_timezone.utc,
        ),
    )
    direct_attendance = Attendance.objects.create(
        lesson=direct_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    assign_attendance_coverage(
        attendance_id=direct_attendance.id,
        actor=actor,
    )

    source_lesson = make_lesson(
        context=context,
        starts_at=datetime(
            2026,
            9,
            12,
            15,
            tzinfo=dt_timezone.utc,
        ),
    )
    target_lesson = make_lesson(
        context=context,
        starts_at=datetime(
            2026,
            9,
            20,
            15,
            tzinfo=dt_timezone.utc,
        ),
    )
    makeup = MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 9, 15),
        valid_until=date(2026, 9, 30),
        created_by=actor,
    )
    makeup_attendance = Attendance.objects.create(
        lesson=target_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=makeup_attendance.id,
        actor=actor,
    )
    assert coverage.makeup_entitlement_id == makeup.id

    rows = manager_subscription_report(
        as_of=date(2026, 9, 21),
        student_id=student.id,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.period is not None
    assert row.period.starts_on == date(2026, 9, 1)
    assert len(row.allowances) == 1
    report = row.allowances[0]
    assert report.granted_visits == 3
    assert report.direct_visits == 1
    assert report.makeup_visits == 1
    assert report.consumed_visits == 2
    assert report.remaining_visits == 1
    assert report.makeup_total == 1
    assert report.makeup_used == 1
    assert report.makeup_available == 0


@pytest.mark.django_db
def test_rolling_subscription_activates_on_first_ordinary_coverage(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-auto",
        name="Rolling auto",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-auto-plan", scheme=scheme, ice=2)

    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 9, 1),
        actor=actor,
        now=datetime(2026, 9, 1, 10, tzinfo=dt_timezone.utc),
    )

    subscription.refresh_from_db()
    assert subscription.valid_from is None
    assert subscription.valid_until is None
    period = subscription.billing_period
    assert period.state == SubscriptionPeriod.State.PENDING

    first_lesson = make_lesson(
        context=context,
        starts_at=datetime(
            2026,
            9,
            12,
            15,
            tzinfo=dt_timezone.utc,
        ),
    )
    attendance = Attendance.objects.create(
        lesson=first_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
    )

    subscription.refresh_from_db()
    period.refresh_from_db()
    assert coverage is not None
    assert coverage.subscription_allowance.subscription_id == subscription.id
    assert subscription.valid_from == date(2026, 9, 12)
    assert subscription.valid_until == date(2026, 10, 9)
    assert period.state == SubscriptionPeriod.State.ACTIVE
    assert period.starts_on == date(2026, 9, 12)
    assert period.ends_on == date(2026, 10, 9)
    assert period.activation_lesson_id == first_lesson.id

    row = manager_subscription_report(
        as_of=date(2026, 9, 12),
        student_id=student.id,
    )[0]
    assert row.subscription_state == "active"
    assert row.allowances[0].consumed_visits == 1
    assert row.allowances[0].remaining_visits == 1


@pytest.mark.django_db
def test_calendar_subscription_issue_uses_scheme_window(actor, student):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="calendar-issue",
        name="Calendar issue",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = make_plan(code="calendar-issue-plan", scheme=scheme, ice=4)

    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 9, 19),
        actor=actor,
        now=datetime(2026, 9, 19, 10, tzinfo=dt_timezone.utc),
    )

    assert subscription.valid_from == date(2026, 9, 1)
    assert subscription.valid_until == date(2026, 9, 30)
    assert subscription.billing_period.state == SubscriptionPeriod.State.ACTIVE
    assert subscription.billing_period.starts_on == date(2026, 9, 1)
    assert subscription.billing_period.ends_on == date(2026, 9, 30)


@pytest.mark.django_db
def test_group_place_hold_expires_after_period(actor, student, context):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-expiry-calendar",
        name="Hold expiry",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    hold = create_group_place_hold(
        student_id=student.id,
        group_id=context["group"].id,
        period_scheme_id=scheme.id,
        period_from=date(2026, 10, 1),
        period_until=date(2026, 10, 31),
        actor=actor,
    )
    confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=actor,
        now=datetime(2026, 9, 25, 12, tzinfo=dt_timezone.utc),
    )

    from subscriptions.services import process_subscription_lifecycle

    result = process_subscription_lifecycle(
        as_of=date(2026, 11, 1),
        actor=actor,
    )

    hold.refresh_from_db()
    assert result["group_place_hold_expired"] == 1
    assert hold.status == GroupPlaceHold.Status.EXPIRED
