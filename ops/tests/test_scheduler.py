import subprocess
import sys
from types import SimpleNamespace

import pytest

from ops import scheduler
from ops.scheduler import Job, build_jobs


def _clear_scheduler_env(monkeypatch):
    for name in (
        "SCHEDULER_LIFECYCLE_INTERVAL_SECONDS",
        "SCHEDULER_GENERATION_INTERVAL_SECONDS",
        "SCHEDULER_LIFECYCLE_TIMEOUT_SECONDS",
        "SCHEDULER_GENERATION_TIMEOUT_SECONDS",
        "SCHEDULER_GENERATION_HORIZON_DAYS",
        "SCHEDULER_LIFECYCLE_SUCCESS_PING_URL",
        "SCHEDULER_GENERATION_SUCCESS_PING_URL",
        "MONITORING_HTTP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_scheduler_defaults(monkeypatch):
    _clear_scheduler_env(monkeypatch)

    jobs = build_jobs()

    assert [job.name for job in jobs] == [
        "subscription_lifecycle",
        "lesson_generation",
    ]
    assert jobs[0].interval_seconds == 3600
    assert jobs[0].timeout_seconds == 3600
    assert jobs[0].command == [
        sys.executable,
        "manage.py",
        "process_subscription_lifecycle",
    ]
    assert jobs[1].interval_seconds == 21600
    assert jobs[1].timeout_seconds == 21600
    assert jobs[1].command == [
        sys.executable,
        "manage.py",
        "generate_lessons",
        "--all-active",
        "--horizon-days",
        "60",
    ]


def test_scheduler_environment_overrides(monkeypatch):
    _clear_scheduler_env(monkeypatch)
    monkeypatch.setenv("SCHEDULER_LIFECYCLE_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("SCHEDULER_GENERATION_INTERVAL_SECONDS", "7200")
    monkeypatch.setenv("SCHEDULER_LIFECYCLE_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("SCHEDULER_GENERATION_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("SCHEDULER_GENERATION_HORIZON_DAYS", "90")
    monkeypatch.setenv(
        "SCHEDULER_LIFECYCLE_SUCCESS_PING_URL",
        "https://monitor.invalid/lifecycle",
    )
    monkeypatch.setenv("MONITORING_HTTP_TIMEOUT_SECONDS", "7")

    jobs = build_jobs()

    assert jobs[0].interval_seconds == 900
    assert jobs[0].timeout_seconds == 300
    assert jobs[0].success_ping_url == "https://monitor.invalid/lifecycle"
    assert jobs[0].ping_timeout_seconds == 7
    assert jobs[1].interval_seconds == 7200
    assert jobs[1].timeout_seconds == 1800
    assert jobs[1].command[-1] == "90"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SCHEDULER_LIFECYCLE_INTERVAL_SECONDS", "0"),
        ("SCHEDULER_LIFECYCLE_TIMEOUT_SECONDS", "-1"),
        ("SCHEDULER_GENERATION_INTERVAL_SECONDS", "abc"),
        ("SCHEDULER_GENERATION_TIMEOUT_SECONDS", ""),
        ("SCHEDULER_GENERATION_HORIZON_DAYS", ""),
        ("MONITORING_HTTP_TIMEOUT_SECONDS", "0"),
    ],
)
def test_scheduler_rejects_invalid_positive_integer(monkeypatch, name, value):
    _clear_scheduler_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit):
        build_jobs()


def test_successful_job_updates_health_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHEDULER_HEALTH_DIR", str(tmp_path))
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, check, timeout: SimpleNamespace(returncode=0),
    )
    job = Job(
        name="test_job",
        interval_seconds=60,
        timeout_seconds=30,
        command=["true"],
    )

    scheduler._run(job)

    assert (tmp_path / "test_job.success").exists()


def test_failed_job_does_not_update_health_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHEDULER_HEALTH_DIR", str(tmp_path))
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, check, timeout: SimpleNamespace(returncode=1),
    )
    job = Job(
        name="test_job",
        interval_seconds=60,
        timeout_seconds=30,
        command=["false"],
    )

    scheduler._run(job)

    assert not (tmp_path / "test_job.success").exists()


def test_timed_out_job_is_logged_and_does_not_update_marker(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SCHEDULER_HEALTH_DIR", str(tmp_path))

    def timeout_run(command, check, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(scheduler.subprocess, "run", timeout_run)
    job = Job(
        name="test_job",
        interval_seconds=60,
        timeout_seconds=3,
        command=["hang"],
    )

    scheduler._run(job)

    output = capsys.readouterr().out
    assert "job=test_job event=timeout timeout_seconds=3" in output
    assert not (tmp_path / "test_job.success").exists()


def test_success_ping_failure_does_not_fail_job_or_log_secret(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SCHEDULER_HEALTH_DIR", str(tmp_path))
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, check, timeout: SimpleNamespace(returncode=0),
    )

    def failed_ping(url, timeout):
        raise OSError(f"failed URL {url}")

    monkeypatch.setattr(scheduler, "urlopen", failed_ping)
    job = Job(
        name="test_job",
        interval_seconds=60,
        timeout_seconds=30,
        command=["true"],
        success_ping_url="https://monitor.invalid/super-secret-token",
    )

    scheduler._run(job)

    output = capsys.readouterr().out
    assert "event=success_ping_failed error_type=OSError" in output
    assert "super-secret-token" not in output
    assert (tmp_path / "test_job.success").exists()
