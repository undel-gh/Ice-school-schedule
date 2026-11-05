"""Safety and command-order tests for the production lifecycle shell entry point."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "install.sh"
VALID_ENV = """\
APP_HOST=ci.school.invalid
DJANGO_ALLOWED_HOSTS=ci.school.invalid
DJANGO_SECRET_KEY=unit-test-secret-of-sufficient-length-1234567890
DJANGO_DEBUG=0
DJANGO_SECURE_SSL_REDIRECT=1
POSTGRES_DB=ice_school
POSTGRES_USER=ice_school
POSTGRES_PASSWORD=unit-test-strong-database-password-1234
POSTGRES_HOST=db
POSTGRES_PORT=5432
SCHOOL_TIME_ZONE=Europe/Riga
BACKUP_HOST_PATH=./backups
"""

DOCKER_MOCK = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["MOCK_DOCKER_LOG"]).open("a") as handle:
    handle.write(json.dumps(args) + "\n")
if args[0] == "info":
    print("27.4.0")
elif args[0] == "inspect":
    print("running" if args[-1] == "mock-proxy" else "healthy")
elif args[0] == "compose":
    if args[1] == "version":
        print("2.40.0")
        sys.exit(0)
    command = next(
        (v for v in ("config", "build", "run", "up", "ps", "down") if v in args),
        None,
    )
    if command == "ps" and "--quiet" in args:
        print("mock-" + args[-1])
    if command == "build":
        print("compose-image-tag=" + os.environ.get("APP_IMAGE_TAG", "unset"))
    if command == "build" and os.environ.get("MOCK_BUILD_FAIL") == "1":
        sys.exit(41)
    if command == "run":
        if "backup" in args and os.environ.get("MOCK_BACKUP_FAIL") == "1":
            sys.exit(42)
        if "migrate" in args and os.environ.get("MOCK_MIGRATION_FAIL") == "1":
            sys.exit(43)
        if "backup" in args:
            print("backup event=success target=/backups/fake.dump")
"""

GIT_MOCK = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["MOCK_GIT_LOG"]).open("a") as handle:
    handle.write(json.dumps(args) + "\n")
if args[:1] == ["-C"]:
    args = args[2:]
state = Path(os.environ["MOCK_GIT_HEAD_FILE"])
if args[0] == "status":
    sys.exit(0)
if args[0] == "rev-parse":
    if "HEAD" in args:
        print(state.read_text())
    else:
        print("b" * 40)
    sys.exit(0)
if args[0] == "checkout":
    state.write_text(args[-1])
    sys.exit(0)
if args[0] == "fetch":
    sys.exit(0)
