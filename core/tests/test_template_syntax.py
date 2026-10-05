from pathlib import Path

from django.conf import settings
from django.template.loader import get_template


def test_all_project_templates_compile():
    templates_dir = Path(settings.BASE_DIR) / "templates"

    template_names = sorted(
        path.relative_to(templates_dir).as_posix()
        for path in templates_dir.rglob("*.html")
    )

    assert template_names

    for template_name in template_names:
        get_template(template_name)
