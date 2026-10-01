from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("scheduling.urls")),
    path("accounts/external/", include("accounts.external_urls")),
    path("manager/school/", include("accounts.manager_urls")),
    path("manager/school/", include("scheduling.school_admin_urls")),
    path("manager/scheduling/", include("scheduling.manager_urls")),
    path("manager/attendance/", include("attendance.manager_urls")),
    path("manager/audit/", include("audit.urls")),
    path("manager/", include("subscriptions.urls")),
    path("account/", include("subscriptions.student_urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]
