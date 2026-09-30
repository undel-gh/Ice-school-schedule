from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student
from attendance.models import Attendance
from core.choices import SubscriptionCategory
from core.time import make_school_aware, school_date
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    SubscriptionAllowance,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import (
    create_absence_compensation_policy_action,
    issue_subscription_for_period,
)


User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="catalog-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_fixed_period_scheme_requires_anchor_in_manager_ui(client, manager):
    client.force_login(manager)

    invalid = client.post(
        reverse("subscriptions:manager_period_scheme_create"),
        {
            "code": "fixed-no-anchor",
            "name": "Fixed no anchor",
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": "",
            "is_active": "on",
        },
    )
    assert invalid.status_code == 200
    assert not SubscriptionPeriodScheme.objects.filter(
        code="fixed-no-anchor"
    ).exists()

    anchor = date(2026, 1, 5)
    created = client.post(
        reverse("subscriptions:manager_period_scheme_create"),
        {
            "code": "fixed-main",
            "name": "Fixed main",
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": anchor.isoformat(),
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    scheme = SubscriptionPeriodScheme.objects.get(code="fixed-main")
    assert scheme.fixed_anchor_date == anchor




@pytest.mark.django_db
def test_fixed_anchor_change_only_affects_future_period_snapshots(
    client,
    manager,
):
    first_anchor = date(2026, 1, 5)
    second_anchor = date(2026, 1, 12)
    scheme = SubscriptionPeriodScheme.objects.create(
        code="fixed-snapshot",
        name="Fixed snapshot",
        mode=SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
        fixed_anchor_date=first_anchor,
    )
    plan = SubscriptionPlan.objects.create(
        code="fixed-plan",
        name="Fixed plan",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    first_student = Student.objects.create(display_name="Anchor first")
    second_student = Student.objects.create(display_name="Anchor second")
    reference_date = date(2026, 10, 20)

    first = issue_subscription_for_period(
        student_id=first_student.id,
        plan_id=plan.id,
        reference_date=reference_date,
        actor=manager,
        now=make_school_aware(datetime(2026, 10, 20, 9, 0)),
    )
    first_period = first.billing_period
    assert first_period.fixed_anchor_snapshot == first_anchor

    client.force_login(manager)
    response = client.post(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        ),
        {
            "code": scheme.code,
            "name": scheme.name,
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": second_anchor.isoformat(),
            "is_active": "on",
        },
    )
    assert response.status_code == 302

    first_period.refresh_from_db()
    assert first_period.fixed_anchor_snapshot == first_anchor

    second = issue_subscription_for_period(
        student_id=second_student.id,
        plan_id=plan.id,
        reference_date=reference_date,
        actor=manager,
        now=make_school_aware(datetime(2026, 10, 20, 10, 0)),
    )
    assert second.billing_period.fixed_anchor_snapshot == second_anchor


@pytest.mark.django_db
def test_plan_catalog_changes_only_future_subscription_snapshots(
    client,
    manager,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="calendar-catalog",
        name="Calendar",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
    )
    plan = SubscriptionPlan.objects.create(
        code="ice-8",
        name="8 льдов",
        period_scheme=scheme,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    first_student = Student.objects.create(display_name="Первый")
    second_student = Student.objects.create(display_name="Второй")
    reference_date = date(2026, 10, 1)

    first = issue_subscription_for_period(
        student_id=first_student.id,
        plan_id=plan.id,
        reference_date=reference_date,
        actor=manager,
        now=make_school_aware(datetime(2026, 10, 1, 9, 0)),
    )
    first_allowance = SubscriptionAllowance.objects.get(
        subscription=first,
        category=SubscriptionCategory.ICE,
    )
    assert first_allowance.visit_limit_snapshot == 8

    client.force_login(manager)
    response = client.post(
        reverse(
            "subscriptions:manager_plan_edit",
            kwargs={"plan_id": plan.id},
        ),
        {
            "code": plan.code,
            "name": "12 льдов + 4 зала",
            "period_scheme": str(scheme.id),
            "ice_visit_limit": "12",
            "hall_visit_limit": "4",
            "is_active": "on",
        },
    )
    assert response.status_code == 302

    first_allowance.refresh_from_db()
    assert first_allowance.visit_limit_snapshot == 8

    second = issue_subscription_for_period(
        student_id=second_student.id,
        plan_id=plan.id,
        reference_date=reference_date,
        actor=manager,
        now=make_school_aware(datetime(2026, 10, 1, 10, 0)),
    )
    snapshots = {
        item.category: item.visit_limit_snapshot
        for item in second.allowances.all()
    }
    assert snapshots == {
        SubscriptionCategory.ICE: 12,
        SubscriptionCategory.HALL: 4,
    }


@pytest.mark.django_db
def test_manager_builds_compensation_policy_actions_and_windows(
    client,
    manager,
):
    client.force_login(manager)
    effective_from = date(2027, 1, 1)

    created = client.post(
        reverse("subscriptions:manager_policy_create"),
        {
            "code": "unexcused-catalog",
            "name": "Неуважительный пропуск",
            "absence_reason": AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
            "justification_requirement": (
                AbsenceCompensationPolicy.JustificationRequirement.NONE
            ),
            "max_eligible_absences": "4",
            "limit_scope": AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
            "effective_from": effective_from.isoformat(),
            "effective_until": "",
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    policy = AbsenceCompensationPolicy.objects.get(code="unexcused-catalog")
    assert policy.version == 1

    action_response = client.post(
        reverse(
            "subscriptions:manager_policy_action_create",
            kwargs={"policy_id": policy.id},
        ),
        {
            "action_type": AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
            "target_period_rule": (
                AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
            ),
            "requirement": (
                AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED
            ),
            "validity_days": "",
            "priority": "20",
            "is_active": "on",
        },
    )
    assert action_response.status_code == 302
    action = policy.actions.get()

    window_response = client.post(
        reverse(
            "subscriptions:manager_policy_window_create",
            kwargs={"action_id": action.id},
        ),
        {
            "name": "Май в июнь",
            "source_from": "2027-05-01",
            "source_until": "2027-05-31",
            "target_from": "2027-06-01",
            "target_until": "2027-06-30",
            "requirement_override": (
                AbsenceCompensationPolicyAction.Requirement.NONE
            ),
            "priority": "10",
            "is_active": "on",
        },
    )
    assert window_response.status_code == 302
    window = action.windows.get()
    assert window.name == "Май в июнь"
    assert window.priority == 10


def _reference_policy(*, policy, manager):
    student = Student.objects.create(display_name="Policy student")
    coach_user = User.objects.create_user(
        username="policy-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Policy coach",
    )
    group = TrainingGroup.objects.create(
        code="policy-group",
        name="Policy group",
    )
    venue = Venue.objects.create(code="policy-venue", name="Policy venue")
    lesson_type = LessonType.objects.create(
        code="policy-ice",
        name="Policy ICE",
        subscription_category=SubscriptionCategory.ICE,
    )
    starts_at = make_school_aware(datetime(2026, 10, 10, 18, 0))
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
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_by=manager,
    )
    return AbsenceCompensationCase.objects.create(
        attendance=attendance,
        student=student,
        source_lesson=lesson,
        policy=policy,
        absence_reason=policy.absence_reason,
        source_date=school_date(starts_at),
        category=SubscriptionCategory.ICE,
        policy_code_snapshot=policy.code,
        policy_version_snapshot=policy.version,
        policy_name_snapshot=policy.name,
        justification_requirement_snapshot=policy.justification_requirement,
        max_eligible_absences_snapshot=None,
        limit_scope_snapshot=policy.limit_scope,
        actions_snapshot=[],
        eligibility_status=AbsenceCompensationCase.EligibilityStatus.ELIGIBLE,
        created_by=manager,
    )


@pytest.mark.django_db
def test_referenced_policy_versions_instead_of_mutating(
    client,
    manager,
    monkeypatch,
):
    policy = AbsenceCompensationPolicy.objects.create(
        code="medical-versioned",
        version=1,
        name="Medical v1",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
        justification_requirement=(
            AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
        ),
        max_eligible_absences=None,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
        ),
        priority=10,
    )
    AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="June to August",
        source_from=date(2027, 6, 1),
        source_until=date(2027, 6, 30),
        target_from=date(2027, 8, 1),
        target_until=date(2027, 8, 31),
        priority=10,
    )
    _reference_policy(policy=policy, manager=manager)

    fixed_now = make_school_aware(datetime(2026, 10, 1, 12, 0))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now)
    effective_from = date(2026, 10, 15)
    client.force_login(manager)

    edit = client.post(
        reverse(
            "subscriptions:manager_policy_edit",
            kwargs={"policy_id": policy.id},
        ),
        {
            "code": policy.code,
            "name": "Mutated",
            "absence_reason": policy.absence_reason,
            "justification_requirement": policy.justification_requirement,
            "max_eligible_absences": "",
            "limit_scope": policy.limit_scope,
            "effective_from": policy.effective_from.isoformat(),
            "effective_until": "",
            "is_active": "on",
        },
    )
    assert edit.status_code == 200
    policy.refresh_from_db()
    assert policy.name == "Medical v1"

    versioned = client.post(
        reverse(
            "subscriptions:manager_policy_version",
            kwargs={"policy_id": policy.id},
        ),
        {
            "name": "Medical v2",
            "justification_requirement": policy.justification_requirement,
            "max_eligible_absences": "",
            "limit_scope": policy.limit_scope,
            "effective_from": effective_from.isoformat(),
            "effective_until": "",
        },
    )
    assert versioned.status_code == 302

    policy.refresh_from_db()
    replacement = AbsenceCompensationPolicy.objects.get(
        code=policy.code,
        version=2,
    )
    assert policy.effective_until == effective_from - timedelta(days=1)
    assert replacement.effective_from == effective_from
    assert replacement.actions.count() == 1
    copied_action = replacement.actions.get()
    assert copied_action.windows.count() == 1
    assert copied_action.windows.get().name == "June to August"

    with pytest.raises(ValidationError, match="referenced"):
        create_absence_compensation_policy_action(
            policy_id=policy.id,
            action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
            target_period_rule=(
                AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
            ),
            requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
            validity_days=None,
            priority=20,
            is_active=True,
            actor=manager,
        )
