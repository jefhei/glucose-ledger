"""M1-T4 — the access-control proof: anonymous read 401, token read 200.

What is pinned here:

* the probe sends a genuinely uncredentialed request (no token, no header) and fails the
  build when that request returns 200 — an open stack is a defect, not a passing result;
* a 200 that is not an entries array (a login page, say) is not an authorised read, and a
  redirect is never followed to a 200;
* both authorised URL forms are supported and the one that answered is reported by name;
* the token never reaches the rendered text, the JSON payload or a failure message;
* probing is read-only (GET only) and deterministic (same stack, same bytes).

The stub below stands in for Nightscout's answer codes so the contract is testable with
no containers. The last test is the real thing: it runs against the deployed stack and
**skips visibly** when none is deployed, because an auth proof that passes with no stack
is worse than no proof at all.
"""

import base64
import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from glucoseledger.authprobe import (
    BASIC_FORM,
    ENTRIES_PATH,
    EXPECTED_DENIED_STATUS,
    QUERY_FORM,
    Response,
    main,
    probe_auth,
    render_text,
)
from glucoseledger.config import ConfigError, SecretConfig, load_config

# Constructed rather than written out, so no token-shaped literal sits in a tracked file.
TOKEN = "5a" * 8

#: 32+ characters, deliberately not hex: never mistakable for a real secret by a grep.
DUMMY_SECRET = "dummy-secret-for-auth-probe-tests-not-real"

ENTRIES = [
    {"_id": "6" * 24, "sgv": 104, "date": 1764300000000, "type": "sgv", "direction": "Flat"}
]
DENIED_BODY = json.dumps({"status": EXPECTED_DENIED_STATUS, "message": "Unauthorized"})


class StubStack:
    """A minimal stand-in for the stack's auth behaviour, on a loopback port."""

    def __init__(self, mode: str):
        self.mode = mode
        self.requests: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> "StubStack":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    # --- behaviour ---------------------------------------------------------

    def answer(self, supplied_token: str | None, basic_user: str | None):
        mode = self.mode
        if mode == "open":
            return 200, json.dumps(ENTRIES)
        if mode == "basic-only":
            return (200, json.dumps(ENTRIES)) if basic_user == TOKEN else (401, DENIED_BODY)
        if mode == "closed":
            return 401, DENIED_BODY
        if mode == "forbidden":
            return (200, json.dumps(ENTRIES)) if supplied_token == TOKEN else (403, DENIED_BODY)
        if mode == "html":
            if supplied_token == TOKEN:
                return 200, "<html><body>login</body></html>"
            return 401, DENIED_BODY
        if mode == "redirect":
            return 302, ""
        if mode == "denied":
            return (200, json.dumps(ENTRIES)) if supplied_token == TOKEN else (401, DENIED_BODY)
        raise AssertionError(f"unknown stub mode {mode!r}")

    def _handler(self):
        stack = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - http.server hook
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                header = self.headers.get("Authorization", "")
                basic_user = None
                if header.startswith("Basic "):
                    decoded = base64.b64decode(header[6:]).decode("ascii")
                    basic_user = decoded.split(":", 1)[0]

                stack.requests.append(
                    {
                        "method": "GET",
                        "path": parsed.path,
                        "query": query,
                        "authorization": header,
                    }
                )

                status, body = stack.answer(query.get("token", [None])[0], basic_user)
                payload = body.encode("utf-8")
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", f"{ENTRIES_PATH}?count=1&token={TOKEN}")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):  # noqa: N802 - http.server hook
                stack.requests.append({"method": "POST", "path": self.path})
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A002 - http.server hook signature
                pass

        return Handler


@pytest.fixture
def make_stack():
    stacks: list[StubStack] = []

    def factory(mode: str) -> StubStack:
        stack = StubStack(mode).start()
        stacks.append(stack)
        return stack

    yield factory
    for stack in stacks:
        stack.close()


def config_for(stack: StubStack, *, token: str = TOKEN) -> SecretConfig:
    return SecretConfig(
        api_secret=DUMMY_SECRET,
        read_token=token,
        bind_ip="127.0.0.1",
        host="127.0.0.1",
        port=stack.port,
        units="mg/dl",
        source_path=Path("/nonexistent/secret.env"),
    )


