from __future__ import annotations

from django import forms
from django.db.models import Q

from core.choices import SubscriptionCategory

from .models import (
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    SubscriptionPeriodScheme,
)


PERIOD_MODE_CHOICES = (
    (SubscriptionPeriodScheme.Mode.CALENDAR_MONTH, "Календарный месяц"),
    (
        SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
        "28 дней от первого занятия",
    ),
    (
        SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
        "Общие 28-дневные периоды",
    ),
)

ABSENCE_REASON_CHOICES = (
    (AbsenceCompensationPolicy.AbsenceReason.MEDICAL, "Медицинская"),
    (AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED, "Без уважительной причины"),
    (AbsenceCompensationPolicy.AbsenceReason.OTHER, "Другая"),
)

JUSTIFICATION_CHOICES = (
    (AbsenceCompensationPolicy.JustificationRequirement.NONE, "Не требуется"),
    (
        AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL,
        "Подтверждённая медицинская справка",
    ),
)

LIMIT_SCOPE_CHOICES = (
    (AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD, "Ученик + период"),
    (
        AbsenceCompensationPolicy.LimitScope.CATEGORY_PERIOD,
        "Ученик + категория + период",
    ),
    (
        AbsenceCompensationPolicy.LimitScope.LESSON_TYPE_PERIOD,
        "Ученик + тип занятия + период",
    ),
)

ACTION_TYPE_CHOICES = (
    (AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP, "Бесплатная отработка"),
    (AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP, "Платная отработка"),
    (
        AbsenceCompensationPolicyAction.ActionType.BILLING_RECALCULATION,
        "Перерасчёт оплаты",
    ),
)

TARGET_PERIOD_CHOICES = (
    (
        AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD,
        "Текущий период",
    ),
    (
        AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD,
        "Следующий период ученика",
    ),
    (
        AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW,
        "Явное окно",
    ),
)

REQUIREMENT_CHOICES = (
    (AbsenceCompensationPolicyAction.Requirement.NONE, "Нет"),
    (AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED, "Требуется оплата"),
    (
        AbsenceCompensationPolicyAction.Requirement.TARGET_SUBSCRIPTION_REQUIRED,
        "Требуется целевой абонемент",
    ),
    (
        AbsenceCompensationPolicyAction.Requirement.FEE_AND_TARGET_SUBSCRIPTION_REQUIRED,
        "Требуются оплата и целевой абонемент",
    ),
)


class ManagerPeriodSchemeForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(label="Название", max_length=128)
    mode = forms.ChoiceField(label="Модель периода", choices=PERIOD_MODE_CHOICES)
    fixed_anchor_date = forms.DateField(
        label="Якорная дата 28-дневного цикла",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Заполняется только для общих 28-дневных периодов.",
    )
    is_active = forms.BooleanField(label="Активна", required=False, initial=True)

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        anchor = cleaned.get("fixed_anchor_date")
        if mode == SubscriptionPeriodScheme.Mode.FIXED_28_DAYS and anchor is None:
            self.add_error(
                "fixed_anchor_date",
                "Для общего 28-дневного цикла требуется якорная дата.",
            )
        if mode and mode != SubscriptionPeriodScheme.Mode.FIXED_28_DAYS and anchor:
            self.add_error(
                "fixed_anchor_date",
                "Якорная дата допустима только для общего 28-дневного цикла.",
            )
        return cleaned


class ManagerSubscriptionPlanForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(label="Название", max_length=128)
    period_scheme = forms.ModelChoiceField(
        queryset=SubscriptionPeriodScheme.objects.none(),
        label="Модель расчётного периода",
        required=False,
    )
    ice_visit_limit = forms.IntegerField(
        label="Лёд, посещений",
        min_value=1,
        required=False,
    )
    hall_visit_limit = forms.IntegerField(
        label="Зал, посещений",
        min_value=1,
        required=False,
    )
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)

    def __init__(self, *args, current_scheme_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = SubscriptionPeriodScheme.objects.filter(is_active=True)
        if current_scheme_id is not None:
            queryset = SubscriptionPeriodScheme.objects.filter(
                Q(is_active=True) | Q(pk=current_scheme_id)
            )
        self.fields["period_scheme"].queryset = queryset.order_by(
            "-is_active", "name", "id"
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("ice_visit_limit") and not cleaned.get("hall_visit_limit"):
            raise forms.ValidationError(
                "У тарифа должен быть хотя бы один лимит: лёд или зал."
            )
        if cleaned.get("is_active") and cleaned.get("period_scheme") is None:
            self.add_error(
                "period_scheme",
                "Активному тарифу нужна модель расчётного периода.",
            )
        return cleaned

    def allowances(self):
        return {
            SubscriptionCategory.ICE: self.cleaned_data.get("ice_visit_limit"),
            SubscriptionCategory.HALL: self.cleaned_data.get("hall_visit_limit"),
        }


class ManagerCompensationPolicyForm(forms.Form):
    code = forms.SlugField(label="Код", max_length=64)
    name = forms.CharField(label="Название", max_length=128)
    absence_reason = forms.ChoiceField(
        label="Причина отсутствия",
        choices=ABSENCE_REASON_CHOICES,
    )
    justification_requirement = forms.ChoiceField(
        label="Подтверждение",
        choices=JUSTIFICATION_CHOICES,
    )
    max_eligible_absences = forms.IntegerField(
        label="Максимум компенсируемых пропусков",
        min_value=1,
        required=False,
        help_text="Пусто — без ограничения.",
    )
    limit_scope = forms.ChoiceField(
        label="Область лимита",
        choices=LIMIT_SCOPE_CHOICES,
    )
    effective_from = forms.DateField(
        label="Действует с",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    effective_until = forms.DateField(
        label="Действует по",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    is_active = forms.BooleanField(label="Активна", required=False, initial=True)


class ManagerCompensationPolicyVersionForm(forms.Form):
    name = forms.CharField(label="Название", max_length=128)
    justification_requirement = forms.ChoiceField(
        label="Подтверждение",
        choices=JUSTIFICATION_CHOICES,
    )
    max_eligible_absences = forms.IntegerField(
        label="Максимум компенсируемых пропусков",
        min_value=1,
        required=False,
        help_text="Пусто — без ограничения.",
    )
    limit_scope = forms.ChoiceField(
        label="Область лимита",
        choices=LIMIT_SCOPE_CHOICES,
    )
    effective_from = forms.DateField(
        label="Новая версия действует с",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Дата должна быть позже текущего дня.",
    )
    effective_until = forms.DateField(
        label="Новая версия действует по",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class ManagerCompensationPolicyActionForm(forms.Form):
    action_type = forms.ChoiceField(label="Действие", choices=ACTION_TYPE_CHOICES)
    target_period_rule = forms.ChoiceField(
        label="Целевой период",
        choices=TARGET_PERIOD_CHOICES,
    )
    requirement = forms.ChoiceField(
        label="Дополнительное условие",
        choices=REQUIREMENT_CHOICES,
    )
    validity_days = forms.IntegerField(
        label="Срок действия, дней",
        min_value=1,
        required=False,
    )
    priority = forms.IntegerField(label="Приоритет", min_value=1, initial=100)
    is_active = forms.BooleanField(label="Активно", required=False, initial=True)


class ManagerCompensationPolicyWindowForm(forms.Form):
    name = forms.CharField(label="Название", max_length=128)
    source_from = forms.DateField(
        label="Пропуски с",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    source_until = forms.DateField(
        label="Пропуски по",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_from = forms.DateField(
        label="Отработка с",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_until = forms.DateField(
        label="Отработка по",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    requirement_override = forms.ChoiceField(
        label="Переопределить условие",
        required=False,
        choices=(("", "Наследовать из действия"),) + REQUIREMENT_CHOICES,
    )
    priority = forms.IntegerField(label="Приоритет", min_value=1, initial=100)
    is_active = forms.BooleanField(label="Активно", required=False, initial=True)
