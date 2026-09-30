from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.manager_forms import (
    ManagerAdministrativeMakeupForm,
    ManagerCompensationCaseCreateForm,
    ManagerOneTimeEntitlementForm,
)
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    create_absence_compensation_case,
    issue_subscription,
)

User = get_user_model()


@pytest.fixture
def operations_context(db):
    manager = User.objects.create_user(
        username="operations-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(username="operations-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="operations-group", name="Operations Group")
    venue = Venue.objects.create(code="operations-venue", name="Operations Venue")
    lesson_type = LessonType.objects.create(
        code="operations-ice",
        name="ICE",
        subscription_category=SubscriptionCategory.ICE,
    )
    student = Student.objects.create(display_name="Operations Student")
    starts_at = timezone.now() - timedelta(days=2)
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
        status=Lesson.Status.COMPLETED,
    )
    return {
        "manager": manager,
        "coach": coach,
        "group": group,
        "venue": venue,
        "student": student,
        "lesson": lesson,
        "lesson_type": lesson_type,
    }


@pytest.mark.django_db
def test_manager_grants_and_cancels_one_time_entitlement(client, operations_context):
    ctx = operations_context
    client.force_login(ctx["manager"])

    created = client.post(
        reverse("subscriptions:manager_one_time_create"),
        {
            "student": str(ctx["student"].id),
            "lesson": str(ctx["lesson"].id),
            "entitlement_type": OneTimeEntitlement.Type.SINGLE_ICE,
        },
    )
    assert created.status_code == 302
    entitlement = OneTimeEntitlement.objects.get(
        student=ctx["student"],
        lesson=ctx["lesson"],
    )

    cancelled = client.post(
        reverse(
            "subscriptions:manager_one_time_cancel",
            kwargs={"entitlement_id": entitlement.id},
        )
    )
    assert cancelled.status_code == 302
    entitlement.refresh_from_db()
    assert entitlement.cancelled_at is not None


@pytest.mark.django_db
def test_manager_creates_and_materializes_free_compensation(client, operations_context):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="operations-plan",
        name="Operations plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date - timedelta(days=5),
        valid_until=source_date + timedelta(days=5),
        actor=ctx["manager"],
    )
    policy = AbsenceCompensationPolicy.objects.create(
        code="operations-unexcused",
        version=1,
        name="Operations unexcused",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        effective_from=source_date.replace(day=1),
    )
    AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD,
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
    )
    attendance = Attendance.objects.create(
        lesson=ctx["lesson"],
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=ctx["lesson"].ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )

    client.force_login(ctx["manager"])
    created = client.post(
        reverse("subscriptions:manager_compensation_case_create"),
        {
            "attendance": str(attendance.id),
            "absence_reason": AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
            "policy_code": policy.code,
        },
    )
    assert created.status_code == 302
    case = AbsenceCompensationCase.objects.get(attendance=attendance)

    materialized = client.post(
        reverse(
            "subscriptions:manager_compensation_materialize_free",
            kwargs={"case_id": case.id},
        )
    )
    assert materialized.status_code == 302
    case.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.MATERIALIZED
    assert MakeupEntitlement.objects.filter(
        student=ctx["student"],
        source_lesson=ctx["lesson"],
        reason=MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
    ).exists()


@pytest.mark.django_db
def test_manager_grants_administrative_makeup(client, operations_context):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="admin-makeup-plan",
        name="Administrative makeup plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=4,
    )
    subscription = issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date.replace(day=1),
        valid_until=source_date.replace(day=28),
        actor=ctx["manager"],
    )
    allowance = subscription.allowances.get(category=SubscriptionCategory.ICE)
    client.force_login(ctx["manager"])

    response = client.post(
        reverse("subscriptions:manager_administrative_makeup_create"),
        {
            "source_subscription_allowance": str(allowance.id),
            "source_lesson": str(ctx["lesson"].id),
            "valid_from": source_date.isoformat(),
            "valid_until": (source_date + timedelta(days=14)).isoformat(),
            "target_lesson": "",
            "reason": "manager correction",
        },
    )

    assert response.status_code == 302
    assert MakeupEntitlement.objects.filter(
        source_subscription_allowance=allowance,
        source_lesson=ctx["lesson"],
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
    ).exists()


@pytest.mark.django_db
def test_manager_runs_paid_makeup_workflow_with_refund_decision(
    client,
    operations_context,
):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="paid-web-plan",
        name="Paid web plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date - timedelta(days=5),
        valid_until=source_date + timedelta(days=5),
        actor=ctx["manager"],
    )
    policy = AbsenceCompensationPolicy.objects.create(
        code="paid-web-policy",
        version=1,
        name="Paid web policy",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        effective_from=source_date - timedelta(days=30),
    )
    AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED,
    )
    attendance = Attendance.objects.create(
        lesson=ctx["lesson"],
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=ctx["lesson"].ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )
    client.force_login(ctx["manager"])

    created = client.post(
        reverse("subscriptions:manager_compensation_case_create"),
        {
            "attendance": str(attendance.id),
            "absence_reason": AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
            "policy_code": policy.code,
        },
    )
    assert created.status_code == 302
    case = AbsenceCompensationCase.objects.get(attendance=attendance)

    authorized = client.post(
        reverse(
            "subscriptions:manager_compensation_authorize_paid",
            kwargs={"case_id": case.id},
        ),
        {"target_subscription": "", "fee_confirmed": ""},
    )
    assert authorized.status_code == 302
    grant = case.action_grants.get(
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
    )
    assert grant.fee_confirmed_at is None

    confirmed = client.post(
        reverse(
            "subscriptions:manager_compensation_confirm_fee",
            kwargs={"grant_id": grant.id},
        )
    )
    assert confirmed.status_code == 302
    grant.refresh_from_db()
    assert grant.fee_confirmed_at is not None

    activated = client.post(
        reverse(
            "subscriptions:manager_compensation_activate_paid",
            kwargs={"grant_id": grant.id},
        ),
        {"target_subscription": ""},
    )
    assert activated.status_code == 302
    grant.refresh_from_db()
    assert grant.activated_at is not None
    assert grant.makeup_entitlement is not None

    reversed_response = client.post(
        reverse(
            "subscriptions:manager_compensation_reverse",
            kwargs={"case_id": case.id},
        ),
        {
            "reason": "manager correction",
            "refund_required": "no",
        },
    )
    assert reversed_response.status_code == 302
    case.refresh_from_db()
    grant.refresh_from_db()
    assert case.status == AbsenceCompensationCase.Status.REVERSED
    assert grant.reversed_at is not None
    assert grant.refund_required is False


