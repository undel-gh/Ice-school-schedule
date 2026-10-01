from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from .models import AccountInvitation, CoachProfile, Student, StudentAccess

User = get_user_model()


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_label


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
        if access is not None:
            queryset = User.objects.filter(pk=access.user_id)
        else:
            queryset = User.objects.filter(is_active=True).order_by(
                "username", "id"
            )
            if student is not None:
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
        if coach is not None:
            queryset = User.objects.filter(pk=coach.user_id)
        else:
            queryset = User.objects.filter(
                is_active=True,
                coach_profile__isnull=True,
            ).order_by("username", "id")
        self.fields["user"].queryset = queryset
        if coach is not None:
            self.fields["user"].initial = coach.user_id
            self.fields["user"].disabled = True



class AccountInvitationForm(forms.Form):
    kind = forms.ChoiceField(
        label="Кому доступ",
        choices=(
            (AccountInvitation.Kind.STUDENT_ACCESS, "Ученик / родитель"),
            (AccountInvitation.Kind.COACH, "Тренер"),
            (AccountInvitation.Kind.RECOVERY, "Восстановление доступа"),
        ),
    )
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Ученик",
        required=False,
    )
    student_access_role = forms.ChoiceField(
        label="Роль доступа",
        required=False,
        choices=(
            ("", "—"),
            (StudentAccess.Role.SELF, "Сам ученик"),
            (StudentAccess.Role.GUARDIAN, "Родитель / представитель"),
        ),
    )
    recovery_user = UserChoiceField(
        queryset=User.objects.none(),
        label="Существующий аккаунт",
        required=False,
        help_text=(
            "Ссылка восстановления — секретный одноразовый токен. "
            "При компрометации передавайте её только по независимому доверенному "
            "каналу: лично, по телефону или через другой проверенный мессенджер/аккаунт."
        ),
    )
    account_display_name = forms.CharField(
        label="Как подписать аккаунт",
        max_length=100,
        required=False,
        help_text="Например: «Мама Ани» или «Папа Ильи».",
    )
    coach_display_name = forms.CharField(
        label="Имя тренера",
        max_length=100,
        required=False,
    )
    expires_in_hours = forms.IntegerField(
        label="Срок действия ссылки, часов",
        min_value=1,
        max_value=24 * 30,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(
            is_active=True
        ).order_by("display_name", "id")
        self.fields["recovery_user"].queryset = User.objects.filter(
            is_active=False,
            is_staff=False,
            is_superuser=False,
        ).order_by("display_name", "username", "id")
        self.fields["expires_in_hours"].initial = getattr(
            settings,
            "ACCOUNT_INVITATION_TTL_HOURS",
            168,
        )

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        if kind == AccountInvitation.Kind.STUDENT_ACCESS:
            if cleaned.get("student") is None:
                self.add_error("student", "Выберите ученика.")
            if cleaned.get("student_access_role") not in StudentAccess.Role.values:
                self.add_error("student_access_role", "Выберите роль доступа.")
            if not (cleaned.get("account_display_name") or "").strip():
                self.add_error(
                    "account_display_name",
                    "Укажите понятную подпись аккаунта.",
                )
            cleaned["coach_display_name"] = ""
            cleaned["recovery_user"] = None
        elif kind == AccountInvitation.Kind.COACH:
            coach_name = (cleaned.get("coach_display_name") or "").strip()
            if not coach_name:
                self.add_error("coach_display_name", "Укажите имя тренера.")
            cleaned["account_display_name"] = coach_name
            cleaned["student"] = None
            cleaned["student_access_role"] = ""
            cleaned["recovery_user"] = None
        elif kind == AccountInvitation.Kind.RECOVERY:
            recovery_user = cleaned.get("recovery_user")
            if recovery_user is None:
                self.add_error(
                    "recovery_user",
                    "Выберите существующий неактивный аккаунт.",
                )
            elif recovery_user.is_active:
                self.add_error(
                    "recovery_user",
                    "Для восстановления сначала деактивируйте аккаунт.",
                )
            else:
                cleaned["account_display_name"] = recovery_user.display_label
            cleaned["student"] = None
            cleaned["student_access_role"] = ""
            cleaned["coach_display_name"] = ""
        return cleaned
