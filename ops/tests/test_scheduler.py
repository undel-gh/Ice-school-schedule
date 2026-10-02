import sys

import pytest

from ops.scheduler import build_jobs


def test_scheduler_defaults(monkeypatch):
    for name in (
        "SCHEDULER_LIFECYCLE_INTERVAL_SECONDS",
        "SCHEDULER_GENERATION_INTERVAL_SECONDS",
        "SCHEDULER_GENERATION_HORIZON_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    jobs = build_jobs()

    assert [job.name for job in jobs] == [
        "subscription_lifecycle",
        "lesson_generation",
    ]
    assert jobs[0].interval_seconds == 3600
    assert jobs[0].command == [
        sys.executable,
        "manage.py",
        "process_subscription_lifecycle",
    ]
    assert jobs[1].interval_seconds == 21600
    assert jobs[1].command == [
        sys.executable,
        "manage.py",
        "generate_lessons",
        "--all-active",
        "--horizon-days",
        "60",
    ]


def test_scheduler_environment_overrides(monkeypatch):
    monkeypatch.setenv("SCHEDULER_LIFECYCLE_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("SCHEDULER_GENERATION_INTERVAL_SECONDS", "7200")
    monkeypatch.setenv("SCHEDULER_GENERATION_HORIZON_DAYS", "90")

    jobs = build_jobs()

    assert jobs[0].interval_seconds == 900
    assert jobs[1].interval_seconds == 7200
    assert jobs[1].command[-1] == "90"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SCHEDULER_LIFECYCLE_INTERVAL_SECONDS", "0"),
        ("SCHEDULER_LIFECYCLE_INTERVAL_SECONDS", "-1"),
        ("SCHEDULER_GENERATION_INTERVAL_SECONDS", "abc"),
        ("SCHEDULER_GENERATION_HORIZON_DAYS", ""),
    ],
)
def test_scheduler_rejects_invalid_positive_integer(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit):
        build_jobs()
