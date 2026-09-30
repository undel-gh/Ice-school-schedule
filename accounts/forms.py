from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from .models import CoachProfile, Student, StudentAccess

User = get_user_model()


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        label = obj.get_full_name().strip() or obj.username
        if obj.email:
            return f"{label} · {obj.email}"
        return label


class StudentForm(forms.Form):
    display_name = forms.CharField(label="Отображаемое имя", max_length=100)
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)


class StudentAccessForm(forms.Form):
    user = UserChoiceField(queryset=User.objects.none(), label="Пользователь")
    role = forms.ChoiceField(
        choices=(
            (StudentAccess.Role.SELF, "Сам ученик"),
            (StudentAccess.Role.GUARDIAN, "Родитель / представитель"),
        ),
        label="Роль",
    )
    is_active = forms.BooleanField(label="Доступ активен", required=False, initial=True)

    def __init__(self, *args, student=None, access=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.filter(is_active=True).order_by("username", "id")
        if access is None and student is not None:
            queryset = queryset.exclude(
                student_accesses__student=student
            )
        self.fields["user"].queryset = queryset
        if access is not None:
            self.fields["user"].initial = access.user_id
            self.fields["user"].disabled = True


class CoachProfileForm(forms.Form):
    user = UserChoiceField(queryset=User.objects.none(), label="Пользователь")
    display_name = forms.CharField(label="Имя тренера", max_length=100)
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)

    def __init__(self, *args, coach=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.filter(is_active=True).order_by("username", "id")
        if coach is None:
            queryset = queryset.filter(coach_profile__isnull=True)
        self.fields["user"].queryset = queryset
        if coach is not None:
            self.fields["user"].initial = coach.user_id
            self.fields["user"].disabled = True
