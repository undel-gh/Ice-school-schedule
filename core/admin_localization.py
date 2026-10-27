from __future__ import annotations

from django.apps import apps


MODEL_LABELS = {
    "accounts.User": ("пользователь", "пользователи"),
    "accounts.ExternalIdentity": ("внешняя учётная запись", "внешние учётные записи"),
    "accounts.AccountInvitation": ("приглашение", "приглашения"),
    "accounts.Student": ("ученик", "ученики"),
    "accounts.StudentAccess": ("доступ к ученику", "доступы к ученикам"),
    "accounts.CoachProfile": ("тренер", "тренеры"),
    "scheduling.TrainingGroup": ("учебная группа", "учебные группы"),
    "scheduling.GroupMembership": ("участие в группе", "участия в группах"),
    "scheduling.GroupSeatReservation": ("резерв места в группе", "резервы мест в группах"),
    "scheduling.Venue": ("площадка", "площадки"),
    "scheduling.LessonType": ("тип занятия", "типы занятий"),
    "scheduling.ScheduleTemplate": ("шаблон расписания", "шаблоны расписания"),
    "scheduling.Lesson": ("занятие", "занятия"),
    "scheduling.LessonEnrollment": ("дополнительная запись на занятие", "дополнительные записи на занятия"),
    "scheduling.LessonRosterEntry": ("участник занятия", "участники занятий"),
    "scheduling.LessonResponse": ("ответ на занятие", "ответы на занятия"),
    "attendance.Attendance": ("посещаемость", "отметки посещаемости"),
    "attendance.AbsenceJustification": ("основание отсутствия", "основания отсутствия"),
    "subscriptions.SubscriptionPeriodScheme": ("модель расчётного периода", "модели расчётных периодов"),
    "subscriptions.SubscriptionPlan": ("тариф", "тарифы"),
    "subscriptions.SubscriptionPlanAllowance": ("лимит тарифа", "лимиты тарифов"),
    "subscriptions.AbsenceCompensationPolicy": ("политика компенсаций", "политики компенсаций"),
    "subscriptions.AbsenceCompensationPolicyAction": ("действие политики компенсаций", "действия политик компенсаций"),
    "subscriptions.AbsenceCompensationPolicyWindow": ("окно политики компенсаций", "окна политик компенсаций"),
    "subscriptions.AbsenceCompensationCase": ("случай компенсации", "случаи компенсаций"),
    "subscriptions.AbsenceCompensationActionGrant": ("выданное действие компенсации", "выданные действия компенсаций"),
    "subscriptions.Subscription": ("абонемент", "абонементы"),
    "subscriptions.SubscriptionPeriod": ("период абонемента", "периоды абонемента"),
    "subscriptions.GroupPlaceHold": ("сохранение места", "сохранения мест"),
    "subscriptions.SubscriptionAllowance": ("лимит абонемента", "лимиты абонементов"),
    "subscriptions.OneTimeEntitlement": ("разовое право", "разовые права"),
    "subscriptions.MakeupEntitlement": ("право на отработку", "права на отработку"),
    "subscriptions.AttendanceCoverage": ("покрытие посещения", "покрытия посещений"),
    "subscriptions.SubscriptionLedgerEntry": ("запись журнала лимита", "записи журнала лимитов"),
    "audit.AuditEvent": ("событие журнала", "события журнала"),
}


