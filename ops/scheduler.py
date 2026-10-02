from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
import signal
import subprocess
import sys
import time


@dataclass
class Job:
    name: str
    interval_seconds: int
    command: list[str]
    next_run: float = 0.0


stop_requested = False


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be positive")
    return value


def _log(message: str) -> None:
    timestamp = datetime.now(UTC).isoformat()
    print(f"{timestamp} scheduler {message}", flush=True)


def _signal_handler(signum, frame) -> None:
    del frame
    global stop_requested
    stop_requested = True
    _log(f"signal={signum} stop_requested=true")


def _run(job: Job) -> None:
    started = time.monotonic()
    _log(f"job={job.name} event=start command={job.command!r}")
    try:
        completed = subprocess.run(job.command, check=False)
        code = completed.returncode
    except OSError as exc:
        code = 127
        _log(f"job={job.name} event=spawn_error error={exc!r}")
    duration = time.monotonic() - started
    _log(
        f"job={job.name} event=finish exit_code={code} "
        f"duration_seconds={duration:.3f}"
    )


def build_jobs() -> list[Job]:
    horizon_days = _positive_int("SCHEDULER_GENERATION_HORIZON_DAYS", 60)
    return [
        Job(
            name="subscription_lifecycle",
            interval_seconds=_positive_int(
                "SCHEDULER_LIFECYCLE_INTERVAL_SECONDS",
                3600,
            ),
            command=[
                sys.executable,
                "manage.py",
                "process_subscription_lifecycle",
            ],
        ),
        Job(
            name="lesson_generation",
            interval_seconds=_positive_int(
                "SCHEDULER_GENERATION_INTERVAL_SECONDS",
                21600,
            ),
            command=[
                sys.executable,
                "manage.py",
                "generate_lessons",
                "--all-active",
                "--horizon-days",
                str(horizon_days),
            ],
        ),
    ]


def main() -> int:
    jobs = build_jobs()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    now = time.monotonic()
    for job in jobs:
        job.next_run = now

    _log("event=started")
    while not stop_requested:
        now = time.monotonic()
        for job in jobs:
            if stop_requested:
                break
            if now >= job.next_run:
                _run(job)
                job.next_run = time.monotonic() + job.interval_seconds

        if stop_requested:
            break
        next_due = min(job.next_run for job in jobs)
        sleep_for = max(0.25, min(30.0, next_due - time.monotonic()))
        time.sleep(sleep_for)

    _log("event=stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
