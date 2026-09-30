from __future__ import annotations

from django import forms

from accounts.models import Student

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
        self.fields["student"].queryset = Student.objects.order_by(
            "-is_active", "display_name", "id"
        )
        self.fields["group"].queryset = TrainingGroup.objects.order_by(
            "-is_active", "name", "id"
        )
        if membership is not None:
            self.fields["student"].initial = membership.student_id
            self.fields["group"].initial = membership.group_id
            self.fields["student"].disabled = True
            self.fields["group"].disabled = True
