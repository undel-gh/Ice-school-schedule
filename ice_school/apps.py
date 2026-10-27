from django.apps import AppConfig


class IceSchoolConfig(AppConfig):
    name = "ice_school"
    verbose_name = "Ice School"

    def ready(self):
        from django.contrib import admin

        admin.site.site_header = "Администрирование Ice School"
        admin.site.site_title = "Ice School"
        admin.site.index_title = "Управление данными"

        from . import checks  # noqa: F401
