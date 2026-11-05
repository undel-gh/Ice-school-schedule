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
    if args[0] == "compose" and args[1] != "version":
        handle.write(
            json.dumps(["compose-tag", os.environ.get("APP_IMAGE_TAG", "unset"), args])
            + "\n"
        )
if args[:2] == ["image", "tag"]:
    if os.environ.get("MOCK_TAG_FAIL") == "1":
        sys.exit(44)
    Path(os.environ["MOCK_RELEASE_IMAGE_STATE"]).write_text(args[2])
elif args[:2] == ["image", "rm"]:
    if os.environ.get("MOCK_IMAGE_RM_FAIL") == "1":
        sys.exit(45)
elif args[0] == "info":
    print("27.4.0")
elif args[0] == "inspect":
    print("running" if args[-1] == "mock-proxy" else "healthy")
elif args[0] == "compose":
    if args[1] == "version":
        print("2.40.0")
        sys.exit(0)
    command = next(
        (v for v in ("config", "build", "run", "up", "ps", "down", "exec") if v in args),
        None,
    )
    if command == "ps" and "--quiet" in args:
        print("mock-" + args[-1])
        if os.environ.get("MOCK_MULTI_IDS") == "1" and args[-1] == "web":
            print("mock-duplicate-web")
    if command == "exec" and "psql" in args:
        print("0" if os.environ.get("MOCK_EMPTY_DB") == "1" else "1")
    if command == "build":
        print("compose-image-tag=" + os.environ.get("APP_IMAGE_TAG", "unset"))
    if command == "build" and os.environ.get("MOCK_BUILD_FAIL") == "1":
        sys.exit(41)
    if command == "run":
        if "offsite-backup" in args and os.environ.get("MOCK_OFFSITE_FAIL") == "1":
            sys.exit(46)
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
    release_state = tmp_path / "release-image-state"
    release_state.write_text("old-release-image")
    environ = dict(
        os.environ,
        PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
        MOCK_DOCKER_LOG=str(docker_log),
        MOCK_GIT_LOG=str(git_log),
        MOCK_GIT_HEAD_FILE=str(tmp_path / "git-head"),
        MOCK_RELEASE_IMAGE_STATE=str(release_state),
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
        "release_state": release_state,
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


def test_first_install_skips_empty_dump_but_installs_schema(stack):
    result = stack["invoke"]("install", MOCK_EMPTY_DB="1")
    commands = compose_commands(stack["docker_calls"]())

    assert result.returncode == 0, result.stderr
    assert "New empty database" in result.stdout
    assert not any(x[:1] == ["run"] and "backup" in x for x in commands)
    assert contains_command(
        commands, ["run", "--rm", "--no-deps", "-T", "migrate"]
    )


def test_image_tag_stays_consistent_with_production_compose(stack):
    normal = stack["invoke"]("install")
    assert normal.returncode == 0, normal.stderr
    calls = stack["docker_calls"]()
    first_build = next(x for x in calls if x[0] == "compose-tag" and "build" in x[2])
    assert first_build[1].startswith("candidate-")
    assert stack["release_state"].read_text() == "ice-school-app:" + first_build[1]
    assert any(
        x[0] == "compose-tag" and x[1] == "local" and "web" in x[2]
        for x in calls
    )

    stack["env_file"].write_text(VALID_ENV + "APP_IMAGE_TAG=release-1\n")
    stack["env_file"].chmod(0o600)
    custom = stack["invoke"]("reinstall")
    assert custom.returncode == 0, custom.stderr
    calls = stack["docker_calls"]()
    assert ["image", "tag", stack["release_state"].read_text(), "ice-school-app:release-1"] in calls
    assert any(
        x[0] == "compose-tag" and x[1] == "release-1" and "web" in x[2]
        for x in calls
    )


def test_candidate_cleanup_after_successful_rollout(stack):
    response = stack["invoke"]("update", "--ref", "v1.2.3")
    calls = stack["docker_calls"]()
    promotion = next(
        (i, x) for i, x in enumerate(calls) if x[:2] == ["image", "tag"]
    )
    removals = [
        (i, x) for i, x in enumerate(calls) if x[:2] == ["image", "rm"]
    ]

    assert response.returncode == 0, response.stderr
    assert len(removals) == 1
    assert removals[0][1] == ["image", "rm", promotion[1][2]]
    assert removals[0][0] > promotion[0]
    assert stack["release_state"].read_text() == promotion[1][2]


def test_candidate_cleanup_after_migration_failure_preserves_release(stack):
    response = stack["invoke"](
        "update", "--ref", "v1.2.3", MOCK_MIGRATION_FAIL="1"
    )
    calls = stack["docker_calls"]()
    build_tag = next(
        x[1] for x in calls
        if x[0] == "compose-tag" and "build" in x[2]
    )

    assert response.returncode != 0
    assert [x for x in calls if x[:2] == ["image", "rm"]] == [
        ["image", "rm", "ice-school-app:" + build_tag]
    ]
    assert not any(x[:2] == ["image", "tag"] for x in calls)
    assert stack["release_state"].read_text() == "old-release-image"


def test_cleanup_failure_does_not_change_operation_exit_status(stack):
    succeeded = stack["invoke"]("reinstall", MOCK_IMAGE_RM_FAIL="1")
    assert succeeded.returncode == 0, succeeded.stderr
    assert "could not remove temporary image tag" in succeeded.stdout
    release = stack["release_state"].read_text()
    assert release.startswith("ice-school-app:candidate-")

    failed = stack["invoke"](
        "update", "--ref", "v1.2.3",
        MOCK_MIGRATION_FAIL="1", MOCK_IMAGE_RM_FAIL="1",
    )
    assert failed.returncode != 0
    assert "Migration gate failed" in failed.stderr
    assert "could not remove temporary image tag" in failed.stdout
    assert stack["release_state"].read_text() == release


def test_no_candidate_cleanup_when_backup_fails_before_build(stack):
    response = stack["invoke"]("update", MOCK_BACKUP_FAIL="1")
    assert response.returncode != 0
    assert not any(x[:2] == ["image", "rm"] for x in stack["docker_calls"]())


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
    assert stack["release_state"].read_text() == "old-release-image"


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
    assert stack["release_state"].read_text() == "old-release-image"
    assert not any(x[:2] == ["image", "tag"] for x in stack["docker_calls"]())
    assert any(
        x[0] == "compose-tag" and x[1].startswith("candidate-") and "migrate" in x[2]
        for x in stack["docker_calls"]()
    )


def test_tag_promotion_failure_prevents_rollout_and_keeps_release_image(stack):
    result = stack["invoke"](
        "update", "--ref", "v1.2.3", MOCK_TAG_FAIL="1"
    )
    assert result.returncode != 0
    assert stack["release_state"].read_text() == "old-release-image"
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["up"]
    )
    assert "Do NOT blindly roll back" in result.stdout


