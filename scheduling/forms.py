from __future__ import annotations

from django import forms

from accounts.models import CoachProfile

from .models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue


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
    reason = forms.ChoiceField(choices=Lesson.CancellationReason.choices, label="Причина")


class ManagerLessonRescheduleForm(forms.Form):
    new_starts_at = forms.DateTimeField(
        label="Новое начало",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    new_ends_at = forms.DateTimeField(
        label="Новое окончание",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    reason = forms.ChoiceField(choices=Lesson.CancellationReason.choices, label="Причина")
