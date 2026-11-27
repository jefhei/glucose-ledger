"""Prove the deployed stack's access contract: anonymous reads get 401, token reads get 200.

BUILD_PLAN M1-T4, PRD F2 and the ``[auto]`` invariant "unauthenticated reads of the
Nightscout API return 401 (asserted by a test, on the deployed instance, not on a mock)".

Two requests are made against the real stack, both ``GET`` (a probe must never write):

1. unauthenticated ``GET /api/v1/entries.json?count=1`` — the expected answer is ``401``.
   A ``200`` is reported as *exposed*: the store is readable by anything on the LAN.
2. authorised ``GET``, tried in the two forms Nightscout accepts, in this order:

   * ``?count=1&token=<token>`` — the query form,
   * ``Authorization: Basic base64("<token>:")`` — what ``http://<token>@host:port`` means
     and the form xDrip+ is pointed at.

   The reported ``auth_form`` names the form that actually answered ``200``, so the
   documentation can quote a form that was measured rather than assumed.

The token never appears in the result, the rendered text or the JSON output; the module
reads no clock and produces no timestamp, so two probes against the same stack produce
byte-identical output and the deterministic-report invariant is not weakened.

There is no mock mode. With no stack deployed the pytest integration test **skips
visibly** rather than passing; the CLI exits ``2`` (no stack) instead of ``0``.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from glucoseledger.config import ConfigError, SecretConfig, load_config

__all__ = [
    "BASIC_FORM",
    "ENTRIES_PATH",
    "EXPECTED_DENIED_STATUS",
    "QUERY_FORM",
    "AuthProbe",
    "Response",
    "main",
    "probe_auth",
    "render_text",
]

#: Nightscout's v1 entries endpoint. ``.json`` is the documented route suffix; the probe
#: asks for a single row so an exposed stack is detected without pulling a series.
ENTRIES_PATH = "/api/v1/entries.json"

EXPECTED_DENIED_STATUS = 401
OK_STATUS = 200

QUERY_FORM = "query-token"
BASIC_FORM = "basic-auth-token"

PROBE_TIMEOUT = 10.0

#: A probe needs one row at most; a server that ignores ``count`` cannot be allowed to
#: make the check read a year of data. Anything past this is truncated and unreported.
MAX_BODY_BYTES = 65536

#: Exit codes for the CLI, so a cron wrapper can tell "wrong" from "not there".
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NO_STACK = 2


@dataclass(frozen=True)
class Response:
    """One HTTP exchange, reduced to what the contract cares about."""

    status: int | None
    body: str = ""
    error: str | None = None


@dataclass(frozen=True)
class AuthProbe:
    """The outcome of both requests. ``problems`` is empty exactly when ``ok`` is true."""

    base_url: str
    unauth_status: int | None
    auth_status: int | None
    auth_form: str | None
    entries_seen: int | None
    ok: bool
    problems: tuple[str, ...] = field(default=())

    @property
    def reachable(self) -> bool:
        """False when nothing answered at all — i.e. no stack, not a failed proof."""
        return self.unauth_status is not None or self.auth_status is not None

    def as_json(self) -> dict[str, object]:
        """A fixed-key, timestamp-free payload, safe to commit or diff."""
        return {
            "auth_form": self.auth_form,
            "auth_status": self.auth_status,
            "base_url": self.base_url,
            "entries_path": ENTRIES_PATH,
            "entries_seen": self.entries_seen,
            "expected_unauth_status": EXPECTED_DENIED_STATUS,
            "ok": self.ok,
            "problems": list(self.problems),
            "unauth_status": self.unauth_status,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect.

    A redirect that lands on a 200 would otherwise be recorded as a successful
    authorised read, which is exactly the kind of quiet upgrade this proof exists to
    rule out. A 3xx is kept as the answer.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib hook
        return None


def _read_capped(stream) -> str:  # noqa: ANN001 - file-like object from urllib
    raw = stream.read(MAX_BODY_BYTES + 1)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw[:MAX_BODY_BYTES]


def _fetch(url: str, authorization: str | None, timeout: float) -> Response:
    """GET ``url`` and return its status, following no redirects and raising nothing."""
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "glucoseledger-authprobe"},
    )
    if authorization is not None:
        request.add_header("Authorization", authorization)

    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return Response(status=response.status, body=_read_capped(response))
    except urllib.error.HTTPError as exc:
        try:
            body = _read_capped(exc)
        except OSError:
            body = ""
        return Response(status=exc.code, body=body)
    except OSError as exc:  # URLError, refused connection, DNS failure, timeout
        reason = getattr(exc, "reason", None) or exc
        return Response(status=None, error=f"{type(exc).__name__}: {reason}")


def _basic_header(token: str) -> str:
    """The header ``http://<token>@host:port`` turns into: token as user, blank password."""
    encoded = base64.b64encode(f"{token}:".encode()).decode("ascii")
    return f"Basic {encoded}"


def _entries_seen(body: str) -> tuple[int | None, str | None]:
    """Count rows in an entries response; return a reason when it is not an array."""
    if not body.strip():
        return None, "authorised GET returned an empty body"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None, "authorised GET returned a body that is not JSON (a login page, not data)"
    if not isinstance(parsed, list):
        return None, "authorised GET returned JSON that is not an entries array"
    return len(parsed), None