def _make_operations_lesson(*, ctx, starts_at, status=Lesson.Status.COMPLETED):
    return Lesson.objects.create(
        group=ctx["group"],
        lesson_type=ctx["lesson_type"],
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
def test_manager_choice_labels_use_school_timezone(
    operations_context,
    settings,
):
    ctx = operations_context
    settings.TIME_ZONE = "UTC"
    settings.SCHOOL_TIME_ZONE = "Asia/Tokyo"

    expected_time = ctx["lesson"].starts_at.astimezone(
        ZoneInfo("Asia/Tokyo")
    ).strftime("%d.%m.%Y %H:%M")
    one_time_form = ManagerOneTimeEntitlementForm()
    lesson_label = one_time_form.fields["lesson"].label_from_instance(
        ctx["lesson"]
    )
    assert expected_time in lesson_label

    attendance = Attendance.objects.create(
        lesson=ctx["lesson"],
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=ctx["lesson"].ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )
    compensation_form = ManagerCompensationCaseCreateForm()
    attendance_label = compensation_form.fields[
        "attendance"
    ].label_from_instance(attendance)
    assert expected_time in attendance_label


@pytest.mark.django_db
def test_manager_lesson_choices_are_limited_to_sixty_day_window(
    operations_context,
):
    ctx = operations_context
    old_lesson = _make_operations_lesson(
        ctx=ctx,
        starts_at=timezone.now() - timedelta(days=61),
    )
    future_lesson = _make_operations_lesson(
        ctx=ctx,
        starts_at=timezone.now() + timedelta(days=61),
        status=Lesson.Status.DRAFT,
    )

    one_time_ids = set(
        ManagerOneTimeEntitlementForm()
        .fields["lesson"]
        .queryset.values_list("id", flat=True)
    )
    admin_form = ManagerAdministrativeMakeupForm()
    source_ids = set(
        admin_form.fields["source_lesson"].queryset.values_list(
            "id",
            flat=True,
        )
    )
    target_ids = set(
        admin_form.fields["target_lesson"].queryset.values_list(
            "id",
            flat=True,
        )
    )

    assert ctx["lesson"].id in one_time_ids
    assert ctx["lesson"].id in source_ids
    assert ctx["lesson"].id in target_ids
    assert old_lesson.id not in one_time_ids | source_ids | target_ids
    assert future_lesson.id not in one_time_ids | source_ids | target_ids


@pytest.mark.django_db
def test_compensation_case_choices_exclude_active_case_and_old_absence(
    operations_context,
):
    ctx = operations_context
    source_date = timezone.localdate(ctx["lesson"].starts_at)
    plan = SubscriptionPlan.objects.create(
        code="choice-filter-plan",
        name="Choice filter plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    issue_subscription(
        student_id=ctx["student"].id,
        plan_id=plan.id,
        valid_from=source_date - timedelta(days=10),
        valid_until=source_date + timedelta(days=10),
        actor=ctx["manager"],
    )
    policy = AbsenceCompensationPolicy.objects.create(
        code="choice-filter-policy",
        version=1,
        name="Choice filter policy",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        effective_from=source_date - timedelta(days=30),
    )
    AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
    )

    active_attendance = Attendance.objects.create(
        lesson=ctx["lesson"],
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=ctx["lesson"].ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )
    create_absence_compensation_case(
        attendance_id=active_attendance.id,
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        actor=ctx["manager"],
        policy_code=policy.code,
        now=timezone.now(),
    )

    fresh_lesson = _make_operations_lesson(
        ctx=ctx,
        starts_at=timezone.now() - timedelta(days=1),
    )
    fresh_attendance = Attendance.objects.create(
        lesson=fresh_lesson,
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=fresh_lesson.ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )

    old_lesson = _make_operations_lesson(
        ctx=ctx,
        starts_at=timezone.now() - timedelta(days=61),
    )
    old_attendance = Attendance.objects.create(
        lesson=old_lesson,
        student=ctx["student"],
        status=Attendance.Status.ABSENT,
        marked_at=old_lesson.ends_at,
        marked_by=ctx["manager"],
        updated_by=ctx["manager"],
    )

    choice_ids = set(
        ManagerCompensationCaseCreateForm()
        .fields["attendance"]
        .queryset.values_list("id", flat=True)
    )

    assert fresh_attendance.id in choice_ids
    assert active_attendance.id not in choice_ids
    assert old_attendance.id not in choice_ids


@pytest.mark.django_db
def test_manager_post_checks_permission_before_compensation_lookup(client):
    import uuid

    outsider = User.objects.create_user(
        username="manager-compensation-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "subscriptions:manager_compensation_cancel",
            kwargs={"case_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 403
