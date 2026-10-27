from __future__ import annotations

import re
from types import SimpleNamespace

from django.apps import apps
from django.contrib import admin
from django.test import RequestFactory
from django.utils import translation

from attendance.admin import AttendanceAdmin
from core.admin_localization import (
    AdminLocalizationMiddleware,
    MODEL_LABELS,
    apply_admin_localization,
)
from scheduling.admin import LessonAdmin


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def test_project_app_names_are_localized():
    assert apps.get_app_config("core").verbose_name == "Система"
    assert apps.get_app_config("accounts").verbose_name == "Пользователи и доступы"
    assert apps.get_app_config("scheduling").verbose_name == "Расписание"
    assert apps.get_app_config("attendance").verbose_name == "Посещаемость"
    assert apps.get_app_config("subscriptions").verbose_name == "Абонементы и компенсации"
    assert apps.get_app_config("audit").verbose_name == "Журнал действий"


def test_admin_model_and_field_labels_are_russian():
    apply_admin_localization()

    with translation.override("ru"):
        for model_label, expected in MODEL_LABELS.items():
            model = apps.get_model(model_label)
            assert str(model._meta.verbose_name) == expected[0]
            assert str(model._meta.verbose_name_plural) == expected[1]

            for field in model._meta.get_fields():
                if getattr(field, "auto_created", False) and not getattr(field, "concrete", False):
                    continue
                label = str(getattr(field, "verbose_name", ""))
                assert label == "ID" or CYRILLIC_RE.search(label), (
                    model_label,
                    field.name,
                    label,
                )


def test_admin_middleware_applies_localization_only_to_admin_requests(monkeypatch):
    import core.admin_localization as localization

    calls = []
    monkeypatch.setattr(localization, "apply_admin_localization", lambda: calls.append(True))
    middleware = AdminLocalizationMiddleware(lambda request: None)

    request = RequestFactory().get("/admin/")
    request.resolver_match = SimpleNamespace(app_name="admin")
    middleware.process_view(request, None, (), {})
    assert calls == [True]

    request.resolver_match = SimpleNamespace(app_name="scheduling")
    middleware.process_view(request, None, (), {})
    assert calls == [True]


def test_custom_admin_actions_have_russian_descriptions():
    attendance_admin = admin.site._registry[next(
        model for model in admin.site._registry if model._meta.label == "attendance.Attendance"
    )]
    lesson_admin = admin.site._registry[next(
        model for model in admin.site._registry if model._meta.label == "scheduling.Lesson"
    )]

    assert isinstance(attendance_admin, AttendanceAdmin)
    assert AttendanceAdmin.mark_present.short_description == "Отметить выбранных как присутствовавших"
    assert AttendanceAdmin.mark_absent.short_description == "Отметить выбранных как отсутствовавших"
    assert isinstance(lesson_admin, LessonAdmin)
    assert LessonAdmin.reopen_selected_attendance.short_description == "Повторно открыть посещаемость выбранных закрытых занятий"