def test_multiple_web_container_ids_fail_with_clear_error(stack):
    result = stack["invoke"]("install", MOCK_MULTI_IDS="1")
    assert result.returncode != 0
    assert "multiple container IDs" in result.stderr


def test_update_success_orders_backup_build_gate_rollout(stack):
    response = stack["invoke"]("update", "--ref", "v1.2.3")
    commands = compose_commands(stack["docker_calls"]())

    assert response.returncode == 0, response.stderr
    backup = next(i for i, x in enumerate(commands) if x[:1] == ["run"] and "backup" in x)
    build = commands.index(["build", "migrate"])
    migrate = commands.index(["run", "--rm", "--no-deps", "-T", "migrate"])
    web = next(i for i, x in enumerate(commands) if x[:1] == ["up"] and "web" in x)
    calls = stack["docker_calls"]()
    promotion = next(i for i, x in enumerate(calls) if x[:2] == ["image", "tag"])
    migration_call = next(
        i for i, x in enumerate(calls)
        if x[0] == "compose-tag" and x[1].startswith("candidate-")
        and "run" in x[2] and "migrate" in x[2]
    )
    web_call = next(
        i for i, x in enumerate(calls)
        if x[0] == "compose-tag" and x[1] == "local" and "web" in x[2]
    )
    assert backup < build < migrate < web
    assert migration_call < promotion < web_call
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


