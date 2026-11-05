"""Offsite restic uploader safety, retention and health behavior, without network access."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "offsite_backup.sh"
HEALTH = ROOT / "ops" / "offsite_healthcheck.sh"
MOCK = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
with Path(os.environ["MOCK_RESTIC_CALLS"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("MOCK_RESTIC_FAIL") in sys.argv:
    sys.exit(13)
remote = Path(os.environ["MOCK_REMOTE_STATE"])
if "backup" in sys.argv:
    remote.write_text(sys.argv[-1])
elif "snapshots" in sys.argv:
    if remote.exists():
        print(json.dumps([{"paths": [remote.read_text()]}]))
    else:
        print("[]")
"""


@pytest.fixture
def uploader(tmp_path):
    backup = tmp_path / "backups"
    backup.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    fake = tmp_path / "bin"
    fake.mkdir()
    restic = fake / "restic"
    restic.write_text(MOCK)
    restic.chmod(0o755)
    passwd = tmp_path / "password"
    passwd.write_text("test-only-strong-passphrase")
    creds = tmp_path / "credentials"
    creds.write_text("[default]\naws_access_key_id=test\naws_secret_access_key=test\n")
    calls = tmp_path / "calls.jsonl"
    remote = tmp_path / "remote-path"
    env = dict(
        os.environ,
        PATH=str(fake) + os.pathsep + os.environ["PATH"],
        RESTIC_REPOSITORY=str(tmp_path / "repo"),
        RESTIC_PASSWORD_FILE=str(passwd),
        AWS_SHARED_CREDENTIALS_FILE=str(creds),
        OFFSITE_BACKUP_DIR=str(backup),
        OFFSITE_STATUS_DIR=str(state),
        MOCK_RESTIC_CALLS=str(calls),
        MOCK_REMOTE_STATE=str(remote),
    )

    def invoke(**extra):
        return subprocess.run(
            ["sh", str(SCRIPT), "--once"],
            env=dict(env, **extra), text=True, capture_output=True,
            check=False,
        )

    def health(**extra):
        return subprocess.run(
            ["sh", str(HEALTH)],
            env=dict(env, **extra), text=True, capture_output=True,
            check=False,
        )

    def restic_calls():
        if not calls.exists():
            return []
        return [json.loads(line) for line in calls.read_text().splitlines()]

    return dict(
        backup=backup, state=state, invoke=invoke,
        health=health, calls=restic_calls, remote=remote, env=env,
    )


def add_dump(uploader, name="ice_school_20261105T180000Z.dump"):
    path = uploader["backup"] / name
    path.write_bytes(b"pg_dump verified fixture\n")
    return path


def test_shell_syntax():
    for path in (SCRIPT, HEALTH):
        result = subprocess.run(["sh", "-n", str(path)], capture_output=True)
        assert result.returncode == 0


def test_plaintext_monitoring_url_is_refused(uploader):
    add_dump(uploader)
    response = uploader["invoke"](
        OFFSITE_SUCCESS_PING_URL="http://example.net/ping/private-token"
    )
    assert response.returncode == 2
    assert "OFFSITE_SUCCESS_PING_URL" in response.stderr
    assert uploader["calls"]() == []


def test_missing_dump_fails_without_success(uploader):
    (uploader["backup"] / "ice_school_test.dump.partial").write_bytes(b"partial")
    result = uploader["invoke"]()
    assert result.returncode != 0
    assert "stage=no_verified_dump" in result.stderr
    assert uploader["calls"]() == []
    assert uploader["health"]().returncode != 0


def test_upload_retention_and_idempotency(uploader):
    latest = add_dump(uploader)
    old = add_dump(uploader, "ice_school_20261104T180000Z.dump")
    (uploader["backup"] / "ice_school_20261106T180000Z.dump.partial").write_text("ignore")
    result = uploader["invoke"]()
    assert result.returncode == 0, result.stderr
    calls = uploader["calls"]()
    assert len(calls) == 2
    assert "backup" in calls[0] and str(latest) in calls[0]
    assert str(old) not in calls[0]
    assert calls[0][calls[0].index("--group-by") + 1] == "host,tags"
    assert "forget" in calls[1] and "--prune" in calls[1]
    assert calls[1][calls[1].index("--group-by") + 1] == "host,tags"
    assert (uploader["state"] / "last_uploaded").read_text().strip().endswith(
        " " + latest.name
    )
    assert uploader["health"]().returncode == 0
    repeated = uploader["invoke"]()
    assert repeated.returncode == 0
    assert len(uploader["calls"]()) == 3
    assert "snapshots" in uploader["calls"]()[-1]


@pytest.mark.parametrize("failure", ["backup", "forget"])
def test_remote_failure_not_acknowledged(uploader, failure):
    add_dump(uploader)
    result = uploader["invoke"](MOCK_RESTIC_FAIL=failure)
    assert result.returncode != 0
    assert not (uploader["state"] / "last_uploaded").exists()
    assert uploader["health"]().returncode != 0
    assert (uploader["state"] / "last_failure").exists()


def test_after_failure_retries_same_dump(uploader):
    add_dump(uploader)
    assert uploader["invoke"](MOCK_RESTIC_FAIL="backup").returncode != 0
    assert uploader["invoke"]().returncode == 0
    assert (uploader["state"] / "last_success").is_file()
    assert not (uploader["state"] / "last_failure").exists()


def test_failed_new_dump_immediately_marks_service_unhealthy(uploader):
    add_dump(uploader)
    assert uploader["invoke"]().returncode == 0
    assert uploader["health"]().returncode == 0

    add_dump(uploader, "ice_school_20261107T180000Z.dump")
    failed = uploader["invoke"](MOCK_RESTIC_FAIL="backup")
    assert failed.returncode != 0
    assert uploader["health"]().returncode != 0
    assert (uploader["state"] / "last_failure").exists()
    assert (uploader["state"] / "last_success").exists()

    recovered = uploader["invoke"]()
    assert recovered.returncode == 0, recovered.stderr
    assert uploader["health"]().returncode == 0
    assert not (uploader["state"] / "last_failure").exists()


def test_new_dump_causes_new_upload(uploader):
    add_dump(uploader)
    assert uploader["invoke"]().returncode == 0
    add_dump(uploader, "ice_school_20261107T180000Z.dump")
    assert uploader["invoke"]().returncode == 0
    assert sum("backup" in c for c in uploader["calls"]()) == 2


def test_stale_health_marker_is_unhealthy(uploader):
    add_dump(uploader)
    assert uploader["invoke"]().returncode == 0
    success = uploader["state"] / "last_success"
    old = time.time() - 300
    os.utime(success, (old, old))
    assert uploader["health"](
        BACKUP_INTERVAL_SECONDS="60",
        OFFSITE_HEALTH_GRACE_SECONDS="0",
    ).returncode != 0


def test_changed_repository_reuploads_dump(uploader):
    add_dump(uploader)
    assert uploader["invoke"]().returncode == 0
    updated = uploader["invoke"](RESTIC_REPOSITORY="/different-repository")
    assert updated.returncode == 0, updated.stderr
    assert sum("backup" in c for c in uploader["calls"]()) == 2


def test_deleted_remote_snapshot_reuploaded(uploader):
    add_dump(uploader)
    assert uploader["invoke"]().returncode == 0
    uploader["remote"].unlink()
    result = uploader["invoke"]()
    assert result.returncode == 0, result.stderr
    assert "remote_snapshot_missing_or_unreachable" in result.stdout
    assert sum("backup" in c for c in uploader["calls"]()) == 2