raise SystemExit(2)
"""


@pytest.fixture
def stack(tmp_path):
    shutil.copy2(SCRIPT, tmp_path / "install.sh")
    shutil.copy2(
        PROJECT_ROOT / ".env.production.example",
        tmp_path / ".env.production.example",
    )
    env_file = tmp_path / ".env.production"
    env_file.write_text(VALID_ENV)
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, source in (("docker", DOCKER_MOCK), ("git", GIT_MOCK)):
        target = fake_bin / name
        target.write_text(source)
        target.chmod(0o755)
    (tmp_path / ".git").mkdir()
    (tmp_path / "git-head").write_text("a" * 40)
    docker_log = tmp_path / "docker.log"
    git_log = tmp_path / "git.log"
    environ = dict(
        os.environ,
        PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
        MOCK_DOCKER_LOG=str(docker_log),
        MOCK_GIT_LOG=str(git_log),
        MOCK_GIT_HEAD_FILE=str(tmp_path / "git-head"),
    )

    def invoke(*args, **overrides):
        return subprocess.run(
            ["bash", str(tmp_path / "install.sh"), *args],
            cwd=tmp_path,
            env=dict(environ, **overrides),
            text=True,
            capture_output=True,
            check=False,
        )

    def calls(log):
        if not log.exists():
            return []
        return [json.loads(s) for s in log.read_text().splitlines()]

    return {
        "root": tmp_path,
        "env_file": env_file,
        "invoke": invoke,
        "docker_calls": lambda: calls(docker_log),
        "git_calls": lambda: calls(git_log),
    }


def compose_commands(calls):
    result = []
    for call in calls:
        if call[:1] != ["compose"] or "version" in call:
            continue
        # Strip Docker Compose global arguments.
        parts = call[1:]
        while parts and parts[0] in (
            "--project-directory", "--env-file", "-f"
        ):
            parts = parts[2:]
        result.append(parts)
    return result


def contains_command(commands, expected):
    return any(command[:len(expected)] == expected for command in commands)


def test_install_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_install_creates_template_but_never_starts_placeholders(stack):
    stack["env_file"].unlink()

    response = stack["invoke"]("install")

    assert response.returncode != 0
    assert stack["env_file"].read_text() == (
        PROJECT_ROOT / ".env.production.example"
    ).read_text()
    assert stack["env_file"].stat().st_mode & 0o077 == 0
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["build"]
    )


def test_install_runs_migration_before_recreating_web(stack):
    result = stack["invoke"]("install")
    commands = compose_commands(stack["docker_calls"]())

    assert result.returncode == 0, result.stderr
    assert contains_command(commands, ["config", "--quiet"])
    assert contains_command(commands, ["up", "-d", "db"])
    gate = commands.index(
        ["run", "--rm", "--no-deps", "-T", "migrate"]
    )
    deploy = commands.index(
        ["up", "-d", "--no-deps", "--force-recreate",
         "web", "scheduler", "backup"]
    )
    assert gate < deploy
    assert not any("--volumes" in x or "-v" in x for x in commands)
    assert stack["env_file"].read_text() == VALID_ENV


def test_image_tag_stays_consistent_with_production_compose(stack):
    normal = stack["invoke"]("install")
    assert normal.returncode == 0, normal.stderr
    assert "compose-image-tag=local" in normal.stdout

    stack["env_file"].write_text(VALID_ENV + "APP_IMAGE_TAG=release-1\n")
    stack["env_file"].chmod(0o600)
    custom = stack["invoke"]("reinstall")
    assert custom.returncode == 0, custom.stderr
    assert "compose-image-tag=release-1" in custom.stdout


def test_update_backup_failure_blocks_fetch_build_and_rollout(stack):
    response = stack["invoke"]("update", MOCK_BACKUP_FAIL="1")
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode != 0
    assert contains_command(
        commands,
        ["run", "--rm", "--no-deps", "-T", "backup"],
    )
    assert not contains_command(commands, ["build"])
    assert not any("fetch" in x for x in stack["git_calls"]())
    assert not contains_command(commands, ["up"])


def test_update_build_failure_restores_old_checkout_before_migration(stack):
    response = stack["invoke"](
        "update", "--ref", "v1.2.3", MOCK_BUILD_FAIL="1"
    )
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode != 0
    assert (stack["root"] / "git-head").read_text() == "a" * 40
    assert contains_command(commands, ["build", "migrate"])
    assert not any("migrate" in x for x in commands if x[:1] == ["run"])
    assert not contains_command(commands, ["up"])


def test_update_migration_failure_blocks_rollout_without_schema_rollback(stack):
    response = stack["invoke"](
        "update", "--ref", "v1.2.3", MOCK_MIGRATION_FAIL="1"
    )
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode != 0
    assert (stack["root"] / "git-head").read_text() == "b" * 40
    assert contains_command(
        commands, ["run", "--rm", "--no-deps", "-T", "migrate"]
    )
    assert not contains_command(commands, ["up"])
    assert "Do NOT blindly roll back" in response.stdout


def test_update_success_orders_backup_build_gate_rollout(stack):
    response = stack["invoke"]("update", "--ref", "v1.2.3")
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode == 0, response.stderr
    backup = next(i for i, x in enumerate(commands) if x[:1] == ["run"] and "backup" in x)
    build = commands.index(["build", "migrate"])
    migrate = commands.index(["run", "--rm", "--no-deps", "-T", "migrate"])
    web = next(i for i, x in enumerate(commands) if x[:1] == ["up"] and "web" in x)
    assert backup < build < migrate < web
    assert stack["env_file"].read_text() == VALID_ENV


def test_reinstall_preserves_data_and_requires_healthy_db(stack):
    response = stack["invoke"]("reinstall")
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode == 0, response.stderr
    assert contains_command(commands, ["build", "migrate"])
    assert contains_command(
        commands, ["run", "--rm", "--no-deps", "-T", "backup"]
    )
    assert not contains_command(commands, ["down"])
    assert stack["env_file"].exists()


def test_uninstall_does_not_remove_volumes_or_secrets(stack):
    response = stack["invoke"]("uninstall")
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode == 0
    assert contains_command(commands, ["down", "--remove-orphans"])
    assert not any("-v" in x or "--volumes" in x for x in commands)
    assert stack["env_file"].read_text() == VALID_ENV


def test_purge_refuses_noninteractive_execution(stack):
    response = stack["invoke"]("uninstall", "--purge-data")

    assert response.returncode != 0
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["down"]
    )
    assert stack["env_file"].exists()


def test_unsafe_ref_rejected_without_fetch_or_rollout(stack):
    response = stack["invoke"]("update", "--ref", "--upload-pack=evil")

    assert response.returncode != 0
    assert "Unsafe --ref" in response.stderr
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["build"]
    )


def test_invalid_secret_fails_without_leaking_it(stack):
    secret = "replace-with-a-long-random-production-secret"
    stack["env_file"].write_text(
        VALID_ENV.replace(
            "unit-test-secret-of-sufficient-length-1234567890",
            secret,
        )
    )
    stack["env_file"].chmod(0o600)

    result = stack["invoke"]("install")

    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["build"]
    )


def test_status_reports_services_without_dumping_environment(stack):
    response = stack["invoke"]("status")

    assert response.returncode == 0, response.stderr
    assert "web: healthy" in response.stdout
    assert "backup: healthy" in response.stdout
    assert "unit-test-strong-database-password-1234" not in (
        response.stdout + response.stderr
    )