def enable_offsite(stack):
    root = stack["root"]
    secret_dir = root.parent / (root.name + "-offsite-secrets")
    secret_dir.mkdir()
    password = secret_dir / "restic-password"
    credentials = secret_dir / "aws-credentials"
    password.write_text("secret-passphrase-for-tests")
    credentials.write_text("[default]\naws_access_key_id=test\naws_secret_access_key=test\n")
    password.chmod(0o600)
    credentials.chmod(0o600)
    stack["env_file"].write_text(
        VALID_ENV
        + "OFFSITE_ENABLED=1\n"
        + "OFFSITE_RESTIC_REPOSITORY=s3:https://s3.example.net/school-backup\n"
        + f"OFFSITE_RESTIC_PASSWORD_HOST_FILE={password}\n"
        + f"OFFSITE_AWS_CREDENTIALS_HOST_FILE={credentials}\n"
    )
    stack["env_file"].chmod(0o600)
    return password, credentials


def test_offsite_update_requires_remote_copy_before_migration(stack):
    enable_offsite(stack)
    response = stack["invoke"]("update", "--ref", "v1.2.3")
    commands = compose_commands(stack["docker_calls"]())
    assert response.returncode == 0, response.stderr
    local = next(
        i for i, x in enumerate(commands)
        if x[:1] == ["run"] and "backup" in x and "offsite-backup" not in x
    )
    remote = next(
        i for i, x in enumerate(commands)
        if x[:1] == ["run"] and "offsite-backup" in x
    )
    migrate = next(
        i for i, x in enumerate(commands)
        if x[:1] == ["run"] and "migrate" in x
    )
    assert local < remote < migrate
    assert any(x[:1] == ["up"] and "offsite-backup" in x for x in commands)


def test_offsite_upload_failure_blocks_migrations_and_checkout(stack):
    enable_offsite(stack)
    response = stack["invoke"]("update", MOCK_OFFSITE_FAIL="1")
    commands = compose_commands(stack["docker_calls"]())
    assert response.returncode != 0
    assert "Encrypted offsite backup failed" in response.stderr
    assert not any("migrate" in x for x in commands if x[:1] == ["run"])
    assert not any("fetch" in x for x in stack["git_calls"]())
    assert stack["release_state"].read_text() == "old-release-image"


def test_offsite_password_file_must_be_private(stack):
    password, _ = enable_offsite(stack)
    password.chmod(0o644)
    response = stack["invoke"]("install")
    assert response.returncode != 0
    assert "OFFSITE_RESTIC_PASSWORD_HOST_FILE" in response.stderr
    assert not contains_command(compose_commands(stack["docker_calls"]()), ["build"])


def test_offsite_secrets_must_not_reside_inside_external_backup_dir(stack):
    password, _ = enable_offsite(stack)
    backup_dir = stack["root"].parent / (stack["root"].name + "-host-backups")
    backup_dir.mkdir()
    password_inside = backup_dir / "restic-password"
    password_inside.write_text(password.read_text())
    password_inside.chmod(0o600)

    settings = stack["env_file"].read_text()
    settings = settings.replace("BACKUP_HOST_PATH=./backups",
                                f"BACKUP_HOST_PATH={backup_dir}")
    settings = settings.replace(str(password), str(password_inside))
    stack["env_file"].write_text(settings)
    stack["env_file"].chmod(0o600)

    result = stack["invoke"]("install")
    assert result.returncode != 0
    assert "outside the Git checkout and local backups" in result.stderr
    assert not contains_command(
        compose_commands(stack["docker_calls"]()), ["build"]
    )


def test_offsite_requires_tls_repository_not_plain_http(stack):
    enable_offsite(stack)
    content = stack["env_file"].read_text().replace("s3:https://", "s3:http://")
    stack["env_file"].write_text(content)
    result = stack["invoke"]("install")
    assert result.returncode != 0
    assert "OFFSITE_RESTIC_REPOSITORY" in result.stderr


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
