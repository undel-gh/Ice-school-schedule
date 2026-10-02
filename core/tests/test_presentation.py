import pytest

from accounts.models import StudentAccess
from attendance.models import AbsenceJustification, Attendance
from core.choices import SubscriptionCategory
from core.presentation import MANAGER_LABELS, localize_message, manager_label
from scheduling.models import Lesson
from subscriptions.models import (
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    OneTimeEntitlement,
    SubscriptionLedgerEntry,
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


def test_domain_messages_are_localized_at_ui_boundary():
    assert (
        localize_message("RSVP deadline has passed.")
        == "Срок ответа на занятие уже истёк."
    )
    assert (
        localize_message("Coverage can only be assigned to PRESENT attendance.")
        == "Покрытие можно назначить только для отметки «Присутствовал»."
    )
    assert (
        localize_message(
            "The coach cannot be deactivated while assigned to future "
            "non-cancelled lessons. Reassign, reschedule or cancel them first."
        )
        == (
            "Тренера нельзя деактивировать, пока он назначен на будущие "
            "неотменённые занятия. Сначала замените тренера, перенесите "
            "или отмените эти занятия."
        )
    )


def test_gettext_choice_labels_are_russian():
    assert str(Attendance.Status.PRESENT.label) == "Присутствовал"
    assert str(StudentAccess.Role.GUARDIAN.label) == "Родитель / представитель"
    assert (
        str(SubscriptionPeriodScheme.Mode.CALENDAR_MONTH.label)
        == "Календарный месяц"
    )
    assert str(SubscriptionLedgerEntry.EntryType.CONSUME.label) == "Списание"


def test_audit_labels_hide_internal_codes():
    assert manager_label("LessonCancelled", "audit_event") == "Занятие отменено"
    assert manager_label("Lesson", "audit_aggregate") == "Занятие"
