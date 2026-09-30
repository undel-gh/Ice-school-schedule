from __future__ import annotations

from django import forms

from attendance.models import Attendance
from subscriptions.models import (
    AbsenceCompensationPolicy,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
)
from accounts.models import Student
from scheduling.models import Lesson


class AttendanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return (
            f"{obj.student.display_name} · "
            f"{obj.lesson.starts_at:%d.%m.%Y %H:%M} · "
            f"{obj.lesson.lesson_type.name}"
        )


class LessonChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return (
            f"{obj.starts_at:%d.%m.%Y %H:%M} · "
            f"{obj.group.name} · {obj.lesson_type.name}"
        )


class SubscriptionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.valid_from is None:
            period = "ожидает активации"
        else:
            period = f"{obj.valid_from:%d.%m.%Y}—{obj.valid_until:%d.%m.%Y}"
        return (
            f"{obj.student.display_name} · "
            f"{obj.plan_name_snapshot} · {period}"
        )


class AllowanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        subscription = obj.subscription
        if subscription.valid_from is None:
            period = "ожидает активации"
        else:
            period = (
                f"{subscription.valid_from:%d.%m.%Y}—"
                f"{subscription.valid_until:%d.%m.%Y}"
            )
        return (
            f"{subscription.student.display_name} · "
            f"{subscription.plan_name_snapshot} · "
            f"{obj.category.upper()} · {period}"
        )


class ManagerCompensationCaseCreateForm(forms.Form):
    attendance = AttendanceChoiceField(
        queryset=Attendance.objects.none(),
        label="Пропуск",
    )
    absence_reason = forms.ChoiceField(
        choices=AbsenceCompensationPolicy.AbsenceReason.choices,
        label="Причина",
    )
    policy_code = forms.CharField(
        label="Код policy",
        required=False,
        help_text="Оставьте пустым для автоматического выбора policy.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attendance"].queryset = (
            Attendance.objects.filter(status=Attendance.Status.ABSENT)
            .select_related("student", "lesson__lesson_type")
            .order_by("-lesson__starts_at", "student__display_name")
        )


class ManagerPaidMakeupAuthorizeForm(forms.Form):
    target_subscription = SubscriptionChoiceField(
        queryset=Subscription.objects.none(),
        label="Target Subscription",
        required=False,
    )
    fee_confirmed = forms.BooleanField(
        label="Оплата уже подтверждена",
        required=False,
    )

    def __init__(self, *args, student_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Subscription.objects.filter(
            cancelled_at__isnull=True,
        ).select_related("student", "billing_period")
        if student_id is not None:
            queryset = queryset.filter(student_id=student_id)
        self.fields["target_subscription"].queryset = queryset.order_by(
            "valid_from", "created_at", "id"
        )


class ManagerPaidMakeupActivateForm(forms.Form):
    target_subscription = SubscriptionChoiceField(
        queryset=Subscription.objects.none(),
        label="Target Subscription",
        required=False,
    )

    def __init__(self, *args, student_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Subscription.objects.filter(
            cancelled_at__isnull=True
        ).select_related("student")
        if student_id is not None:
            queryset = queryset.filter(student_id=student_id)
        self.fields["target_subscription"].queryset = queryset.order_by(
            "valid_from", "created_at", "id"
        )


class ManagerCompensationReverseForm(forms.Form):
    reason = forms.CharField(label="Причина reversal", max_length=128)
    refund_required = forms.ChoiceField(
        label="Возврат оплаты",
        required=False,
        choices=(
            ("", "Не применимо / оплаты не было"),
            ("yes", "Да, требуется возврат"),
            ("no", "Нет, возврат не требуется"),
        ),
    )

    def refund_value(self):
        value = self.cleaned_data["refund_required"]
        if value == "yes":
            return True
        if value == "no":
            return False
        return None


class ManagerOneTimeEntitlementForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Ученик",
    )
    lesson = LessonChoiceField(
        queryset=Lesson.objects.none(),
        label="Занятие",
    )
    entitlement_type = forms.ChoiceField(
        choices=OneTimeEntitlement.Type.choices,
        label="Тип права",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(
            is_active=True
        ).order_by("display_name", "id")
        self.fields["lesson"].queryset = (
            Lesson.objects.exclude(status=Lesson.Status.CANCELLED)
            .select_related("group", "lesson_type")
            .order_by("-starts_at", "id")
        )


class ManagerAdministrativeMakeupForm(forms.Form):
    source_subscription_allowance = AllowanceChoiceField(
        queryset=SubscriptionAllowance.objects.none(),
        label="Source allowance",
    )
    source_lesson = LessonChoiceField(
        queryset=Lesson.objects.none(),
        label="Source Lesson",
    )
    valid_from = forms.DateField(
        label="Действует с",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    valid_until = forms.DateField(
        label="Действует по",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_lesson = LessonChoiceField(
        queryset=Lesson.objects.none(),
        required=False,
        label="Target Lesson",
    )
    reason = forms.CharField(label="Причина", max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_subscription_allowance"].queryset = (
            SubscriptionAllowance.objects.select_related(
                "subscription__student"
            ).order_by(
                "subscription__student__display_name",
                "subscription__valid_until",
                "category",
                "id",
            )
        )
        lessons = Lesson.objects.exclude(
            status=Lesson.Status.CANCELLED
        ).select_related("group", "lesson_type").order_by("-starts_at", "id")
        self.fields["source_lesson"].queryset = lessons
        self.fields["target_lesson"].queryset = lessons