# --- the passing contract ----------------------------------------------------


def test_denied_stack_proves_401_anonymous_and_200_with_the_token(make_stack):
    probe = probe_auth(config_for(make_stack("denied")))

    assert probe.ok, render_text(probe)
    assert probe.unauth_status == EXPECTED_DENIED_STATUS
    assert probe.auth_status == 200
    assert probe.auth_form == QUERY_FORM
    assert probe.entries_seen == len(ENTRIES)
    assert probe.problems == ()


def test_the_anonymous_request_really_carries_no_credential(make_stack):
    stack = make_stack("denied")
    probe_auth(config_for(stack))

    anonymous = stack.requests[0]
    assert anonymous["query"].get("token") is None
    assert anonymous["authorization"] == ""
    assert anonymous["path"] == ENTRIES_PATH


def test_basic_auth_form_is_accepted_when_the_query_form_is_not(make_stack):
    probe = probe_auth(config_for(make_stack("basic-only")))

    assert probe.ok, render_text(probe)
    assert probe.auth_form == BASIC_FORM
    assert probe.unauth_status == EXPECTED_DENIED_STATUS


def test_probe_is_read_only(make_stack):
    stack = make_stack("denied")
    probe_auth(config_for(stack))
    assert stack.requests, "the probe must actually make requests"
    assert {request["method"] for request in stack.requests} == {"GET"}


# --- the failures that must not pass ----------------------------------------


def test_open_stack_is_reported_as_exposed(make_stack):
    probe = probe_auth(config_for(make_stack("open")))

    assert not probe.ok
    assert probe.unauth_status == 200
    assert any("EXPOSED" in problem for problem in probe.problems)
    assert any("AUTH_DEFAULT_ROLES" in problem for problem in probe.problems)
    assert "FAIL" in render_text(probe)


def test_a_rejected_token_is_not_a_pass(make_stack):
    probe = probe_auth(config_for(make_stack("closed")))

    assert not probe.ok
    assert probe.unauth_status == EXPECTED_DENIED_STATUS
    assert probe.auth_status == EXPECTED_DENIED_STATUS
    assert probe.auth_form is None
    assert any("not accepted" in problem for problem in probe.problems)


def test_a_wrong_token_never_authenticates(make_stack):
    stack = make_stack("denied")
    probe = probe_auth(config_for(stack, token="be" * 8))

    assert not probe.ok
    assert probe.auth_form is None


def test_non_401_denial_is_flagged_against_the_documented_contract(make_stack):
    probe = probe_auth(config_for(make_stack("forbidden")))

    assert not probe.ok
    assert probe.unauth_status == 403
    assert any("expected 401" in problem for problem in probe.problems)
    # the token itself is fine; the drift is in the anonymous answer
    assert probe.auth_form == QUERY_FORM


def test_200_with_a_login_page_is_not_an_authorised_read(make_stack):
    probe = probe_auth(config_for(make_stack("html")))

    assert not probe.ok
    assert probe.auth_status == 200
    assert probe.entries_seen is None
    assert any("not JSON" in problem for problem in probe.problems)


def test_redirect_is_not_followed_into_a_200(make_stack):
    stack = make_stack("redirect")
    probe = probe_auth(config_for(stack))

    assert not probe.ok
    assert probe.unauth_status == 302
    assert probe.auth_status == 302
    # exactly one request per attempt: the Location was ignored
    assert len(stack.requests) == 3
    assert all(request["path"] == ENTRIES_PATH for request in stack.requests)


def test_unreachable_stack_is_a_no_stack_result_not_a_pass():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = SecretConfig(
        api_secret=DUMMY_SECRET,
        read_token=TOKEN,
        bind_ip="127.0.0.1",
        host="127.0.0.1",
        port=port,
        units="mg/dl",
        source_path=Path("/nonexistent/secret.env"),
    )
    probe = probe_auth(config, timeout=1.0)

    assert not probe.ok
    assert not probe.reachable
    assert any("did not connect" in problem for problem in probe.problems)


