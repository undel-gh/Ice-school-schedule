import pytest

from attendance.models import AbsenceJustification
from core.choices import SubscriptionCategory
from core.presentation import MANAGER_LABELS
from scheduling.models import Lesson
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    OneTimeEntitlement,
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
    ],
)
def test_manager_labels_cover_textchoices_exactly(kind, choices):
    assert set(MANAGER_LABELS[kind]) == set(choices.values)
