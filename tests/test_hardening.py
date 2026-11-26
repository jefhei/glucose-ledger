"""M1-T3 — hardening environment and the written exposure model.

Three properties are pinned here, because each of them is a way a home CGM server
becomes a public glucose feed:

* ``AUTH_DEFAULT_ROLES=denied`` — an unauthenticated caller has no role at all,
* ``DISPLAY_UNITS`` and ``TIME_FORMAT`` are set from the same vocabulary the config
  loader validates, and the display unit never becomes a stored-value conversion,
* ``deploy/README.md`` states the exposure model: LAN bind, no port forward, no TLS
  terminator in the MVP, remote access only through an overlay network later.

The README assertions are deliberately about *content*, not formatting: a future edit
may reword the prose, but it may not delete the rule.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from glucoseledger import config as cfg

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"
README = REPO_ROOT / "deploy" / "README.md"

BASE_ENV = {
    "API_SECRET": "dummy-secret-for-hardening-tests-not-real",
    "GLUCOSE_BIND_IP": "192.0.2.10",
}

FORBIDDEN_ROLES = ("readable", "careportal", "admin", "api:*", "*", "allow", "public")

# The loader needs a token in its file; keep it constructed so no literal token-shaped
# string lands in a tracked file (M1-T6 greps for those).
SECRET_ENV = {**BASE_ENV, "GLUCOSE_READ_TOKEN": "0" * 16}


def compose_doc() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def nightscout_env() -> dict:
    return compose_doc()["services"]["nightscout"]["environment"]


def rendered_config(tmp_path: Path, env: dict[str, str]) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("skipped: no docker binary on this host")
    env_file = tmp_path / "deploy.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    env_file.chmod(0o600)
    result = subprocess.run(
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
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


# --- compose hardening -------------------------------------------------------


def test_unauthenticated_readers_are_denied_a_role():
    env = nightscout_env()
    assert env["AUTH_DEFAULT_ROLES"] == "denied"
    # and the value is not silently interpolated from an outside file
    assert not str(env["AUTH_DEFAULT_ROLES"]).startswith("${")


def test_no_role_granting_value_appears_anywhere_in_the_compose_file():
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(r"^\s*AUTH_DEFAULT_ROLES:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match, "AUTH_DEFAULT_ROLES must be set explicitly in the compose file"
    value = match.group(1)
    assert value not in FORBIDDEN_ROLES, f"AUTH_DEFAULT_ROLES={value} grants anonymous access"


def test_display_units_default_matches_the_config_loader_vocabulary():
    env = nightscout_env()
    assert env["DISPLAY_UNITS"] == f"${{DISPLAY_UNITS:-{cfg.DEFAULT_UNITS}}}"
    assert cfg.DEFAULT_UNITS == "mg/dl"
    assert cfg.ALLOWED_UNITS == ("mg/dl", "mmol")


def test_time_format_is_the_string_24():
    assert nightscout_env()["TIME_FORMAT"] == "24"


def test_time_format_is_quoted_in_the_yaml_so_compose_gets_a_string():
    text = COMPOSE.read_text(encoding="utf-8")
    assert re.search(r'^\s*TIME_FORMAT:\s*"24"\s*$', text, re.MULTILINE)


def test_hardening_vars_are_only_on_the_web_service():
    services = compose_doc()["services"]
    for key in ("AUTH_DEFAULT_ROLES", "DISPLAY_UNITS", "TIME_FORMAT"):
        assert key not in services["mongo"].get("environment", {})


def test_compose_still_holds_no_secret_after_hardening():
    text = COMPOSE.read_text(encoding="utf-8")
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text)
    assert "API_SECRET: ${API_SECRET:?" in text


def test_rendered_config_applies_every_hardening_value(tmp_path):
    rendered = rendered_config(tmp_path, BASE_ENV)
    env = rendered["services"]["nightscout"]["environment"]
    assert env["AUTH_DEFAULT_ROLES"] == "denied"
    assert env["DISPLAY_UNITS"] == "mg/dl"
    assert env["TIME_FORMAT"] == "24"
    # compose must hand Nightscout strings, not numbers or booleans
    for key in ("AUTH_DEFAULT_ROLES", "DISPLAY_UNITS", "TIME_FORMAT"):
        assert isinstance(env[key], str)
    # the hardening did not open a listener: still one LAN-bound port, Mongo unpublished
    assert len(rendered["services"]["nightscout"]["ports"]) == 1
    assert rendered["services"]["nightscout"]["ports"][0]["host_ip"] == BASE_ENV["GLUCOSE_BIND_IP"]
    assert "ports" not in rendered["services"]["mongo"]


def test_units_override_from_the_secret_file_reaches_the_render(tmp_path):
    """The loader validates the unit, and the same value is what compose renders."""
    secret = tmp_path / "secret.env"
    secret.write_text(
        "".join(f"{k}={v}\n" for k, v in {**SECRET_ENV, "DISPLAY_UNITS": "mmol"}.items()),
        encoding="utf-8",
    )
    secret.chmod(0o600)
    loaded = cfg.load_config(secret)

    env = {**BASE_ENV, "DISPLAY_UNITS": loaded.units}
    rendered = rendered_config(tmp_path, env)
    assert rendered["services"]["nightscout"]["environment"]["DISPLAY_UNITS"] == "mmol"


def test_loader_refuses_a_unit_the_render_could_not_support(tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text(
        "".join(f"{k}={v}\n" for k, v in {**SECRET_ENV, "DISPLAY_UNITS": "mMol/L"}.items()),
        encoding="utf-8",
    )
    secret.chmod(0o600)
    with pytest.raises(cfg.ConfigError) as excinfo:
        cfg.load_config(secret)
    assert "DISPLAY_UNITS" in str(excinfo.value)
    assert "dummy-secret" not in str(excinfo.value)


# --- deploy/README.md exposure notes ----------------------------------------


def readme_text() -> str:
    assert README.is_file(), "deploy/README.md must document the exposure model"
    return README.read_text(encoding="utf-8")


def test_readme_documents_the_hardening_environment():
    text = readme_text()
    for needle in ("AUTH_DEFAULT_ROLES=denied", "TIME_FORMAT=24", "DISPLAY_UNITS"):
        assert needle in text


def test_readme_states_no_port_forward():
    text = readme_text()
    assert re.search(r"no port forward", text, re.IGNORECASE)
    assert "GLUCOSE_BIND_IP" in text


def test_readme_names_the_lan_bind_and_the_401_expectation():
    text = readme_text()
    assert "0.0.0.0" in text  # the refused value is named, next to the refusal
    assert re.search(r"never", text)
    assert "401" in text
    assert "/api/v1/entries" in text


def test_readme_does_not_contain_a_publish_instruction():
    text = readme_text()
    for forbidden in ("--publish", "1337:1337", "iptables -t nat", "dmz"):
        assert forbidden not in text.lower(), f"deploy/README.md must not suggest {forbidden}"


def test_readme_keeps_remote_access_as_a_later_overlay_step():
    text = readme_text()
    assert "Tailscale" in text
    assert "M7-T3" in text


def test_readme_says_the_stack_is_plain_http_on_the_lan_only():
    text = readme_text()
    assert "INSECURE_USE_HTTP" in text
    assert re.search(r"plain http", text, re.IGNORECASE)


def test_readme_never_puts_a_secret_in_the_repo():
    text = readme_text()
    assert "~/glucose/secret.env" in text
    assert "0600" in text
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text), "no secret-shaped literal in the README"