def test_a_dead_host_is_not_retried_in_the_second_url_form():
    """One connect attempt plus one token attempt, not four: the retry must be cheap."""
    calls: list[tuple[str, str | None]] = []

    def transport(url, authorization, timeout):
        calls.append((url, authorization))
        return Response(status=None, error="ConnectionRefusedError: refused")

    config = SecretConfig(
        api_secret=DUMMY_SECRET,
        read_token=TOKEN,
        bind_ip="127.0.0.1",
        host="127.0.0.1",
        port=1337,
        units="mg/dl",
        source_path=Path("/nonexistent/secret.env"),
    )
    probe = probe_auth(config, transport=transport)

    assert len(calls) == 2
    assert not probe.reachable
    assert not probe.ok


# --- no credential in any output, and nothing that varies run to run --------


def test_rendered_output_and_payload_never_carry_the_token(make_stack):
    probe = probe_auth(config_for(make_stack("closed")))
    rendered = "\n".join(
        [render_text(probe), repr(probe), json.dumps(probe.as_json()), str(probe.problems)]
    )
    assert TOKEN not in rendered
    assert DUMMY_SECRET not in rendered


def test_probe_and_render_are_deterministic(make_stack):
    stack = make_stack("denied")
    config = config_for(stack)

    first = probe_auth(config)
    second = probe_auth(config)

    assert first == second
    assert render_text(first) == render_text(second)
    assert json.dumps(first.as_json(), sort_keys=True) == json.dumps(
        second.as_json(), sort_keys=True
    )
    # no timestamp-shaped value in the payload
    assert not any("time" in key or "at" == key[-2:] for key in first.as_json())


def test_probe_source_reads_no_clock_and_no_randomness():
    """The PRD determinism invariant: a proof must not vary with wall-clock time."""
    import glucoseledger.authprobe as authprobe

    source = Path(authprobe.__file__).read_text(encoding="utf-8")
    assert "import time" not in source
    assert "datetime" not in source
    assert "random" not in source


# --- the runbook documents what was built -----------------------------------


def test_readme_documents_both_url_forms_and_the_exit_codes():
    readme = (Path(__file__).resolve().parents[1] / "deploy" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "make authcheck" in readme
    assert "http://<token>@" in readme
    assert "entries.json?token=<token>" in readme
    for code in ("`0`", "`1`", "`2`"):
        assert code in readme
    assert "skips visibly" in readme


# --- CLI exit codes ---------------------------------------------------------


def write_secret(tmp_path: Path, port: int, token: str = TOKEN) -> Path:
    path = tmp_path / "secret.env"
    path.write_text(
        f"API_SECRET={DUMMY_SECRET}\n"
        f"GLUCOSE_READ_TOKEN={token}\n"
        "GLUCOSE_BIND_IP=127.0.0.1\n"
        "GLUCOSE_HOST=127.0.0.1\n"
        f"GLUCOSE_PORT={port}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_cli_passes_prints_json_and_hides_the_token(make_stack, tmp_path, capsys):
    stack = make_stack("denied")
    secret = write_secret(tmp_path, stack.port)

    assert main(["--secret-env", str(secret), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out
    assert TOKEN not in out


def test_cli_exits_1_when_the_store_is_exposed(make_stack, tmp_path, capsys):
    stack = make_stack("open")
    secret = write_secret(tmp_path, stack.port)

    assert main(["--secret-env", str(secret)]) == 1
    assert "EXPOSED" in capsys.readouterr().out


def test_cli_exits_2_when_no_stack_answers(tmp_path, capsys):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    secret = write_secret(tmp_path, port)

    assert main(["--secret-env", str(secret), "--timeout", "1"]) == 2


def test_cli_exits_2_when_the_secrets_file_is_missing(tmp_path):
    assert main(["--secret-env", str(tmp_path / "absent.env")]) == 2


# --- the real proof: against the deployed stack, or a visible skip ----------


def _live_config() -> SecretConfig:
    """The deployed stack's settings, or a visible skip when there is no deployment."""
    try:
        return load_config()
    except ConfigError as exc:
        pytest.skip(f"skipped: no deployed stack ({exc})")
        raise AssertionError("pytest.skip must raise") from exc  # pragma: no cover


def test_deployed_stack_answers_401_anonymously_and_200_with_the_token():
    """PRD `[auto]`: asserted on the deployed instance, not on a mock."""
    probe = probe_auth(_live_config())
    if not probe.reachable:
        pytest.skip(f"skipped: no stack reachable at {probe.base_url}")

    assert probe.ok, render_text(probe)