FIELD_LABELS = {
    "id": "ID",
    "code": "Код",
    "name": "Название",
    "display_name": "Отображаемое имя",
    "email": "Электронная почта",
    "user": "Пользователь",
    "student": "Ученик",
    "coach": "Тренер",
    "lesson": "Занятие",
    "lesson_type": "Тип занятия",
    "group": "Группа",
    "venue": "Площадка",
    "role": "Роль",
    "status": "Статус",
    "type": "Тип",
    "category": "Категория",
    "reason": "Причина",
    "source": "Источник",
    "created_at": "Создано",
    "created_by": "Создал",
    "updated_at": "Изменено",
    "updated_by": "Изменил",
    "is_active": "Активен",
    "starts_on": "Начало периода",
    "ends_on": "Окончание периода",
    "cancelled_at": "Отменено",
    "cancelled_by": "Отменил",
    "valid_from": "Действует с",
    "valid_until": "Действует по",
    "priority": "Приоритет",
    "policy": "Политика",
    "attendance": "Посещаемость",
    "subscription": "Абонемент",
    "allowance": "Лимит",
    "provider": "Провайдер",
    "provider_subject": "Идентификатор у провайдера",
    "last_used_at": "Последнее использование",
    "kind": "Тип приглашения",
    "token_hash": "Хэш токена",
    "student_access_role": "Роль доступа к ученику",
    "coach_display_name": "Имя тренера",
    "recovery_user": "Восстанавливаемый пользователь",
    "account_display_name": "Подпись аккаунта",
    "expires_at": "Истекает",
    "accepted_at": "Принято",
    "accepted_by": "Принял",
    "revoked_at": "Отозвано",
    "revoked_by": "Отозвал",
    "default_minimum_attendees": "Минимум участников",
    "capacity": "Вместимость",
    "address": "Адрес",
    "floor": "Этаж",
    "subscription_category": "Категория абонемента",
    "weekday": "День недели",
    "start_time": "Время начала",
    "duration_minutes": "Продолжительность, мин.",
    "minimum_attendees_override": "Минимум участников (переопределение)",
    "source_template": "Исходный шаблон",
    "starts_at": "Начало",
    "ends_at": "Окончание",
    "minimum_attendees": "Минимум участников",
    "rsvp_deadline": "Срок ответа",
    "decision_deadline": "Срок решения",
    "published_at": "Опубликовано",
    "confirmed_at": "Подтверждено",
    "confirmed_by": "Подтвердил",
    "decision_evaluated_at": "Решение рассчитано",
    "decision_yes_count": "Ответов «Буду»",
    "decision_no_count": "Ответов «Не буду»",
    "decision_no_response_count": "Без ответа",
    "completed_at": "Завершено",
    "attendance_submitted_at": "Посещаемость отправлена",
    "attendance_submitted_by": "Посещаемость отправил",
    "cancellation_reason": "Причина отмены",
    "replacement_lesson": "Занятие-замена",
    "group_membership": "Участие в группе",
    "lesson_enrollment": "Дополнительная запись",
    "added_at": "Добавлено",
    "added_by": "Добавил",
    "deactivated_at": "Деактивировано",
    "deactivated_by": "Деактивировал",
    "marked_at": "Отмечено",
    "marked_by": "Отметил",
    "verification_method": "Способ проверки",
    "declared_at": "Заявлено",
    "declared_by": "Заявил",
    "reviewed_at": "Проверено",
    "reviewed_by": "Проверил",
    "revocation_reason": "Причина отзыва",
    "event_type": "Тип события",
    "actor": "Инициатор",
    "aggregate_type": "Тип объекта",
    "aggregate_id": "ID объекта",
    "correlation_id": "ID корреляции",
    "payload": "Данные события",
    "occurred_at": "Время события",
    "mode": "Режим периода",
    "fixed_anchor_date": "Опорная дата",
    "validity_months": "Срок действия, мес.",
    "period_scheme": "Модель расчётного периода",
    "plan": "Тариф",
    "visit_limit": "Лимит посещений",
    "version": "Версия",
    "absence_reason": "Причина отсутствия",
    "justification_requirement": "Требование к основанию",
    "max_eligible_absences": "Максимум компенсируемых отсутствий",
    "limit_scope": "Область лимита",
    "effective_from": "Действует с",
    "effective_until": "Действует по",
    "action_type": "Тип действия",
    "target_period_rule": "Целевой период",
    "requirement": "Дополнительное условие",
    "validity_days": "Срок действия, дней",
    "policy_action": "Действие политики",
    "source_from": "Исходный период с",
    "source_until": "Исходный период по",
    "target_from": "Целевой период с",
    "target_until": "Целевой период по",
    "requirement_override": "Переопределение условия",
    "source_lesson": "Исходное занятие",
    "source_subscription_allowance": "Исходный лимит абонемента",
    "source_justification": "Исходное основание",
    "source_date": "Дата отсутствия",
    "policy_code_snapshot": "Код политики (снимок)",
    "policy_version_snapshot": "Версия политики (снимок)",
    "policy_name_snapshot": "Название политики (снимок)",
    "justification_requirement_snapshot": "Требование к основанию (снимок)",
    "max_eligible_absences_snapshot": "Максимум отсутствий (снимок)",
    "limit_scope_snapshot": "Область лимита (снимок)",
    "actions_snapshot": "Действия (снимок)",
    "eligibility_status": "Статус права",
    "eligible_absence_ordinal": "Номер учитываемого отсутствия",
    "eligibility_period_from": "Период проверки с",
    "eligibility_period_until": "Период проверки по",
    "eligibility_evaluated_at": "Право проверено",
    "materialized_at": "Право выдано",
    "materialized_by": "Право выдал",
    "reversed_at": "Отменено",
    "reversed_by": "Отменил",
    "reversal_reason": "Причина отмены",
    "plan_code_snapshot": "Код тарифа (снимок)",
    "plan_name_snapshot": "Название тарифа (снимок)",
    "scheme": "Модель периода",
    "mode_snapshot": "Режим периода (снимок)",
    "fixed_anchor_snapshot": "Опорная дата (снимок)",
    "reference_date": "Опорная дата расчёта",
    "state": "Состояние",
    "activation_lesson": "Занятие активации",
    "activated_at": "Активировано",
    "seat_reservation": "Резерв места",
    "suspended_membership": "Приостановленное участие",
    "suspended_membership_ends_on_snapshot": "Окончание участия (снимок)",
    "restored_membership": "Восстановленное участие",
    "period_from": "Период с",
    "period_until": "Период по",
    "fee_confirmed_at": "Оплата подтверждена",
    "fee_confirmed_by": "Оплату подтвердил",
    "restored_at": "Восстановлено",
    "restored_by": "Восстановил",
    "visit_limit_snapshot": "Лимит посещений (снимок)",
    "entitlement_type": "Тип права",
    "target_lesson": "Целевое занятие",
    "case": "Случай компенсации",
    "action_snapshot": "Действие (снимок)",
    "makeup_entitlement": "Право на отработку",
    "target_subscription": "Целевой абонемент",
    "refund_required": "Требуется возврат",
    "subscription_allowance": "Лимит абонемента",
    "one_time_entitlement": "Разовое право",
    "coverage": "Покрытие",
    "entry_type": "Тип записи",
    "delta": "Изменение остатка",
}


