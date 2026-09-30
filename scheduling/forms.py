from __future__ import annotations

from datetime import datetime

from django import forms
from django.core.exceptions import ValidationError

from core.presentation import localized_choices
from core.time import make_school_aware, school_timezone

from accounts.models import CoachProfile

from .models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue


class SchoolDateTimeField(forms.DateTimeField):
    """Parse browser datetime-local values in the school timezone."""

    def prepare_value(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(school_timezone())
            return make_school_aware(value)
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(school_timezone())
            return make_school_aware(value)
        if isinstance(value, str):
            for input_format in self.input_formats:
                try:
                    parsed = datetime.strptime(value, input_format)
                except (TypeError, ValueError):
                    continue
                return make_school_aware(parsed)
        raise ValidationError(
            self.error_messages["invalid"],
            code="invalid",
        )


WEEKDAY_CHOICES = (
    (0, "Понедельник"),
    (1, "Вторник"),
    (2, "Среда"),
    (3, "Четверг"),
    (4, "Пятница"),
    (5, "Суббота"),
    (6, "Воскресенье"),
)


class ManagerScheduleTemplateForm(forms.Form):
    group = forms.ModelChoiceField(queryset=TrainingGroup.objects.none(), label="Группа")
    lesson_type = forms.ModelChoiceField(queryset=LessonType.objects.none(), label="Тип занятия")
    coach = forms.ModelChoiceField(queryset=CoachProfile.objects.none(), label="Тренер")
    venue = forms.ModelChoiceField(queryset=Venue.objects.none(), label="Площадка")
    weekday = forms.TypedChoiceField(choices=WEEKDAY_CHOICES, coerce=int, label="День недели")
    start_time = forms.TimeField(label="Время начала", widget=forms.TimeInput(attrs={"type": "time"}))
    duration_minutes = forms.IntegerField(label="Длительность, минут", min_value=1)
    valid_from = forms.DateField(label="Действует с", widget=forms.DateInput(attrs={"type": "date"}))
    valid_until = forms.DateField(label="Действует по", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    minimum_attendees_override = forms.IntegerField(label="Минимум участников", required=False, min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = TrainingGroup.objects.filter(is_active=True).order_by("name", "id")
        self.fields["lesson_type"].queryset = LessonType.objects.filter(is_active=True).order_by("name", "id")
        self.fields["coach"].queryset = CoachProfile.objects.filter(is_active=True).order_by("display_name", "id")
        self.fields["venue"].queryset = Venue.objects.filter(is_active=True).order_by("name", "id")


class ManagerScheduleTemplateVersionForm(ManagerScheduleTemplateForm):
    effective_from = forms.DateField(
        label="Новая версия действует с",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    valid_from = None
    valid_until = None


class ManagerLessonCancelForm(forms.Form):
    reason = forms.ChoiceField(
        choices=localized_choices(
            "lesson_cancellation_reason",
            Lesson.CancellationReason.choices,
        ),
        label="Причина",
    )


class ManagerLessonRescheduleForm(forms.Form):
    new_starts_at = SchoolDateTimeField(
        label="Новое начало",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    new_ends_at = SchoolDateTimeField(
        label="Новое окончание",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    reason = forms.ChoiceField(
        choices=localized_choices(
            "lesson_cancellation_reason",
            Lesson.CancellationReason.choices,
        ),
        label="Причина",
    )


class ManagerLessonCoachReassignForm(forms.Form):
    coach = forms.ModelChoiceField(
        queryset=CoachProfile.objects.none(),
        label="Новый тренер",
    )
    reason = forms.CharField(
        label="Причина замены",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, lesson: Lesson | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = CoachProfile.objects.filter(is_active=True).order_by(
            "display_name", "id"
        )
        if lesson is not None:
            queryset = queryset.exclude(pk=lesson.coach_id)
        self.fields["coach"].queryset = queryset
