from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        ("coach", "Причина: тренер уже занят другим занятием в это время."),
        ("venue", "Причина: площадка уже занята другим занятием в это время."),
        (
            "coach_and_venue",
            "Причина: и тренер, и площадка уже заняты другим занятием в это время.",
        ),
    ],
)
def test_generation_conflict_template_explains_resource_reason(resource, expected):
    row = {
        "event": SimpleNamespace(payload={"conflict_resource": resource}),
        "template": None,
        "conflicting_lesson": None,
        "expected_starts_at": None,
        "resolved": False,
        "occurrence_status": None,
    }

    html = render_to_string(
        "scheduling/manager_generation_conflicts.html",
        {"rows": [row]},
    )

    assert expected in html
