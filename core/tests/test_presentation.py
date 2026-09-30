import pytest

from attendance.models import AbsenceJustification
from core.choices import SubscriptionCategory
from core.presentation import MANAGER_LABELS
from scheduling.models import Lesson
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    OneTimeEntitlement,
    SubscriptionPeriodScheme,
)


@pytest.mark.parametrize(
    ("kind", "choices"),
    [
        ("lesson_status", Lesson.Status),
        ("lesson_cancellation_reason", Lesson.CancellationReason),
        ("compensation_status", AbsenceCompensationCase.Status),
        (
            "compensation_eligibility",
            AbsenceCompensationCase.EligibilityStatus,
        ),
        ("absence_reason", AbsenceCompensationPolicy.AbsenceReason),
        ("medical_status", AbsenceJustification.Status),
        ("one_time_entitlement", OneTimeEntitlement.Type),
        ("subscription_category", SubscriptionCategory),
        ("subscription_period_mode", SubscriptionPeriodScheme.Mode),
        (
            "policy_justification",
            AbsenceCompensationPolicy.JustificationRequirement,
        ),
        ("policy_limit_scope", AbsenceCompensationPolicy.LimitScope),
        ("policy_action_type", AbsenceCompensationPolicyAction.ActionType),
        (
            "policy_target_period",
            AbsenceCompensationPolicyAction.TargetPeriodRule,
        ),
        ("policy_requirement", AbsenceCompensationPolicyAction.Requirement),
    ],
)
def test_manager_labels_cover_textchoices_exactly(kind, choices):
    assert set(MANAGER_LABELS[kind]) == set(choices.values)
