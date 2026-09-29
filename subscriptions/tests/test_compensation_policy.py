from datetime import date

import pytest

from subscriptions.models import (
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
)
from subscriptions.selectors import (
    get_applicable_absence_policy,
    resolve_compensation_actions,
)


def make_policy(
    *,
    code: str = "unexcused-default",
    version: int = 1,
    effective_from: date = date(2026, 1, 1),
    effective_until: date | None = None,
    absence_reason: str = AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
) -> AbsenceCompensationPolicy:
    return AbsenceCompensationPolicy.objects.create(
        code=code,
        version=version,
        name=f"{code} v{version}",
        absence_reason=absence_reason,
        justification_requirement=(
            AbsenceCompensationPolicy.JustificationRequirement.NONE
        ),
        max_eligible_absences=4,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=effective_from,
        effective_until=effective_until,
    )


@pytest.mark.django_db
def test_policy_resolution_uses_effective_interval():
    old = make_policy(
        version=1,
        effective_from=date(2026, 1, 1),
        effective_until=date(2026, 8, 31),
    )
    new = make_policy(
        version=2,
        effective_from=date(2026, 9, 1),
    )

    assert get_applicable_absence_policy(
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        source_date=date(2026, 8, 20),
    ) == old
    assert get_applicable_absence_policy(
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        source_date=date(2026, 9, 20),
    ) == new


@pytest.mark.django_db
def test_policy_resolution_returns_none_when_no_policy_matches():
    make_policy(effective_from=date(2026, 9, 1))

    assert (
        get_applicable_absence_policy(
            absence_reason=(
                AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED
            ),
            source_date=date(2026, 8, 20),
        )
        is None
    )


@pytest.mark.django_db
def test_policy_resolution_rejects_ambiguous_configuration():
    make_policy(code="first")
    make_policy(code="second")

    with pytest.raises(
        ValueError,
        match="Multiple active absence compensation policies",
    ):
        get_applicable_absence_policy(
            absence_reason=(
                AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED
            ),
            source_date=date(2026, 9, 20),
        )


@pytest.mark.django_db
def test_policy_code_can_disambiguate_variants():
    first = make_policy(code="first")
    make_policy(code="second")

    assert get_applicable_absence_policy(
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        source_date=date(2026, 9, 20),
        policy_code="first",
    ) == first


@pytest.mark.django_db
def test_policy_can_offer_makeup_and_recalculation_together():
    policy = make_policy(
        code="medical",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.MEDICAL,
    )
    policy.justification_requirement = (
        AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
    )
    policy.max_eligible_absences = None
    policy.save(
        update_fields=[
            "justification_requirement",
            "max_eligible_absences",
        ]
    )

    makeup = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        priority=10,
    )
    recalculation = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=(
            AbsenceCompensationPolicyAction.ActionType.BILLING_RECALCULATION
        ),
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
        ),
        priority=20,
    )

    resolved = resolve_compensation_actions(
        policy=policy,
        source_date=date(2026, 9, 20),
    )

    assert [item.action for item in resolved] == [makeup, recalculation]


@pytest.mark.django_db
def test_may_window_allows_june_without_target_subscription():
    policy = make_policy()
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
        ),
        requirement=(
            AbsenceCompensationPolicyAction.Requirement.FEE_AND_TARGET_SUBSCRIPTION_REQUIRED
        ),
    )
    may_window = AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="May absences into June",
        source_from=date(2027, 5, 1),
        source_until=date(2027, 5, 31),
        target_from=date(2027, 6, 1),
        target_until=date(2027, 6, 30),
        requirement_override=(
            AbsenceCompensationPolicyAction.Requirement.NONE
        ),
    )

    resolved = resolve_compensation_actions(
        policy=policy,
        source_date=date(2027, 5, 15),
    )

    assert len(resolved) == 1
    assert resolved[0].window == may_window
    assert (
        resolved[0].requirement
        == AbsenceCompensationPolicyAction.Requirement.NONE
    )
    assert resolved[0].target_from == date(2027, 6, 1)
    assert resolved[0].target_until == date(2027, 6, 30)


@pytest.mark.django_db
def test_june_window_can_require_august_subscription():
    policy = make_policy()
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
        ),
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
    )
    AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="June absences into August",
        source_from=date(2027, 6, 1),
        source_until=date(2027, 6, 30),
        target_from=date(2027, 8, 1),
        target_until=date(2027, 8, 31),
        requirement_override=(
            AbsenceCompensationPolicyAction.Requirement.TARGET_SUBSCRIPTION_REQUIRED
        ),
    )

    resolved = resolve_compensation_actions(
        policy=policy,
        source_date=date(2027, 6, 15),
    )

    assert (
        resolved[0].requirement
        == AbsenceCompensationPolicyAction.Requirement.TARGET_SUBSCRIPTION_REQUIRED
    )
    assert resolved[0].target_from == date(2027, 8, 1)
    assert resolved[0].target_until == date(2027, 8, 31)


@pytest.mark.django_db
def test_matching_windows_with_same_priority_are_configuration_error():
    policy = make_policy()
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=(
            AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
        ),
    )
    for name, target_month in [("A", 6), ("B", 7)]:
        AbsenceCompensationPolicyWindow.objects.create(
            policy_action=action,
            name=name,
            source_from=date(2027, 5, 1),
            source_until=date(2027, 5, 31),
            target_from=date(2027, target_month, 1),
            target_until=date(2027, target_month, 28),
            priority=100,
        )

    with pytest.raises(
        ValueError,
        match="Multiple absence compensation windows with the same priority",
    ):
        resolve_compensation_actions(
            policy=policy,
            source_date=date(2027, 5, 15),
        )