def probe_auth(
    config: SecretConfig,
    *,
    timeout: float = PROBE_TIMEOUT,
    transport: Callable[[str, str | None, float], Response] | None = None,
) -> AuthProbe:
    """Probe ``config``'s stack and return the verdict. Never raises on a network error."""
    send = transport or _fetch
    base = config.base_url.rstrip("/")
    entries_url = f"{base}{ENTRIES_PATH}"
    token = config.read_token

    def redact(text: str) -> str:
        return text.replace(token, "***") if token else text

    problems: list[str] = []

    # 1. No credential of any kind. This request is the proof and must stay bare.
    unauth = send(f"{entries_url}?count=1", None, timeout)
    if unauth.status is None:
        problems.append(
            f"unauthenticated GET did not connect ({redact(unauth.error or 'unknown error')})"
        )
    elif unauth.status == OK_STATUS:
        problems.append(
            "EXPOSED: unauthenticated GET returned 200 — the series is readable without a "
            "token; AUTH_DEFAULT_ROLES must be denied"
        )
    elif unauth.status != EXPECTED_DENIED_STATUS:
        problems.append(
            f"unauthenticated GET returned {unauth.status}; expected {EXPECTED_DENIED_STATUS}"
        )

    # 2. The token, in the query form first, then the basic-auth form.
    attempts: list[tuple[str, Response]] = [
        (
            QUERY_FORM,
            send(
                f"{entries_url}?count=1&token={urllib.parse.quote(token, safe='')}", None, timeout
            ),
        )
    ]
    winner: tuple[str, Response] | None = None
    if attempts[0][1].status == OK_STATUS:
        winner = attempts[0]
    elif attempts[0][1].status is not None:
        # Only worth a second form when the first actually answered: a refused connection
        # or a dead host will fail the same way twice and just double the timeout.
        attempts.append((BASIC_FORM, send(entries_url, _basic_header(token), timeout)))
        if attempts[-1][1].status == OK_STATUS:
            winner = attempts[-1]

    auth_form: str | None = None
    auth_status: int | None = None
    entries_seen: int | None = None

    if winner is not None:
        auth_form, response = winner
        auth_status = response.status
        entries_seen, reason = _entries_seen(response.body)
        if reason is not None:
            problems.append(reason)
    else:
        auth_form = None
        auth_status = attempts[-1][1].status
        tried = " and ".join(f"{form} -> {resp.status}" for form, resp in attempts)
        if auth_status is None:
            last_error = redact(attempts[-1][1].error or "unknown error")
            problems.append(f"authorised GET did not connect ({last_error})")
        else:
            problems.append(f"authorised GET was not accepted ({tried}); expected {OK_STATUS}")

    return AuthProbe(
        base_url=base,
        unauth_status=unauth.status,
        auth_status=auth_status,
        auth_form=auth_form,
        entries_seen=entries_seen,
        ok=not problems,
        problems=tuple(problems),
    )


def render_text(probe: AuthProbe) -> str:
    """Deterministic human-readable proof; contains no credential and no timestamp."""
    unauth = "no answer" if probe.unauth_status is None else str(probe.unauth_status)
    authorised = "no answer" if probe.auth_status is None else str(probe.auth_status)
    form = probe.auth_form or "none accepted"
    lines = [
        f"auth proof: {probe.base_url}{ENTRIES_PATH}",
        f"  unauthenticated GET -> {unauth} (expected {EXPECTED_DENIED_STATUS})",
        f"  authorised GET      -> {authorised} ({form})",
    ]
    if probe.entries_seen is not None:
        lines.append(f"  entries returned    -> {probe.entries_seen}")
    lines.append(f"  result: {'PASS' if probe.ok else 'FAIL'}")
    lines.extend(f"  FAIL: {problem}" for problem in probe.problems)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``python -m glucoseledger.authprobe``: 0 pass, 1 wrong, 2 no stack/config."""
    parser = argparse.ArgumentParser(
        prog="glucoseledger.authprobe",
        description=(
            "Prove a deployed Nightscout answers 401 to an anonymous read and 200 to a "
            "token read. Exit 0 pass, 1 auth failure, 2 no stack reachable."
        ),
    )
    parser.add_argument(
        "--secret-env",
        type=Path,
        default=None,
        help="secrets file to load (default: $GLUCOSE_SECRET_ENV, then ~/glucose/secret.env)",
    )
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT, help="seconds per request")
    parser.add_argument("--json", action="store_true", help="print the JSON payload instead")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.secret_env)
    except ConfigError as exc:
        print(f"auth proof: NO STACK — {exc}", file=sys.stderr)
        return EXIT_NO_STACK

    probe = probe_auth(config, timeout=args.timeout)
    if args.json:
        print(json.dumps(probe.as_json(), indent=2, sort_keys=True))
    else:
        print(render_text(probe))

    if probe.ok:
        return EXIT_PASS
    if not probe.reachable:
        return EXIT_NO_STACK
    return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    sys.exit(main())
