from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext

from core.ui_messages import localize_domain_message


def localize_message(message: str) -> str:
    """Translate a user-visible domain message at the presentation boundary."""
    raw = str(message)
    translated = gettext(raw)
    if translated != raw:
        return translated
    return localize_domain_message(raw)


def validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return " ".join(
            localize_message(message)
            for messages_ in exc.message_dict.values()
            for message in messages_
        )
    return " ".join(localize_message(message) for message in exc.messages)


MANAGER_LABELS = {
    "lesson_status": {
        "draft": "Черновик",
        "rsvp_open": "Сбор ответов",
        "confirmed": "Подтверждено",
        "completed": "Проведено",
        "closed": "Закрыто",
        "cancelled": "Отменено",
    },
    "lesson_cancellation_reason": {
        "low_attendance": "Недостаточно участников",
        "coach_unavailable": "Тренер недоступен",
        "venue_unavailable": "Площадка недоступна",
        "administrative": "Административная причина",
        "other": "Другая причина",
    },
    "compensation_status": {
        "open": "Открыт",
        "materialized": "Право выдано",
        "reversed": "Отменён",
        "cancelled": "Закрыт",
    },
    "compensation_eligibility": {
        "eligible": "Доступна",
        "limit_exceeded": "Лимит исчерпан",
        "undetermined": "Не определено",
    },
    "absence_reason": {
        "medical": "Медицинская причина",
        "unexcused": "Без уважительной причины",
        "other": "Другая причина",
    },
    "medical_status": {
        "pending": "Ожидает проверки",
        "verified": "Подтверждено",
        "rejected": "Отклонено",
        "revoked": "Отозвано",
    },
    "one_time_entitlement": {
        "single_ice": "Разовое занятие на льду",
        "single_hall": "Разовое занятие в зале",
        "individual_ice": "Индивидуальное занятие на льду",
        "mini_group_ice": "Мини-группа на льду",
        "trial_ice": "Пробное занятие на льду",
    },
    "subscription_category": {
        "ice": "Лёд",
        "hall": "Зал",
    },
    "subscription_period_mode": {
        "calendar_month": "Календарный месяц",
        "rolling_28_first_lesson": "28 дней от первого занятия",
        "fixed_28_days": "Общие 28-дневные периоды",
    },
    "policy_justification": {
        "none": "Не требуется",
        "verified_medical": "Подтверждённая медицинская справка",
    },
    "policy_limit_scope": {
        "student_period": "Ученик + период",
        "category_period": "Ученик + категория + период",
        "lesson_type_period": "Ученик + тип занятия + период",
    },
    "policy_action_type": {
        "free_makeup": "Бесплатная отработка",
        "paid_makeup": "Платная отработка",
        "billing_recalculation": "Перерасчёт оплаты",
    },
    "policy_target_period": {
        "current_period": "Текущий период",
        "next_student_period": "Следующий период ученика",
        "explicit_target_window": "Явное окно",
    },
    "policy_requirement": {
        "none": "Нет дополнительных условий",
        "fee_required": "Требуется оплата",
        "target_subscription_required": "Требуется целевой абонемент",
        "fee_and_target_subscription_required": "Требуются оплата и целевой абонемент",
    },
,
    "subscription_period_state": {
        "pending": "Ожидает активации",
        "active": "Активен",
    },
    "subscription_state": {
        "pending": "Ожидает активации",
        "upcoming": "Будущий",
        "active": "Действует",
        "expired": "Истёк",
        "cancelled": "Отменён",
    },
    "rolling_recovery_reason": {
        "active_coverages_remain": "остались активные покрытия посещений",
        "dependent_rights_exist": "остались зависимые права на отработку или компенсацию",
    },
    "ledger_reason": {
        "Initial subscription grant": "Начальное начисление по абонементу",
        "Attendance covered by subscription allowance": "Списание за посещение по абонементу",
        "Attendance covered by make-up entitlement": "Списание за посещение по отработке",
        "Attendance coverage reversed": "Возврат после отмены покрытия посещения",
        "Attendance coverage rebound": "Перенос списания на другой источник",
    },
    "audit_aggregate": {
        "AbsenceCompensationActionGrant": "Действие компенсации",
        "AbsenceCompensationCase": "Компенсационный случай",
        "AbsenceCompensationPolicy": "Политика компенсаций",
        "AbsenceCompensationPolicyAction": "Действие политики компенсаций",
        "AbsenceCompensationPolicyWindow": "Сезонное окно политики",
        "AbsenceJustification": "Основание отсутствия",
        "AccountInvitation": "Приглашение",
        "AttendanceCoverage": "Покрытие посещения",
        "CoachProfile": "Профиль тренера",
        "ExternalIdentity": "Внешняя учётная запись",
        "GroupMembership": "Участие в группе",
        "GroupPlaceHold": "Сохранение места",
        "GroupSeatReservation": "Бронь места",
        "Lesson": "Занятие",
        "LessonEnrollment": "Запись на занятие",
        "LessonResponse": "Ответ на занятие",
        "MakeupEntitlement": "Право на отработку",
        "OneTimeEntitlement": "Разовое право",
        "ScheduleTemplate": "Шаблон расписания",
        "Student": "Ученик",
        "StudentAccess": "Доступ к ученику",
        "Subscription": "Абонемент",
        "SubscriptionAllowance": "Лимит абонемента",
        "SubscriptionPeriod": "Расчётный период",
        "SubscriptionPeriodScheme": "Модель расчётного периода",
        "SubscriptionPlan": "Тариф",
        "TrainingGroup": "Группа",
        "User": "Пользователь",
    },
    "audit_event": {
        "AbsenceCompensationActionReversed": "Действие компенсации отменено",
        "AbsenceCompensationCaseCancelled": "Компенсационный случай закрыт",
        "AbsenceCompensationCaseCreated": "Компенсационный случай создан",
        "AbsenceCompensationCaseReversed": "Компенсационный случай отменён",
        "AbsenceCompensationEvaluated": "Право на компенсацию рассчитано",
        "AbsenceCompensationMaterialized": "Компенсационное право выдано",
        "AbsenceCompensationPolicyActionChanged": "Действие политики компенсаций изменено",
        "AbsenceCompensationPolicyActionCreated": "Действие политики компенсаций создано",
        "AbsenceCompensationPolicyChanged": "Политика компенсаций изменена",
        "AbsenceCompensationPolicyCreated": "Политика компенсаций создана",
        "AbsenceCompensationPolicyEnded": "Действие версии политики завершено",
        "AbsenceCompensationPolicyVersioned": "Создана новая версия политики компенсаций",
        "AbsenceCompensationPolicyWindowChanged": "Сезонное окно изменено",
        "AbsenceCompensationPolicyWindowCreated": "Сезонное окно создано",
        "AbsenceJustificationRejected": "Медицинское основание отклонено",
        "AbsenceJustificationRevoked": "Медицинское основание отозвано",
        "AbsenceJustificationVerified": "Медицинское основание подтверждено",
        "AccountInvitationAccepted": "Приглашение принято",
        "AccountInvitationCreated": "Приглашение создано",
        "AccountInvitationRevoked": "Приглашение отозвано",
        "AccountRecovered": "Доступ к аккаунту восстановлен",
        "AttendanceCorrectedToAbsent": "Посещаемость исправлена на «Отсутствовал»",
        "AttendanceCorrectedToPresent": "Посещаемость исправлена на «Присутствовал»",
        "AttendanceCoverageAssigned": "Источник покрытия назначен",
        "AttendanceCoverageRebound": "Источник покрытия изменён",
        "AttendanceCoverageRecovered": "Покрытие посещения восстановлено",
        "AttendanceCoverageReversed": "Покрытие посещения отменено",
        "AttendanceMarkedAbsent": "Отмечено отсутствие",
        "AttendanceMarkedPresent": "Отмечено присутствие",
        "AttendanceUncovered": "Посещение осталось без покрытия",
        "CoachProfileChanged": "Профиль тренера изменён",
        "CoachProfileCreated": "Профиль тренера создан",
        "ExternalAccountDeactivatedForRecovery": "Внешний аккаунт деактивирован для восстановления",
        "ExternalIdentityLinked": "Внешняя учётная запись привязана",
        "ExternalIdentityUnlinked": "Внешняя учётная запись отвязана",
        "GroupMembershipChanged": "Участие в группе изменено",
        "GroupMembershipCreated": "Участие в группе создано",
        "GroupMembershipRestoredFromSeatReservation": "Участие в группе восстановлено после сохранения места",
        "GroupMembershipSuspendedForSeatReservation": "Участие в группе приостановлено для сохранения места",
        "GroupPlaceHoldActivated": "Сохранение места активировано",
        "GroupPlaceHoldCancelled": "Сохранение места отменено",
        "GroupPlaceHoldCreated": "Сохранение места создано",
        "GroupPlaceHoldExpired": "Срок сохранения места истёк",
        "GroupPlaceHoldRestored": "Возврат после сохранения места оформлен",
        "GroupSeatReservationCancelled": "Бронь места отменена",
        "GroupSeatReservationRosterSuppressed": "Бронь места исключила ученика из состава занятий",
        "GroupSeatReserved": "Место в группе забронировано",
        "LessonAttendanceReopened": "Ведомость посещаемости переоткрыта",
        "LessonAttendanceSubmitted": "Ведомость посещаемости закрыта",
        "LessonCancelled": "Занятие отменено",
        "LessonCoachReassigned": "Тренер занятия заменён",
        "LessonCompleted": "Занятие проведено",
        "LessonConfirmed": "Занятие подтверждено",
        "LessonEnrollmentAdded": "Ученик записан на занятие",
        "LessonGenerationConflict": "Обнаружен конфликт генерации занятия",
        "LessonPublished": "Занятие опубликовано",
        "LessonRescheduled": "Занятие перенесено",
        "LessonResponseChanged": "Ответ на занятие изменён",
        "LessonsGenerated": "Занятия сгенерированы",
        "MFAAuthenticated": "Дополнительная проверка пройдена",
        "MFAAuthenticatorReplacementStarted": "Начата замена приложения-аутентификатора",
        "MFAAuthenticatorReplaced": "Приложение-аутентификатор заменено",
        "MFAEnrolled": "MFA настроена",
        "MFARecoveryCodeUsed": "Резервный код использован",
        "MFARecoveryCodesRegenerated": "Резервные коды перевыпущены",
        "MakeupEntitlementCancelled": "Право на отработку отменено",
        "MakeupEntitlementExpired": "Срок права на отработку истёк",
        "MakeupEntitlementGranted": "Право на отработку выдано",
        "MakeupEntitlementUsed": "Право на отработку использовано",
        "OneTimeEntitlementCancelled": "Разовое право отменено",
        "OneTimeEntitlementGranted": "Разовое право выдано",
        "OneTimeEntitlementTransferred": "Разовое право перенесено",
        "OneTimeEntitlementUsed": "Разовое право использовано",
        "PaidFreezeActivated": "Платное действие активировано",
        "PaidFreezeAuthorizationDeadlineUnresolved": "Срок авторизации платного действия требует решения",
        "PaidFreezeAuthorizationExpired": "Срок авторизации платного действия истёк",
        "PaidFreezeAuthorized": "Платное действие авторизовано",
        "PaidFreezeCancelled": "Платное действие отменено",
        "PaidFreezeFeeConfirmed": "Оплата платного действия подтверждена",
        "PaidFreezeRefundRequired": "Требуется возврат оплаты",
        "ScheduleTemplateCreated": "Шаблон расписания создан",
        "ScheduleTemplateOccurrenceSkipped": "Регулярное занятие явно пропущено",
        "ScheduleTemplateVersioned": "Создана новая версия шаблона расписания",
        "StudentAccessChanged": "Доступ к ученику изменён",
        "StudentAccessCreated": "Доступ к ученику создан",
        "StudentChanged": "Данные ученика изменены",
        "StudentCreated": "Ученик создан",
        "SubscriptionActivated": "Абонемент активирован",
        "SubscriptionAllowanceAdjusted": "Лимит абонемента скорректирован",
        "SubscriptionAllowanceConsumed": "Посещение списано с абонемента",
        "SubscriptionAllowanceExhausted": "Лимит абонемента исчерпан",
        "SubscriptionAllowanceRestored": "Посещение возвращено в лимит абонемента",
        "SubscriptionCancelled": "Абонемент отменён",
        "SubscriptionExpired": "Срок абонемента истёк",
        "SubscriptionExpiredWithUnusedBalance": "Абонемент истёк с неиспользованным остатком",
        "SubscriptionIssued": "Абонемент выдан",
        "SubscriptionPeriodActivated": "Расчётный период активирован",
        "SubscriptionPeriodActivationRevertSkipped": "Автоматический откат расчётного периода пропущен",
        "SubscriptionPeriodActivationReverted": "Расчётный период возвращён в ожидание",
        "SubscriptionPeriodAttached": "Расчётный период привязан",
        "SubscriptionPeriodSchemeChanged": "Модель расчётного периода изменена",
        "SubscriptionPeriodSchemeCreated": "Модель расчётного периода создана",
        "SubscriptionPlanChanged": "Тариф изменён",
        "SubscriptionPlanCreated": "Тариф создан",
        "TrainingGroupChanged": "Группа изменена",
        "TrainingGroupCreated": "Группа создана",
    }
}


def manager_label(value, kind: str) -> str:
    return MANAGER_LABELS.get(kind, {}).get(str(value), str(value))


def localized_choices(kind: str, choices) -> tuple[tuple[str, str], ...]:
    return tuple(
        (value, manager_label(value, kind))
        for value, _label in choices
    )
