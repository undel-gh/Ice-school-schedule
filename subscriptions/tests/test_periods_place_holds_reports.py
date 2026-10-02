from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as dt_timezone
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.urls import reverse

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from audit.models import AuditEvent
from core.choices import SubscriptionCategory
from core.testing import school_dt
from scheduling.models import GroupMembership, Lesson, LessonType, TrainingGroup, Venue
import subscriptions.services as subscription_services
from subscriptions.models import (
    AttendanceCoverage,
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
    cancel_group_place_hold,
    confirm_group_place_hold_fee,
    create_group_place_hold,
    issue_subscription,
    issue_subscription_for_period,
    recover_rolling_subscription_period_activation,
    resolve_subscription_period_window,
    reverse_attendance_coverage,
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


def make_plan(
    *,
    code: str,
    scheme=None,
    ice: int = 3,
    hall: int | None = None,
):
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
    if hall is not None:
        SubscriptionPlanAllowance.objects.create(
            plan=plan,
            category=SubscriptionCategory.HALL,
            visit_limit=hall,
        )
    return plan


def ensure_hold_membership(*, student, group, actor):
    membership, _ = GroupMembership.objects.get_or_create(
        student=student,
        group=group,
        starts_on=date(2026, 1, 1),
        defaults={
            "ends_on": None,
            "created_by": actor,
        },
    )
    return membership


def make_lesson(*, context, starts_at, lesson_type=None):
    return Lesson.objects.create(
        group=context["group"],
        lesson_type=lesson_type or context["ice"],
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
        starts_at=school_dt(2026, 9, 12, 18, 0),
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
    ensure_hold_membership(
        student=student,
        group=context["group"],
        actor=actor,
    )
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
        starts_at=school_dt(2026, 9, 12, 18, 0),
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
        starts_at=school_dt(2026, 9, 12, 18, 0),
    )
    attendance = Attendance.objects.create(
        lesson=first_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )

    activation_time = datetime(
        2026,
        9,
        12,
        16,
        tzinfo=dt_timezone.utc,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=activation_time,
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
    assert period.activated_at == activation_time

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
    ensure_hold_membership(
        student=student,
        group=context["group"],
        actor=actor,
    )
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
        as_of=date(2026, 11, 2),
        actor=actor,
    )

    hold.refresh_from_db()
    hold.seat_reservation.refresh_from_db()
    assert result["group_place_hold_expired"] == 1
    assert hold.status == GroupPlaceHold.Status.EXPIRED
    assert hold.seat_reservation.cancelled_at is not None


@pytest.mark.django_db
def test_existing_subscription_coverage_does_not_activate_future_rolling(
    actor,
    student,
    context,
):
    calendar = SubscriptionPeriodScheme.objects.create(
        code="rolling-priority-calendar",
        name="Current calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    current_plan = make_plan(
        code="rolling-priority-current",
        scheme=calendar,
        ice=4,
    )
    current = issue_subscription_for_period(
        student_id=student.id,
        plan_id=current_plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 10, tzinfo=dt_timezone.utc),
    )

    rolling = SubscriptionPeriodScheme.objects.create(
        code="rolling-priority-next",
        name="Next rolling",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    next_plan = make_plan(
        code="rolling-priority-next-plan",
        scheme=rolling,
        ice=4,
    )
    future = issue_subscription_for_period(
        student_id=student.id,
        plan_id=next_plan.id,
        reference_date=date(2026, 11, 1),
        actor=actor,
        now=datetime(2026, 10, 5, 10, tzinfo=dt_timezone.utc),
    )

    lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )

    future.refresh_from_db()
    future.billing_period.refresh_from_db()
    assert coverage.subscription_allowance.subscription_id == current.id
    assert future.valid_from is None
    assert future.valid_until is None
    assert future.billing_period.state == SubscriptionPeriod.State.PENDING
    assert future.billing_period.reference_date == date(2026, 11, 1)


@pytest.mark.django_db
def test_backdated_attendance_does_not_activate_rolling_subscription(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-backdate",
        name="Rolling backdate",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-backdate-plan", scheme=scheme, ice=4)
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 10),
        actor=actor,
        now=datetime(2026, 10, 10, 10, tzinfo=dt_timezone.utc),
    )

    old_lesson = make_lesson(
        context=context,
        starts_at=datetime(2026, 8, 20, 15, tzinfo=dt_timezone.utc),
    )
    attendance = Attendance.objects.create(
        lesson=old_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=actor,
    )

    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=datetime(2026, 10, 12, 12, tzinfo=dt_timezone.utc),
    )

    subscription.refresh_from_db()
    period = subscription.billing_period
    assert coverage is None
    assert subscription.valid_from is None
    assert subscription.valid_until is None
    assert period.state == SubscriptionPeriod.State.PENDING
    assert period.reference_date == date(2026, 10, 10)

    with pytest.raises(
        ValidationError,
        match="before its reference date",
    ):
        activate_rolling_subscription_period(
            subscription_id=subscription.id,
            lesson_id=old_lesson.id,
            actor=actor,
            now=datetime(2026, 10, 12, 12, tzinfo=dt_timezone.utc),
        )


