from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)


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
