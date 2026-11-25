"""M1-T2 — tests for the deployment config loader.

The loader is the one door to ``~/glucose/secret.env``. What these tests pin:

* a well-formed ``0600`` file loads, and the derived URLs are the ones the rest of the
  pipeline will dial;
* a missing file, a missing key, a short or whitespace-bearing secret, a malformed
  token and an unusable bind address all fail loudly — never silently defaulted;
* group/world-accessible files are refused, including through a symlink to a loose
  target;
* no failure message and no ``repr()`` ever carries a secret value.

Fixtures are written with dummy values under ``tmp/``; nothing here reads the real
``~/glucose/secret.env`` or the network.
"""

import os
import textwrap
from pathlib import Path

import pytest

from glucoseledger.config import (
    DEFAULT_PORT,
    DEFAULT_SECRET_ENV,
    ENV_OVERRIDE,
    ConfigError,
    load_config,
)

DUMMY_SECRET = "9f2c" * 16  # 64 chars, dummy, never a real secret
DUMMY_TOKEN = "0123456789abcdef"

GOOD_ENV = f"""\
# glucose-ledger deployment secrets (dummy values in tests)
API_SECRET={DUMMY_SECRET}
GLUCOSE_READ_TOKEN={DUMMY_TOKEN}
GLUCOSE_BIND_IP=192.168.0.30
GLUCOSE_HOST=hermes.local
GLUCOSE_PORT=1337
DISPLAY_UNITS=mg/dl
"""


def write_env(tmp_path: Path, text: str = GOOD_ENV, mode: int = 0o600) -> Path:
    path = tmp_path / "secret.env"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    path.chmod(mode)
    return path


def test_loads_a_well_formed_file(tmp_path):
    config = load_config(write_env(tmp_path))

    assert config.api_secret == DUMMY_SECRET
    assert config.read_token == DUMMY_TOKEN
    assert config.bind_ip == "192.168.0.30"
    assert config.host == "hermes.local"
    assert config.port == 1337
    assert config.units == "mg/dl"
    assert config.source_path == tmp_path / "secret.env"


def test_dialable_urls(tmp_path):
    config = load_config(write_env(tmp_path))
    assert config.base_url == "http://hermes.local:1337"
    assert config.api_url == "http://hermes.local:1337/api/v1"
    assert config.token_url == f"http://{DUMMY_TOKEN}@hermes.local:1337"


def test_host_defaults_to_the_bind_address(tmp_path):
    text = GOOD_ENV.replace("GLUCOSE_HOST=hermes.local\n", "")
    config = load_config(write_env(tmp_path, text))
    assert config.host == "192.168.0.30"
    assert config.base_url == "http://192.168.0.30:1337"


def test_port_and_units_default_when_absent(tmp_path):
    text = GOOD_ENV.replace("GLUCOSE_PORT=1337\n", "").replace("DISPLAY_UNITS=mg/dl\n", "")
    config = load_config(write_env(tmp_path, text))
    assert config.port == DEFAULT_PORT
    assert config.units == "mg/dl"


def test_units_are_case_insensitive_and_mmol_is_accepted(tmp_path):
    config = load_config(write_env(tmp_path, GOOD_ENV.replace("mg/dl", "MG/DL")))
    assert config.units == "mg/dl"

    config = load_config(write_env(tmp_path, GOOD_ENV.replace("mg/dl", "mmol")))
    assert config.units == "mmol"


def test_units_reject_anything_else(tmp_path):
    with pytest.raises(ConfigError, match="DISPLAY_UNITS"):
        load_config(write_env(tmp_path, GOOD_ENV.replace("mg/dl", "mmol/l")))


def test_missing_file_fails_with_the_creation_command(tmp_path):
    missing = tmp_path / "nope" / "secret.env"
    with pytest.raises(ConfigError) as excinfo:
        load_config(missing)
    message = str(excinfo.value)
    assert str(missing) in message
    assert "0600" in message


def test_directory_is_not_a_secrets_file(tmp_path):
    (tmp_path / "secret.env").mkdir(mode=0o700)
    with pytest.raises(ConfigError, match="regular file"):
        load_config(tmp_path / "secret.env")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
def test_group_or_world_accessible_file_is_refused(tmp_path, mode):
    path = write_env(tmp_path, mode=mode)
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert f"{mode:04o}" in message
    assert "chmod 600" in message


def test_symlink_to_a_loose_target_is_refused(tmp_path):
    loose = tmp_path / "loose.env"
    loose.write_text(GOOD_ENV, encoding="utf-8")
    loose.chmod(0o644)
    link = tmp_path / "secret.env"
    os.symlink(loose, link)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(link)


def test_every_missing_required_key_is_named_at_once(tmp_path):
    text = f"GLUCOSE_BIND_IP=192.168.0.30\nAPI_SECRET={DUMMY_SECRET}\n"
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_env(tmp_path, text))
    message = str(excinfo.value)
    named = message.split("missing required key(s):")[1].split(".")[0]
    assert "GLUCOSE_READ_TOKEN" in named
    assert "API_SECRET" not in named
    assert "GLUCOSE_BIND_IP" not in named


def test_empty_value_counts_as_missing(tmp_path):
    text = GOOD_ENV.replace(f"GLUCOSE_READ_TOKEN={DUMMY_TOKEN}", "GLUCOSE_READ_TOKEN=")
    with pytest.raises(ConfigError, match="GLUCOSE_READ_TOKEN"):
        load_config(write_env(tmp_path, text))


def test_short_api_secret_is_refused(tmp_path):
    text = GOOD_ENV.replace(DUMMY_SECRET, "a" * 31)
    with pytest.raises(ConfigError, match="at least 32"):
        load_config(write_env(tmp_path, text))