@pytest.mark.django_db
def test_cancelled_group_place_hold_can_be_recreated(actor, student, context):
    ensure_hold_membership(
        student=student,
        group=context["group"],
        actor=actor,
    )
    scheme = SubscriptionPeriodScheme.objects.create(
        code="hold-recreate",
        name="Hold recreate",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    first = create_group_place_hold(
        student_id=student.id,
        group_id=context["group"].id,
        period_scheme_id=scheme.id,
        period_from=date(2026, 11, 1),
        period_until=date(2026, 11, 30),
        actor=actor,
    )
    cancel_group_place_hold(
        hold_id=first.id,
        actor=actor,
        reason="created by mistake",
        now=datetime(2026, 10, 20, 12, tzinfo=dt_timezone.utc),
    )

    second = create_group_place_hold(
        student_id=student.id,
        group_id=context["group"].id,
        period_scheme_id=scheme.id,
        period_from=date(2026, 11, 1),
        period_until=date(2026, 11, 30),
        actor=actor,
    )

    assert second.id != first.id
    assert second.status == GroupPlaceHold.Status.PENDING_PAYMENT
    assert GroupPlaceHold.objects.filter(
        student=student,
        group=context["group"],
        period_from=date(2026, 11, 1),
    ).count() == 2


@pytest.mark.django_db
def test_overlapping_group_place_holds_are_rejected(actor, student, context):
    ensure_hold_membership(
        student=student,
        group=context["group"],
        actor=actor,
    )
    calendar = SubscriptionPeriodScheme.objects.create(
        code="hold-overlap-calendar",
        name="Hold overlap calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    rolling = SubscriptionPeriodScheme.objects.create(
        code="hold-overlap-rolling",
        name="Hold overlap rolling",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    create_group_place_hold(
        student_id=student.id,
        group_id=context["group"].id,
        period_scheme_id=calendar.id,
        period_from=date(2026, 11, 1),
        period_until=date(2026, 11, 30),
        actor=actor,
    )

    with pytest.raises(
        ValidationError,
        match="overlaps this period",
    ):
        create_group_place_hold(
            student_id=student.id,
            group_id=context["group"].id,
            period_scheme_id=rolling.id,
            period_from=date(2026, 11, 5),
            period_until=date(2026, 12, 2),
            actor=actor,
        )


@pytest.mark.django_db
def test_manager_subscription_report_query_count_is_bounded(
    actor,
    context,
    django_assert_num_queries,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="report-query-calendar",
        name="Report query calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = make_plan(code="report-query-plan", scheme=scheme, ice=4)
    for index in range(12):
        report_student = Student.objects.create(
            display_name=f"Query student {index:02d}",
        )
        issue_subscription_for_period(
            student_id=report_student.id,
            plan_id=plan.id,
            reference_date=date(2026, 10, 1),
            actor=actor,
            now=datetime(2026, 10, 1, 10, tzinfo=dt_timezone.utc),
        )

    with django_assert_num_queries(5):
        rows = manager_subscription_report(
            as_of=date(2026, 10, 15),
            from_date=date(2026, 10, 1),
            until_date=date(2026, 10, 31),
        )
        assert len(rows) == 12


@pytest.mark.django_db
def test_manager_subscription_report_orders_pending_first_explicitly(
    actor,
    student,
):
    calendar = SubscriptionPeriodScheme.objects.create(
        code="report-order-calendar",
        name="Report order calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    active_plan = make_plan(code="report-order-active", scheme=calendar, ice=4)
    active = issue_subscription_for_period(
        student_id=student.id,
        plan_id=active_plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 10, tzinfo=dt_timezone.utc),
    )

    rolling = SubscriptionPeriodScheme.objects.create(
        code="report-order-rolling",
        name="Report order rolling",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    pending_plan = make_plan(
        code="report-order-pending",
        scheme=rolling,
        ice=4,
    )
    pending = issue_subscription_for_period(
        student_id=student.id,
        plan_id=pending_plan.id,
        reference_date=date(2026, 11, 1),
        actor=actor,
        now=datetime(2026, 10, 10, 10, tzinfo=dt_timezone.utc),
    )

    rows = manager_subscription_report(
        as_of=date(2026, 10, 15),
        student_id=student.id,
    )
    assert [row.subscription.id for row in rows] == [pending.id, active.id]


@pytest.mark.django_db
def test_reversing_activation_coverage_returns_unused_rolling_period_to_pending(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-revert",
        name="Rolling revert",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-revert-plan", scheme=scheme, ice=2)
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 10, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )
    assert coverage is not None

    subscription.refresh_from_db()
    period = subscription.billing_period
    assert period.state == SubscriptionPeriod.State.ACTIVE
    assert period.activation_lesson_id == lesson.id

    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
        now=datetime(2026, 10, 10, 17, tzinfo=dt_timezone.utc),
    )

    subscription.refresh_from_db()
    period.refresh_from_db()
    assert subscription.valid_from is None
    assert subscription.valid_until is None
    assert period.state == SubscriptionPeriod.State.PENDING
    assert period.starts_on is None
    assert period.ends_on is None
    assert period.activation_lesson_id is None
    assert period.activated_at is None
    event = AuditEvent.objects.get(
        event_type="SubscriptionPeriodActivationReverted",
        aggregate_id=period.id,
    )
    assert event.payload["reverted_coverage_id"] == str(coverage.id)
    assert event.payload["previous_starts_on"] == "2026-10-10"
    assert event.payload["previous_ends_on"] == "2026-11-06"


@pytest.mark.django_db
def test_reversing_activation_coverage_keeps_period_after_other_coverage(
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-no-revert",
        name="Rolling no revert",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-no-revert-plan", scheme=scheme, ice=3)
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    first_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    second_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 17, 18, 0),
    )
    first_attendance = Attendance.objects.create(
        lesson=first_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 10, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    second_attendance = Attendance.objects.create(
        lesson=second_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 17, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    first_coverage = assign_attendance_coverage(
        attendance_id=first_attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )
    second_coverage = assign_attendance_coverage(
        attendance_id=second_attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 17, 19, 0),
    )
    assert first_coverage is not None
    assert second_coverage is not None

    reverse_attendance_coverage(
        coverage_id=first_coverage.id,
        actor=actor,
        now=school_dt(2026, 10, 17, 20, 0),
    )

    subscription.refresh_from_db()
    period = subscription.billing_period
    assert period.state == SubscriptionPeriod.State.ACTIVE
    assert period.activation_lesson_id == first_lesson.id
    assert subscription.valid_from == date(2026, 10, 10)
    assert subscription.valid_until == date(2026, 11, 6)
    assert AuditEvent.objects.filter(
        event_type="SubscriptionPeriodActivationRevertSkipped",
        aggregate_id=period.id,
    ).exists()


@pytest.mark.django_db
def test_automatic_rolling_revert_locks_all_allowances_before_dependencies(
    actor,
    student,
    context,
    monkeypatch,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-lock-all-auto",
        name="Rolling lock all auto",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(
        code="rolling-lock-all-auto-plan",
        scheme=scheme,
        ice=2,
        hall=2,
    )
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 10, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )
    assert coverage is not None

    order = []
    original_lock = (
        subscription_services._lock_subscription_allowances_for_rolling_revert
    )
    original_dependencies = subscription_services._rolling_period_revert_dependencies

    def recording_lock(*, subscription_id):
        order.append(("lock", subscription_id))
        return original_lock(subscription_id=subscription_id)

    def recording_dependencies(**kwargs):
        order.append(("dependencies", kwargs["subscription_id"]))
        return original_dependencies(**kwargs)

    monkeypatch.setattr(
        subscription_services,
        "_lock_subscription_allowances_for_rolling_revert",
        recording_lock,
    )
    monkeypatch.setattr(
        subscription_services,
        "_rolling_period_revert_dependencies",
        recording_dependencies,
    )

    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 20, 0),
    )

    assert order[:2] == [
        ("lock", subscription.id),
        ("dependencies", subscription.id),
    ]
    subscription.refresh_from_db()
    assert subscription.valid_from is None
    assert subscription.valid_until is None


@pytest.mark.django_db(transaction=True)
def test_manual_rolling_recovery_blocks_concurrent_other_category_coverage(
    actor,
    student,
    context,
    monkeypatch,
):
    if connection.vendor != "postgresql":
        pytest.skip("Row-locking concurrency test requires PostgreSQL.")

    hall_type = LessonType.objects.create(
        code="period-hall-concurrent-recovery",
        name="Period Hall Concurrent Recovery",
        subscription_category=SubscriptionCategory.HALL,
    )
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-manual-lock-all",
        name="Rolling manual lock all",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(
        code="rolling-manual-lock-all-plan",
        scheme=scheme,
        ice=2,
        hall=2,
    )
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    activation_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    activation_attendance = Attendance.objects.create(
        lesson=activation_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 10, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    activation_coverage = assign_attendance_coverage(
        attendance_id=activation_attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )
    assert activation_coverage is not None

    ice_allowance = subscription.allowances.get(
        category=SubscriptionCategory.ICE,
    )
    blocker_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 11, 18, 0),
    )
    blocker = MakeupEntitlement.objects.create(
        student=student,
        source_lesson=blocker_lesson,
        source_subscription_allowance=ice_allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 10, 11),
        valid_until=date(2026, 10, 31),
        created_by=actor,
    )
    reverse_attendance_coverage(
        coverage_id=activation_coverage.id,
        actor=actor,
        now=school_dt(2026, 10, 11, 20, 0),
    )
    blocker.cancelled_at = school_dt(2026, 10, 11, 21, 0)
    blocker.cancelled_by = actor
    blocker.save(update_fields=["cancelled_at", "cancelled_by"])

    hall_allowance = subscription.allowances.get(
        category=SubscriptionCategory.HALL,
    )
    hall_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 12, 18, 0),
        lesson_type=hall_type,
    )
    hall_attendance = Attendance.objects.create(
        lesson=hall_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 12, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )

    dependencies_checked = threading.Event()
    release_recovery = threading.Event()
    hall_lock_attempted = threading.Event()
    hall_lock_acquired = threading.Event()

    original_dependencies = subscription_services._rolling_period_revert_dependencies
    original_locked_balance = subscription_services.locked_allowance_balance

    def paused_dependencies(**kwargs):
        result = original_dependencies(**kwargs)
        if kwargs.get("excluded_coverage_id") is None:
            dependencies_checked.set()
            assert release_recovery.wait(timeout=5)
        return result

    def instrumented_locked_balance(allowance_id):
        if allowance_id == hall_allowance.id:
            hall_lock_attempted.set()
            result = original_locked_balance(allowance_id)
            hall_lock_acquired.set()
            return result
        return original_locked_balance(allowance_id)

    monkeypatch.setattr(
        subscription_services,
        "_rolling_period_revert_dependencies",
        paused_dependencies,
    )
    monkeypatch.setattr(
        subscription_services,
        "locked_allowance_balance",
        instrumented_locked_balance,
    )

    def recovery_worker():
        close_old_connections()
        try:
            return recover_rolling_subscription_period_activation(
                subscription_id=subscription.id,
                actor=actor,
            )
        finally:
            connections.close_all()

    def coverage_worker():
        close_old_connections()
        try:
            return assign_attendance_coverage(
                attendance_id=hall_attendance.id,
                actor=None,
                now=school_dt(2026, 10, 12, 19, 0),
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery_future = pool.submit(recovery_worker)
        assert dependencies_checked.wait(timeout=5)

        coverage_future = pool.submit(coverage_worker)
        assert hall_lock_attempted.wait(timeout=5)
        assert hall_lock_acquired.wait(timeout=0.5) is False

        release_recovery.set()
        recovered = recovery_future.result(timeout=5)
        assert recovered.state == SubscriptionPeriod.State.PENDING
        hall_coverage = coverage_future.result(timeout=5)

    assert hall_coverage is not None
    subscription.refresh_from_db()
    period = subscription.billing_period
    assert period.state == SubscriptionPeriod.State.ACTIVE
    assert subscription.valid_from == date(2026, 10, 12)
    assert subscription.valid_until == date(2026, 11, 8)
    assert AttendanceCoverage.objects.filter(
        pk=hall_coverage.id,
        subscription_allowance=hall_allowance,
        reversed_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_reversing_activation_coverage_is_blocked_by_active_makeup_dependency(
    client,
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="rolling-makeup-dependency",
        name="Rolling makeup dependency",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
    )
    plan = make_plan(code="rolling-makeup-dependency-plan", scheme=scheme, ice=3)
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    activation_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    attendance = Attendance.objects.create(
        lesson=activation_lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_at=school_dt(2026, 10, 10, 19, 0),
        marked_by=actor,
        updated_by=actor,
    )
    coverage = assign_attendance_coverage(
        attendance_id=attendance.id,
        actor=actor,
        now=school_dt(2026, 10, 10, 19, 0),
    )
    assert coverage is not None

    source_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 12, 18, 0),
    )
    allowance = subscription.allowances.get(category=SubscriptionCategory.ICE)
    MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=date(2026, 11, 7),
        valid_until=date(2026, 12, 31),
        created_by=actor,
    )

    reverse_attendance_coverage(
        coverage_id=coverage.id,
        actor=actor,
        now=school_dt(2026, 10, 12, 20, 0),
    )

    subscription.refresh_from_db()
    period = subscription.billing_period
    assert period.state == SubscriptionPeriod.State.ACTIVE
    assert subscription.valid_from == date(2026, 10, 10)
    assert subscription.valid_until == date(2026, 11, 6)
    event = AuditEvent.objects.get(
        event_type="SubscriptionPeriodActivationRevertSkipped",
        aggregate_id=period.id,
    )
    assert event.payload["reason"] == "dependent_rights_exist"
    assert event.payload["dependencies"]["active_makeups"] is True

    with pytest.raises(ValidationError, match="dependent"):
        recover_rolling_subscription_period_activation(
            subscription_id=subscription.id,
            actor=actor,
        )

    client.force_login(actor)
    detail = client.get(
        reverse(
            "subscriptions:manager_subscription_detail",
            kwargs={"subscription_id": subscription.id},
        )
    )
    body = detail.content.decode()
    assert detail.status_code == 200
    assert "Требуется восстановление расчётного периода" in body
    assert "Повторить откат периода" in body
    assert "зависимые права" in body

    makeup = MakeupEntitlement.objects.get(
        source_subscription_allowance=allowance,
        source_lesson=source_lesson,
    )
    cancelled = client.post(
        reverse(
            "subscriptions:manager_makeup_cancel",
            kwargs={"makeup_id": makeup.id},
        ),
        {"reason": "Исправление ошибочной активации периода"},
    )
    assert cancelled.status_code == 302
    makeup.refresh_from_db()
    assert makeup.cancelled_at is not None
    assert AuditEvent.objects.filter(
        event_type="MakeupEntitlementCancelled",
        aggregate_id=makeup.id,
        payload__source="manager_manual",
    ).exists()

    recovered = client.post(
        reverse(
            "subscriptions:manager_subscription_rolling_recovery",
            kwargs={"subscription_id": subscription.id},
        )
    )
    assert recovered.status_code == 302

    subscription.refresh_from_db()
    period.refresh_from_db()
    assert subscription.valid_from is None
    assert subscription.valid_until is None
    assert period.state == SubscriptionPeriod.State.PENDING
    assert period.starts_on is None
    assert period.ends_on is None
    assert period.activation_lesson_id is None
    recovery_event = AuditEvent.objects.filter(
        event_type="SubscriptionPeriodActivationReverted",
        aggregate_id=period.id,
    ).latest("occurred_at")
    assert recovery_event.payload["reason"] == "manager_recovery"
    assert recovery_event.payload["recovery_event_id"] == str(event.id)

    detail_after = client.get(
        reverse(
            "subscriptions:manager_subscription_detail",
            kwargs={"subscription_id": subscription.id},
        )
    )
    assert "Требуется восстановление расчётного периода" not in (
        detail_after.content.decode()
    )

