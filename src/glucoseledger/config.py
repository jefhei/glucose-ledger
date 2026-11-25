"""Load and validate the deployment secrets file (``~/glucose/secret.env``).

BUILD_PLAN M1-T2, PRD F2. The secrets live **outside** the repo on purpose: the file
holds ``API_SECRET`` and the read token, and anything with that pair can read or write
the glucose store. This module is the only place in the codebase that reads it.

Every check below fails loudly with an actionable message, and no error message or
``repr()`` ever contains a secret value — a stack trace pasted into a chat must not leak
the store.

The expected file (mode ``0600``, one ``KEY=VALUE`` per line, ``#`` comments allowed)::

    API_SECRET=<32+ characters, no whitespace>
    GLUCOSE_READ_TOKEN=<16 hex characters, from the Nightscout admin UI>
    GLUCOSE_BIND_IP=<LAN IPv4 address the stack is published on>
    GLUCOSE_HOST=<host the API client dials; defaults to GLUCOSE_BIND_IP>
    GLUCOSE_PORT=<published port; defaults to 1337>
    DISPLAY_UNITS=<mg/dl | mmol; defaults to mg/dl>

Nothing here reads the clock or the network, so a config error is reproducible.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_SECRET_ENV",
    "ENV_OVERRIDE",
    "ConfigError",
    "SecretConfig",
    "load_config",
]

#: Where the deployment keeps its secrets. Overridable for tests and for a second host.
DEFAULT_SECRET_ENV = Path.home() / "glucose" / "secret.env"

#: Environment variable that points the loader at a different secrets file.
ENV_OVERRIDE = "GLUCOSE_SECRET_ENV"

REQUIRED_KEYS = ("API_SECRET", "GLUCOSE_READ_TOKEN", "GLUCOSE_BIND_IP")
OPTIONAL_KEYS = ("GLUCOSE_HOST", "GLUCOSE_PORT", "DISPLAY_UNITS")

DEFAULT_PORT = 1337
DEFAULT_UNITS = "mg/dl"
ALLOWED_UNITS = ("mg/dl", "mmol")

#: PRD F2: API_SECRET is 32+ random bytes. A shorter secret is not a secret.
MIN_SECRET_CHARS = 32

#: Nightscout access tokens are the first 16 hex characters of a SHA-1 digest.
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16}$")

#: A bare host name or dotted-quad address. No scheme, no path, no credentials.
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?$")

WHITESPACE_RE = re.compile(r"\s")


class ConfigError(Exception):
    """The deployment configuration is missing, unreadable, unsafe or malformed."""


@dataclass(frozen=True)
class SecretConfig:
    """Validated deployment settings.

    ``api_secret`` and ``read_token`` are marked ``repr=False`` so they cannot end up in
    a log line, a pytest failure message or a crash report.
    """

    api_secret: str = field(repr=False)
    read_token: str = field(repr=False)
    bind_ip: str
    host: str
    port: int
    units: str
    source_path: Path

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1"

    @property
    def token_url(self) -> str:
        """The ``http://<token>@host:port`` form xDrip+ and the API client use."""
        return f"http://{self.read_token}@{self.host}:{self.port}"

    def public_summary(self) -> dict[str, object]:
        """Everything that is safe to print, with no credential in it."""
        return {
            "source_path": str(self.source_path),
            "bind_ip": self.bind_ip,
            "host": self.host,
            "port": self.port,
            "units": self.units,
        }


