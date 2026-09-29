from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)






@admin.register(AbsenceCompensationActionGrant)
class AbsenceCompensationActionGrantAdmin(ReadOnlyAdmin):
    list_display = (
        "case",
        "action_type",
        "fee_confirmed_at",
        "target_subscription",
        "activated_at",
        "makeup_entitlement",
        "reversed_at",
        "refund_required",
        "created_at",
        "created_by",
    )
    list_filter = ("action_type",)


@admin.register(AbsenceCompensationCase)
class AbsenceCompensationCaseAdmin(ReadOnlyAdmin):
    list_display = (
        "student",
        "source_lesson",
        "absence_reason",
        "policy_code_snapshot",
        "policy_version_snapshot",
        "eligibility_status",
        "eligible_absence_ordinal",
        "status",
        "materialized_at",
        "created_at",
    )
    list_filter = (
        "absence_reason",
        "eligibility_status",
        "status",
        "category",
    )
    search_fields = (
        "student__display_name",
        "policy_code_snapshot",
        "policy_name_snapshot",
    )

class AbsenceCompensationPolicyActionInline(admin.TabularInline):
    model = AbsenceCompensationPolicyAction
    extra = 0

    def has_add_permission(self, request, obj=None):
        if obj is not None and obj.compensation_cases.exists():
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.compensation_cases.exists():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.compensation_cases.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AbsenceCompensationPolicy)
class AbsenceCompensationPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "version",
        "name",
        "absence_reason",
        "effective_from",
        "effective_until",
        "is_active",
    )
    list_filter = ("absence_reason", "is_active")
    search_fields = ("code", "name")
    inlines = (AbsenceCompensationPolicyActionInline,)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.compensation_cases.exists():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.compensation_cases.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AbsenceCompensationPolicyAction)
class AbsenceCompensationPolicyActionAdmin(admin.ModelAdmin):
    list_display = (
        "policy",
        "action_type",
        "target_period_rule",
        "requirement",
        "priority",
        "is_active",
    )
    list_filter = ("action_type", "target_period_rule", "requirement", "is_active")

    def has_change_permission(self, request, obj=None):
        if (
            obj is not None
            and obj.policy.compensation_cases.exists()
        ):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if (
            obj is not None
            and obj.policy.compensation_cases.exists()
        ):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AbsenceCompensationPolicyWindow)
class AbsenceCompensationPolicyWindowAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "policy_action",
        "source_from",
        "source_until",
        "target_from",
        "target_until",
        "priority",
        "is_active",
    )
    list_filter = ("is_active",)

    def has_change_permission(self, request, obj=None):
        if (
            obj is not None
            and obj.policy_action.policy.compensation_cases.exists()
        ):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if (
            obj is not None
            and obj.policy_action.policy.compensation_cases.exists()
        ):
            return False
        return super().has_delete_permission(request, obj)

admin.site.register(SubscriptionPlan)
admin.site.register(SubscriptionPlanAllowance)


@admin.register(Subscription)
class SubscriptionAdmin(ReadOnlyAdmin):
    list_display = (
        "student",
        "plan_name_snapshot",
        "valid_from",
        "valid_until",
        "cancelled_at",
    )


@admin.register(SubscriptionAllowance)
class SubscriptionAllowanceAdmin(ReadOnlyAdmin):
    list_display = ("subscription", "category", "visit_limit_snapshot")


@admin.register(OneTimeEntitlement)
class OneTimeEntitlementAdmin(ReadOnlyAdmin):
    list_display = (
        "student",
        "lesson",
        "entitlement_type",
        "category",
        "cancelled_at",
    )


@admin.register(MakeupEntitlement)
class MakeupEntitlementAdmin(ReadOnlyAdmin):
    list_display = (
        "student",
        "source_lesson",
        "category",
        "reason",
        "valid_from",
        "valid_until",
        "cancelled_at",
    )


@admin.register(AttendanceCoverage)
class AttendanceCoverageAdmin(ReadOnlyAdmin):
    list_display = (
        "attendance",
        "subscription_allowance",
        "one_time_entitlement",
        "makeup_entitlement",
        "reversed_at",
    )


@admin.register(SubscriptionLedgerEntry)
class SubscriptionLedgerEntryAdmin(ReadOnlyAdmin):
    list_display = (
        "allowance",
        "entry_type",
        "delta",
        "coverage",
        "created_at",
    )
    list_filter = ("entry_type",)
