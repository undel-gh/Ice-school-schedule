from __future__ import annotations

from django import forms

from accounts.models import Student
from scheduling.models import TrainingGroup

from .models import SubscriptionPeriodScheme, SubscriptionPlan


class ManagerSubscriptionIssueForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Ученик",
    )
    plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.none(),
        label="Абонемент",
    )
    reference_date = forms.DateField(
        label="Расчётная дата",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(
            is_active=True
        ).order_by("display_name", "id")
        self.fields["plan"].queryset = (
            SubscriptionPlan.objects.filter(
                is_active=True,
                period_scheme__isnull=False,
                period_scheme__is_active=True,
            )
            .select_related("period_scheme")
            .order_by("name", "id")
        )


class ManagerPlaceHoldCreateForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Ученик",
    )
    group = forms.ModelChoiceField(
        queryset=TrainingGroup.objects.none(),
        label="Группа",
    )
    period_scheme = forms.ModelChoiceField(
        queryset=SubscriptionPeriodScheme.objects.none(),
        label="Модель расчётного периода",
    )
    reference_date = forms.DateField(
        label="Первый день периода / расчётная дата",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(
            is_active=True
        ).order_by("display_name", "id")
        self.fields["group"].queryset = TrainingGroup.objects.filter(
            is_active=True
        ).order_by("name", "id")
        self.fields["period_scheme"].queryset = (
            SubscriptionPeriodScheme.objects.filter(
                is_active=True
            ).order_by("name", "id")
        )


class ManagerPlaceHoldCancelForm(forms.Form):
    reason = forms.CharField(
        label="Причина отмены",
        max_length=128,
        widget=forms.TextInput(
            attrs={"placeholder": "Например: услуга оформлена ошибочно"}
        ),
    )



class ManagerAllowanceAdjustmentForm(forms.Form):
    delta = forms.IntegerField(
        label="Изменение остатка",
        help_text="Положительное значение добавляет посещения, отрицательное списывает.",
    )
    reason = forms.CharField(
        label="Причина",
        max_length=255,
    )


class ManagerSubscriptionCancelForm(forms.Form):
    confirm = forms.BooleanField(
        label="Подтверждаю отмену абонемента",
    )
