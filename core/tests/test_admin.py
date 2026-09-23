from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from attendance.admin import AttendanceAdmin
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from core.admin import ReadOnlyAdmin
from scheduling.admin import LessonAdmin
from scheduling.models import Lesson, LessonEnrollment, LessonResponse, LessonRosterEntry
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)

User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="admin-safety",
        password="test",
        is_staff=True,
    )


@pytest.fixture
def admin_request(staff_user):
    request = RequestFactory().get("/admin/")
    request.user = staff_user
    return request


@pytest.mark.django_db
@pytest.mark.parametrize(
    "model",
    [
        Attendance,
        AbsenceJustification,
        Lesson,
        LessonEnrollment,
        LessonRosterEntry,
        LessonResponse,
        Subscription,
        SubscriptionAllowance,
        OneTimeEntitlement,
        MakeupEntitlement,
        AttendanceCoverage,
        SubscriptionLedgerEntry,
        AuditEvent,
    ],
)
def test_operational_models_are_registered_read_only(model, admin_request):
    model_admin = admin.site._registry[model]

    assert isinstance(model_admin, ReadOnlyAdmin)
    assert model_admin.has_add_permission(admin_request) is False
    assert model_admin.has_delete_permission(admin_request) is False
    assert model_admin.has_change_permission(admin_request) is True

    readonly = set(model_admin.get_readonly_fields(admin_request))
    model_fields = {field.name for field in model._meta.fields}
    assert model_fields <= readonly


@pytest.mark.django_db
def test_attendance_admin_exposes_service_backed_actions(admin_request):
    model_admin = admin.site._registry[Attendance]

    assert isinstance(model_admin, AttendanceAdmin)
    assert set(model_admin.actions) == {"mark_present", "mark_absent"}


@pytest.mark.django_db
def test_lesson_admin_exposes_reopen_action(admin_request):
    model_admin = admin.site._registry[Lesson]

    assert isinstance(model_admin, LessonAdmin)
    assert "reopen_selected_attendance" in model_admin.actions