CHOICE_LABEL_OVERRIDES = {
    ("subscriptions.MakeupEntitlement", "reason"): {
        "administrative": "Административная причина",
    },
}


_applied = False


def apply_admin_localization() -> None:
    """Apply presentation-only labels immediately before Django Admin handles a request.

    The domain model and migration state intentionally stay unchanged. This avoids a
    large set of metadata-only AlterField migrations whose only purpose would be an
    administrative UI label.
    """
    global _applied
    if _applied:
        return

    for model_label, (singular, plural) in MODEL_LABELS.items():
        model = apps.get_model(model_label)
        model._meta.verbose_name = singular
        model._meta.verbose_name_plural = plural

        for field in model._meta.get_fields():
            if getattr(field, "auto_created", False) and not getattr(field, "concrete", False):
                continue
            label = FIELD_LABELS.get(field.name)
            if label is not None and hasattr(field, "verbose_name"):
                field.verbose_name = label

            choice_overrides = CHOICE_LABEL_OVERRIDES.get((model_label, field.name))
            if choice_overrides and getattr(field, "choices", None):
                field.choices = [
                    (value, choice_overrides.get(value, choice_label))
                    for value, choice_label in field.choices
                ]

    _applied = True


class AdminLocalizationMiddleware:
    """Localize model metadata only for requests resolved to Django Admin."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match is not None and resolver_match.app_name == "admin":
            apply_admin_localization()
        return None
