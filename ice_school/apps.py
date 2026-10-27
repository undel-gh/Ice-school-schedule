from django.apps import AppConfig
from django.contrib import admin


class IceSchoolConfig(AppConfig):
    name = "ice_school"
    verbose_name = "Ice School"

    def ready(self):
        admin.site.site_header = "Администрирование Ice School"
        admin.site.site_title = "Ice School"
        admin.site.index_title = "Управление данными"

        from . import checks  # noqa: F401