@pytest.mark.django_db
def test_manager_makeup_cancel_preserves_source_workflow_boundaries(
    client,
    actor,
    student,
    context,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="makeup-cancel-boundary",
        name="Makeup cancel boundary",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = make_plan(code="makeup-cancel-boundary-plan", scheme=scheme, ice=2)
    subscription = issue_subscription_for_period(
        student_id=student.id,
        plan_id=plan.id,
        reference_date=date(2026, 10, 1),
        actor=actor,
        now=datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc),
    )
    allowance = subscription.allowances.get(category=SubscriptionCategory.ICE)
    source_lesson = make_lesson(
        context=context,
        starts_at=school_dt(2026, 10, 10, 18, 0),
    )
    compensation_makeup = MakeupEntitlement.objects.create(
        student=student,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=SubscriptionCategory.ICE,
        reason=MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
        valid_from=date(2026, 10, 11),
        valid_until=date(2026, 10, 31),
        created_by=actor,
    )
    client.force_login(actor)

    response = client.post(
        reverse(
            "subscriptions:manager_makeup_cancel",
            kwargs={"makeup_id": compensation_makeup.id},
        ),
        {"reason": "Не должен обходить compensation workflow"},
        follow=True,
    )

    assert response.status_code == 200
    compensation_makeup.refresh_from_db()
    assert compensation_makeup.cancelled_at is None
    assert "процесс компенсации" in response.content.decode()

