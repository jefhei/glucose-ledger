"""M1-T1 — contract tests for the deployable Nightscout stack.

The stack itself is brought up by ``make stack-up`` (M1-T5) once the config loader
(M1-T2) exists. These tests pin the properties that make the deploy file safe and
reproducible *before* anything runs, so a careless edit fails the build:

* both images are pinned by tag, and Mongo stays on the last major that runs on this
  host (no AVX — MongoDB 5.0+ dies SIGILL, measured in M0-T1),
* the UI is published on one LAN address, never on all interfaces, and the database is
  not published at all,
* the Mongo data lives in a named volume with a fixed project-scoped name, so a data
  directory cannot be silently orphaned by running the file from another directory,
* no secret material lives in the template — ``API_SECRET`` and the bind address are
  required variables supplied from outside the repo,
* ``docker compose config`` validates the render and fails loudly when a required
  variable is missing.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"

DUMMY_ENV = {
    "API_SECRET": "dummy-secret-for-compose-config-not-real",
    "GLUCOSE_BIND_IP": "192.0.2.10",
}


def compose_doc() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("skipped: no docker binary on this host")


def run_compose_config(tmp_path: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env_file = tmp_path / "deploy.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in extra_env.items()), encoding="utf-8")
    env_file.chmod(0o600)
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_images_are_pinned_and_mongo_matches_the_host():
    services = compose_doc()["services"]
    assert services["nightscout"]["image"] == "nightscout/cgm-remote-monitor:15.0.9"
    # 4.4 is deliberate: the debian2 host CPU has no AVX and 5.0+ dies with SIGILL.
    assert services["mongo"]["image"] == "mongo:4.4"
    for name, service in services.items():
        image = service["image"]
        assert ":" in image and not image.endswith(":latest"), f"{name} must be pinned by tag"


def test_both_services_restart_unless_stopped():
    services = compose_doc()["services"]
    assert services["nightscout"]["restart"] == "unless-stopped"
    assert services["mongo"]["restart"] == "unless-stopped"


def test_project_name_is_pinned():
    """A fixed project name keeps the Mongo volume's identity across run directories."""
    assert compose_doc()["name"] == "glucose-ledger"


def test_ui_binds_one_lan_address_not_all_interfaces():
    ports = compose_doc()["services"]["nightscout"]["ports"]
    assert len(ports) == 1
    spec = str(ports[0])
    assert spec.startswith("${GLUCOSE_BIND_IP:?"), "host address must be a required variable"
    assert spec.endswith(":${GLUCOSE_PORT:-1337}:1337")
    directive_lines = [
        line
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert all("0.0.0.0" not in line for line in directive_lines), "never bind all interfaces"


def test_database_is_not_published():
    assert "ports" not in compose_doc()["services"]["mongo"]


def test_no_host_networking_or_privileged_containers():
    for name, service in compose_doc()["services"].items():
        assert "network_mode" not in service, f"{name} must use the compose network"
        assert service.get("privileged") is not True, f"{name} must not be privileged"


def test_mongo_data_is_a_named_volume_and_nothing_binds_into_the_repo():
    doc = compose_doc()
    assert list(doc["volumes"]) == ["mongo_data"]
    mounts = doc["services"]["mongo"]["volumes"]
    assert mounts == ["mongo_data:/data/db"]
    for name, service in doc["services"].items():
        for mount in service.get("volumes", []):
            source = str(mount).split(":", 1)[0]
            assert not source.startswith(("/", ".", "~")), f"{name} must not bind-mount {source}"


def test_template_holds_no_secret():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "API_SECRET: ${API_SECRET:?" in text
    # no 32+ byte hex literal, i.e. nothing that could be mistaken for a real secret
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text)


def test_compose_config_validates_and_binds_the_given_address(tmp_path):
    require_docker()
    result = run_compose_config(tmp_path, DUMMY_ENV)
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)

    assert rendered["name"] == "glucose-ledger"
    assert rendered["services"]["nightscout"]["image"] == "nightscout/cgm-remote-monitor:15.0.9"
    assert rendered["services"]["mongo"]["image"] == "mongo:4.4"

    port = rendered["services"]["nightscout"]["ports"][0]
    assert port["host_ip"] == DUMMY_ENV["GLUCOSE_BIND_IP"]
    assert int(port["published"]) == 1337
    assert int(port["target"]) == 1337
    assert "ports" not in rendered["services"]["mongo"]

    assert rendered["volumes"]["mongo_data"]["name"] == "glucose-ledger_mongo_data"
    mount = rendered["services"]["mongo"]["volumes"][0]
    assert mount["type"] == "volume"
    assert mount["source"] == "mongo_data"
    assert mount["target"] == "/data/db"


def test_compose_config_fails_loudly_without_a_secret(tmp_path):
    require_docker()
    env = {k: v for k, v in DUMMY_ENV.items() if k != "API_SECRET"}
    result = run_compose_config(tmp_path, env)
    assert result.returncode != 0
    assert "API_SECRET" in result.stderr + result.stdout


def test_compose_config_fails_loudly_without_a_bind_address(tmp_path):
    require_docker()
    env = {k: v for k, v in DUMMY_ENV.items() if k != "GLUCOSE_BIND_IP"}
    result = run_compose_config(tmp_path, env)
    assert result.returncode != 0
    assert "GLUCOSE_BIND_IP" in result.stderr + result.stdout
