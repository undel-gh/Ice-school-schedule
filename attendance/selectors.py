from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.db.models import Exists, OuterRef, Prefetch

from core.time import make_school_aware, school_date
from subscriptions.models import AttendanceCoverage
from subscriptions.selectors import (
    get_available_makeups,
    get_available_one_time_entitlements,
    get_eligible_allowances,
)

from .models import Attendance


@dataclass(frozen=True)
class ManagerAttendanceCoverageRow:
    attendance: Attendance
    coverage: AttendanceCoverage | None
    source_label: str


@dataclass(frozen=True)
class AttendanceCoverageTarget:
    key: str
    label: str
    one_time_entitlement_id: UUID | None = None
    subscription_allowance_id: UUID | None = None
    makeup_entitlement_id: UUID | None = None


def _coverage_source_label(coverage: AttendanceCoverage) -> str:
    if coverage.one_time_entitlement_id is not None:
        entitlement = coverage.one_time_entitlement
        return f"Разовое право · {entitlement.get_entitlement_type_display()}"

    allowance = coverage.subscription_allowance
    if coverage.makeup_entitlement_id is not None:
        makeup = coverage.makeup_entitlement
        return (
            f"Отработка · {makeup.get_reason_display()} · "
            f"{allowance.subscription.plan_name_snapshot}"
        )

    return (
        f"Абонемент · {allowance.subscription.plan_name_snapshot} · "
        f"{allowance.category.upper()}"
    )


def manager_attendance_coverage_report(
    *,
    coverage_state: str,
    student_id: UUID | None = None,
    from_date: date | None = None,
    until_date: date | None = None,
) -> tuple[ManagerAttendanceCoverageRow, ...]:
    active_coverage_exists = AttendanceCoverage.objects.filter(
        attendance_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    active_coverages = (
        AttendanceCoverage.objects.filter(reversed_at__isnull=True)
        .select_related(
            "subscription_allowance__subscription",
            "one_time_entitlement",
            "makeup_entitlement",
        )
        .order_by("created_at", "id")
    )

    attendances = (
        Attendance.objects.filter(status=Attendance.Status.PRESENT)
        .select_related(
            "student",
            "lesson__lesson_type",
            "lesson__group",
        )
        .annotate(
            report_has_active_coverage=Exists(active_coverage_exists),
        )
        .prefetch_related(
            Prefetch(
                "coverages",
                queryset=active_coverages,
                to_attr="report_active_coverages",
            )
        )
        .order_by("-lesson__starts_at", "student__display_name", "id")
    )

    if coverage_state == "covered":
        attendances = attendances.filter(report_has_active_coverage=True)
    elif coverage_state == "uncovered":
        attendances = attendances.filter(report_has_active_coverage=False)

    if student_id is not None:
        attendances = attendances.filter(student_id=student_id)

    if from_date is not None:
        from_at = make_school_aware(datetime.combine(from_date, time.min))
        attendances = attendances.filter(lesson__starts_at__gte=from_at)

    if until_date is not None:
        until_at = make_school_aware(
            datetime.combine(until_date + timedelta(days=1), time.min)
        )
        attendances = attendances.filter(lesson__starts_at__lt=until_at)

    rows = []
    for attendance in attendances:
        coverage = (
            attendance.report_active_coverages[0]
            if attendance.report_active_coverages
            else None
        )
        rows.append(
            ManagerAttendanceCoverageRow(
                attendance=attendance,
                coverage=coverage,
                source_label=(
                    _coverage_source_label(coverage)
                    if coverage is not None
                    else ""
                ),
            )
        )
    return tuple(rows)


def available_attendance_coverage_targets(
    *,
    attendance: Attendance,
    current_coverage: AttendanceCoverage | None = None,
) -> tuple[AttendanceCoverageTarget, ...]:
    category = attendance.lesson.lesson_type.subscription_category
    lesson_date = school_date(attendance.lesson.starts_at)
    targets: list[AttendanceCoverageTarget] = []

    for entitlement in get_available_one_time_entitlements(
        student_id=attendance.student_id,
        lesson_id=attendance.lesson_id,
        category=category,
    ):
        targets.append(
            AttendanceCoverageTarget(
                key=f"one_time:{entitlement.id}",
                label=(
                    "Разовое право · "
                    f"{entitlement.get_entitlement_type_display()}"
                ),
                one_time_entitlement_id=entitlement.id,
            )
        )

    for makeup in get_available_makeups(
        student_id=attendance.student_id,
        lesson_id=attendance.lesson_id,
        category=category,
        lesson_date=lesson_date,
    ):
        targets.append(
            AttendanceCoverageTarget(
                key=f"makeup:{makeup.id}",
                label=(
                    f"Отработка · {makeup.get_reason_display()} · "
                    f"до {makeup.valid_until:%d.%m.%Y} · "
                    f"{makeup.source_subscription_allowance.subscription.plan_name_snapshot}"
                ),
                subscription_allowance_id=(
                    makeup.source_subscription_allowance_id
                ),
                makeup_entitlement_id=makeup.id,
            )
        )

    for eligible in get_eligible_allowances(
        student_id=attendance.student_id,
        category=category,
        lesson_date=lesson_date,
    ):
        allowance = eligible.allowance
        if (
            current_coverage is not None
            and current_coverage.makeup_entitlement_id is None
            and current_coverage.subscription_allowance_id == allowance.id
        ):
            continue
        targets.append(
            AttendanceCoverageTarget(
                key=f"allowance:{allowance.id}",
                label=(
                    "Абонемент · "
                    f"{allowance.subscription.plan_name_snapshot} · "
                    f"остаток {eligible.balance} · "
                    f"до {allowance.subscription.valid_until:%d.%m.%Y}"
                ),
                subscription_allowance_id=allowance.id,
            )
        )

    return tuple(targets)