def load_config(path: str | Path | None = None) -> SecretConfig:
    """Read, validate and return the deployment settings.

    ``path`` wins; otherwise ``$GLUCOSE_SECRET_ENV``; otherwise ``~/glucose/secret.env``.
    """
    source = _resolve_path(path)
    values = _parse_env_text(_read_secret_file(source), source)

    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ConfigError(
            f"{source}: missing required key(s): {', '.join(missing)}. "
            "Add them to the file (mode 0600); see the module docstring for the key list."
        )

    api_secret = values["API_SECRET"]
    if WHITESPACE_RE.search(api_secret):
        raise ConfigError(f"{source}: API_SECRET must not contain whitespace")
    if len(api_secret) < MIN_SECRET_CHARS:
        raise ConfigError(
            f"{source}: API_SECRET is {len(api_secret)} characters; "
            f"it must be at least {MIN_SECRET_CHARS}"
        )

    read_token = values["GLUCOSE_READ_TOKEN"]
    if not TOKEN_RE.match(read_token):
        raise ConfigError(
            f"{source}: GLUCOSE_READ_TOKEN must be the 16 hex character token from the "
            "Nightscout admin UI (no spaces, no quotes)"
        )

    bind_ip = _validated_bind_ip(values["GLUCOSE_BIND_IP"], source)
    host = _validated_host(values.get("GLUCOSE_HOST") or bind_ip, source)
    port = _validated_port(values.get("GLUCOSE_PORT"), source)
    units = _validated_units(values.get("DISPLAY_UNITS"), source)

    return SecretConfig(
        api_secret=api_secret,
        read_token=read_token,
        bind_ip=bind_ip,
        host=host,
        port=port,
        units=units,
        source_path=source,
    )


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    from_env = os.environ.get(ENV_OVERRIDE)
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_SECRET_ENV


def _read_secret_file(source: Path) -> str:
    """Read the file only if it is a regular file that group/other cannot touch."""
    try:
        info = os.stat(source)
    except FileNotFoundError:
        raise ConfigError(
            f"{source}: no secrets file. Create it with the required keys and mode 0600:\n"
            f"  install -m 600 /dev/null {source}"
        ) from None
    except OSError as exc:
        raise ConfigError(f"{source}: cannot stat secrets file ({exc.strerror})") from None

    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"{source}: must be a regular file, not a directory or device")

    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise ConfigError(
            f"{source}: mode {mode:04o} is accessible by group/other; run: chmod 600 {source}"
        )

    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{source}: cannot read secrets file ({exc.strerror})") from None


def _parse_env_text(text: str, source: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines. Deliberately strict: this is not a full dotenv."""
    values: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ConfigError(f"{source}:{lineno}: expected KEY=VALUE, found a line without '='")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ConfigError(f"{source}:{lineno}: expected KEY=VALUE, found an empty key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            # A duplicate key is a half-finished edit, not a default to be overridden.
            raise ConfigError(f"{source}:{lineno}: duplicate key {key}")
        values[key] = value
    return values


def _validated_bind_ip(value: str, source: Path) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        raise ConfigError(
            f"{source}: GLUCOSE_BIND_IP must be a bare IPv4 address, got {value!r}"
        ) from None
    if address.is_unspecified:
        raise ConfigError(
            f"{source}: GLUCOSE_BIND_IP must be the LAN address, never 0.0.0.0 "
            "(the UI must not listen on every interface)"
        )
    return str(address)


def _validated_host(value: str, source: Path) -> str:
    if not HOST_RE.match(value) or ".." in value:
        raise ConfigError(
            f"{source}: GLUCOSE_HOST must be a bare host name or IPv4 address "
            "(no scheme, no path, no port); the value has an illegal character"
        )
    return value


def _validated_port(value: str | None, source: Path) -> int:
    if value is None or value == "":
        return DEFAULT_PORT
    if not value.isdigit():
        raise ConfigError(f"{source}: GLUCOSE_PORT must be a number, got {value!r}")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ConfigError(f"{source}: GLUCOSE_PORT must be 1-65535, got {port}")
    return port


def _validated_units(value: str | None, source: Path) -> str:
    if value is None or value == "":
        return DEFAULT_UNITS
    units = value.lower()
    if units not in ALLOWED_UNITS:
        raise ConfigError(
            f"{source}: DISPLAY_UNITS must be one of {', '.join(ALLOWED_UNITS)}, got {value!r}"
        )
    return units
