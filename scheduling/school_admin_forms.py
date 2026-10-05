from __future__ import annotations

from django import forms

from accounts.models import Student
from core.choices import SubscriptionCategory
from core.presentation import localized_choices

from .models import GroupMembership, TrainingGroup


class StudentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_name if obj.is_active else f"{obj.display_name} · неактивен"


class GroupChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name if obj.is_active else f"{obj.name} · неактивна"


class TrainingGroupForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(label="Название", max_length=128)
    default_minimum_attendees = forms.IntegerField(
        label="Минимум участников по умолчанию",
        min_value=1,
    )
    capacity = forms.IntegerField(
        label="Вместимость",
        min_value=1,
        required=False,
        help_text="Пусто — вместимость не ограничивается системой.",
    )
    is_active = forms.BooleanField(label="Активна", required=False, initial=True)


class LessonTypeForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(label="Название", max_length=128)
    subscription_category = forms.ChoiceField(
        label="Категория абонемента",
        choices=localized_choices("subscription_category", SubscriptionCategory.choices),
    )
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)


class VenueForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(
        label="Название зала / площадки",
        max_length=128,
        help_text="Например: «Зал хореографии» или «Лёд A».",
    )
    address = forms.CharField(label="Адрес", max_length=255, required=False)
    floor = forms.CharField(
        label="Этаж",
        max_length=32,
        required=False,
        help_text="Например: 2, 1A, -1 или цоколь.",
    )
    is_active = forms.BooleanField(label="Активна", required=False, initial=True)


class GroupMembershipForm(forms.Form):
    student = StudentChoiceField(queryset=Student.objects.none(), label="Ученик")
    group = GroupChoiceField(queryset=TrainingGroup.objects.none(), label="Группа")
    starts_on = forms.DateField(
        label="С",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    ends_on = forms.DateField(
        label="По",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, membership: GroupMembership | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if membership is None:
            self.fields["student"].queryset = Student.objects.filter(
                is_active=True
            ).order_by("display_name", "id")
            self.fields["group"].queryset = TrainingGroup.objects.filter(
                is_active=True
            ).order_by("name", "id")
        else:
            self.fields["student"].queryset = Student.objects.filter(
                pk=membership.student_id
            )
            self.fields["group"].queryset = TrainingGroup.objects.filter(
                pk=membership.group_id
            )
            self.fields["student"].initial = membership.student_id
            self.fields["group"].initial = membership.group_id
            self.fields["student"].disabled = True
            self.fields["group"].disabled = True