def test_api_secret_with_whitespace_is_refused(tmp_path):
    text = GOOD_ENV.replace(DUMMY_SECRET, "a" * 20 + " " + "b" * 20)
    with pytest.raises(ConfigError, match="whitespace"):
        load_config(write_env(tmp_path, text))


@pytest.mark.parametrize("token", ["0123456789abcde", "0123456789abcdef0", "0123456789abcdeZ"])
def test_malformed_read_token_is_refused(tmp_path, token):
    text = GOOD_ENV.replace(DUMMY_TOKEN, token)
    with pytest.raises(ConfigError, match="GLUCOSE_READ_TOKEN"):
        load_config(write_env(tmp_path, text))


@pytest.mark.parametrize("bad_ip", ["0.0.0.0", "hermes.local", "192.168.0.300", "::1"])
def test_bind_address_must_be_a_specific_ipv4(tmp_path, bad_ip):
    text = GOOD_ENV.replace("GLUCOSE_BIND_IP=192.168.0.30", f"GLUCOSE_BIND_IP={bad_ip}")
    with pytest.raises(ConfigError, match="GLUCOSE_BIND_IP"):
        load_config(write_env(tmp_path, text))


def test_bind_all_interfaces_is_refused_with_its_reason(tmp_path):
    text = GOOD_ENV.replace("GLUCOSE_BIND_IP=192.168.0.30", "GLUCOSE_BIND_IP=0.0.0.0")
    with pytest.raises(ConfigError, match="never 0.0.0.0"):
        load_config(write_env(tmp_path, text))


@pytest.mark.parametrize("host", ["http://hermes.local", "hermes.local:1337", "her/mes", "a..b"])
def test_host_must_be_bare(tmp_path, host):
    text = GOOD_ENV.replace("GLUCOSE_HOST=hermes.local", f"GLUCOSE_HOST={host}")
    with pytest.raises(ConfigError, match="GLUCOSE_HOST"):
        load_config(write_env(tmp_path, text))


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1"])
def test_port_must_be_a_real_port(tmp_path, port):
    text = GOOD_ENV.replace("GLUCOSE_PORT=1337", f"GLUCOSE_PORT={port}")
    with pytest.raises(ConfigError, match="GLUCOSE_PORT"):
        load_config(write_env(tmp_path, text))


def test_comments_blank_lines_export_prefix_and_quotes_are_accepted(tmp_path):
    text = f"""\
        # leading comment

        export API_SECRET="{DUMMY_SECRET}"
        GLUCOSE_READ_TOKEN='{DUMMY_TOKEN}'

        # trailing comment
        GLUCOSE_BIND_IP="192.168.0.30"
    """
    config = load_config(write_env(tmp_path, text))
    assert config.api_secret == DUMMY_SECRET
    assert config.read_token == DUMMY_TOKEN
    assert config.bind_ip == "192.168.0.30"


def test_duplicate_key_is_refused(tmp_path):
    text = GOOD_ENV + "GLUCOSE_BIND_IP=192.168.0.31\n"
    with pytest.raises(ConfigError, match="duplicate key GLUCOSE_BIND_IP"):
        load_config(write_env(tmp_path, text))


def test_line_without_equals_is_refused_with_its_line_number(tmp_path):
    text = GOOD_ENV + "this is not a setting\n"
    with pytest.raises(ConfigError, match=":8: expected KEY=VALUE"):
        load_config(write_env(tmp_path, text))


def test_env_override_selects_the_file_and_expands_a_tilde(tmp_path, monkeypatch):
    write_env(tmp_path)
    monkeypatch.setenv(ENV_OVERRIDE, str(tmp_path / "secret.env"))
    assert load_config().source_path == tmp_path / "secret.env"
    assert load_config().host == "hermes.local"

    # A tilde is expanded against HOME, so the same file is found via "~/..."
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_OVERRIDE, "~/secret.env")
    home_relative = load_config()
    assert home_relative.source_path == tmp_path / "secret.env"
    assert home_relative.host == "hermes.local"


def test_default_path_is_outside_the_repo():
    repo_root = Path(__file__).resolve().parents[1]
    assert DEFAULT_SECRET_ENV == Path.home() / "glucose" / "secret.env"
    assert repo_root not in DEFAULT_SECRET_ENV.parents
    assert DEFAULT_SECRET_ENV.name == "secret.env"


def test_repr_and_public_summary_never_carry_a_credential(tmp_path):
    config = load_config(write_env(tmp_path))
    for rendered in (repr(config), str(config), str(config.public_summary())):
        assert DUMMY_SECRET not in rendered
        assert DUMMY_TOKEN not in rendered
    assert config.public_summary()["host"] == "hermes.local"


def test_failure_messages_never_carry_the_secret(tmp_path):
    # Valid secret, but the file is loose: the error must describe the mode, not the data.
    path = write_env(tmp_path, mode=0o644)
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert DUMMY_SECRET not in str(excinfo.value)
    assert DUMMY_TOKEN not in str(excinfo.value)

    # Same when a later validation step fails on an otherwise well-formed file.
    text = GOOD_ENV.replace("GLUCOSE_PORT=1337", "GLUCOSE_PORT=abc")
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_env(tmp_path, text))
    assert DUMMY_SECRET not in str(excinfo.value)
    assert DUMMY_TOKEN not in str(excinfo.value)


def test_load_is_repeatable_and_pure(tmp_path):
    path = write_env(tmp_path)
    first = load_config(path)
    second = load_config(path)
    assert first == second
    assert first.public_summary() == second.public_summary()
