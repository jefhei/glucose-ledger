"""M0-T1 — contract tests for the throwaway Nightscout staging stack.

The live path (bring the stack up, POST a reading, read it back, tear it down) is
exercised by ``scripts/ns_spike.sh``; these tests pin the properties that keep the
spike safe and repeatable without needing Docker:

* both images are pinned by tag (a ``latest`` pull against a populated Mongo volume is
  the corruption risk PRD §Risk names),
* the UI is published on one LAN address, never on all interfaces,
* the database is not published at all,
* no secret material lives in the template, and the renderer refuses to place its
  secret inside the repo,
* the compose file validates and fails loudly when the secret is missing.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "spike" / "docker-compose.spike.yml"
SCRIPT = REPO_ROOT / "scripts" / "ns_spike.sh"

DUMMY_ENV = {
    "SPIKE_API_SECRET": "dummy-secret-for-compose-config-not-real",
    "SPIKE_BIND_IP": "192.0.2.10",
    "SPIKE_PORT": "1337",
}


def compose_doc() -> dict:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("skipped: no docker binary on this host")


def run_compose_config(tmp_path: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env_file = tmp_path / "spike.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in extra_env.items()), encoding="utf-8")
    env_file.chmod(0o600)
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(TEMPLATE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_images_are_pinned_by_tag():
    services = compose_doc()["services"]
    assert services["nightscout"]["image"] == "nightscout/cgm-remote-monitor:15.0.9"
    assert services["mongo"]["image"] == "mongo:4.4"
    for name, service in services.items():
        assert not service["image"].endswith(":latest"), f"{name} must not use :latest"


def test_ui_binds_one_lan_address_not_all_interfaces():
    ports = compose_doc()["services"]["nightscout"]["ports"]
    assert len(ports) == 1
    spec = ports[0]
    assert spec.startswith("${SPIKE_BIND_IP:?"), "host address must be a required variable"
    assert spec.endswith(":${SPIKE_PORT:-1337}:1337")
    assert "0.0.0.0" not in str(ports)


def test_database_is_not_published():
    assert "ports" not in compose_doc()["services"]["mongo"]


def test_template_holds_no_secret():
    """The secret is required from the environment; the file carries no value."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "API_SECRET: ${SPIKE_API_SECRET:?" in text
    # no 32+ byte hex literal, i.e. nothing that could be mistaken for a real secret
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text)


def test_script_targets_tmp_and_tears_down_volumes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SPIKE_DIR=${SPIKE_DIR:-/tmp/ns-spike}" in text
    assert "down -v" in text, "the throwaway volume must go with the stack"
    assert "chmod 700" in text and "chmod 600" in text


def test_script_syntax():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_render_writes_secret_outside_repo_with_0600(tmp_path):
    spike_dir = tmp_path / "ns-spike"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPIKE_")}
    env["SPIKE_DIR"] = str(spike_dir)
    env["SPIKE_BIND_IP"] = DUMMY_ENV["SPIKE_BIND_IP"]

    result = subprocess.run(
        ["bash", str(SCRIPT), "render"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    secret = (spike_dir / "secret.env").read_text(encoding="utf-8")
    assert re.fullmatch(r"API_SECRET=[0-9a-f]{64}\n", secret), "32 random bytes, hex encoded"
    assert (spike_dir / "secret.env").stat().st_mode & 0o777 == 0o600
    assert (spike_dir / "spike.env").stat().st_mode & 0o777 == 0o600
    token = (spike_dir / "read.token").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", token), "Nightscout access token is the SHA1 of the secret"
    rendered = (spike_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert rendered == TEMPLATE.read_text(encoding="utf-8")

    # the generated secret must not appear in any tracked file
    secret_value = secret.split("=", 1)[1].strip()
    leak = subprocess.run(
        ["git", "grep", "-F", secret_value, "--", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert leak.returncode == 1, f"secret found in tracked files: {leak.stdout}"


def test_render_refuses_a_spike_dir_inside_the_repo(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPIKE_")}
    env["SPIKE_DIR"] = str(REPO_ROOT / "spike" / "ns-spike-should-not-exist")
    result = subprocess.run(
        ["bash", str(SCRIPT), "render"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode != 0
    assert "outside the repo" in result.stderr
    assert not (REPO_ROOT / "spike" / "ns-spike-should-not-exist").exists()


def test_compose_config_validates_and_binds_the_given_address(tmp_path):
    require_docker()
    result = run_compose_config(tmp_path, DUMMY_ENV)
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert rendered["name"] == "ns-spike"
    port = rendered["services"]["nightscout"]["ports"][0]
    assert port["host_ip"] == DUMMY_ENV["SPIKE_BIND_IP"]
    assert int(port["published"]) == 1337
    assert int(port["target"]) == 1337
    assert "ports" not in rendered["services"]["mongo"]


def test_compose_config_fails_loudly_without_a_secret(tmp_path):
    require_docker()
    env = {k: v for k, v in DUMMY_ENV.items() if k != "SPIKE_API_SECRET"}
    result = run_compose_config(tmp_path, env)
    assert result.returncode != 0
    assert "SPIKE_API_SECRET" in result.stderr + result.stdout
