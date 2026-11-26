# Deploying the Nightscout stack

Two containers on debian2 (`hostname: hermes`): `nightscout/cgm-remote-monitor:15.0.9`
and `mongo:4.4`. The compose file holds **no secret**; every value that could leak is a
required variable supplied at run time from `~/glucose/secret.env` (mode `0600`).

This file documents the **exposure model** (M1-T3, PRD F2). If a step below would publish
the stack anywhere but the LAN, the step is wrong.

## Exposure model

| Listener | Address | Reachable from |
|---|---|---|
| Nightscout UI + API | `${GLUCOSE_BIND_IP}:${GLUCOSE_PORT}` (one LAN IPv4, default port 1337) | the LAN only |
| MongoDB | compose network only — no `ports:` entry | the Nightscout container only |

What is deliberately absent, on this and every MVP deployment:

- **No port forward.** Never add one to the router for this stack. The compose file takes
  a required host address for exactly this reason: `${GLUCOSE_BIND_IP}` cannot be
  `0.0.0.0`, and the loader refuses that value outright.
- **No UPnP / NAT-PMP.** Docker does not create mappings by itself, and nothing here asks
  it to. Do not install a helper that does.
- **No reverse proxy and no public DNS name.** There is no TLS terminator in front of the
  stack, so `INSECURE_USE_HTTP=true` is honest rather than a downgrade being hidden.
- **No vendor cloud.** The data path is sensor → phone → this server. Nightscout is not
  configured to forward anywhere.

Why the strictness: a CGM series is a continuous health signal that also reveals meal
times, sleep and travel. It cannot be rotated like a password. LAN-only is the default and
the fallback, not a stepping stone.

Remote access is a separate, later task (M7-T3, Tailscale). It does **not** change this
file's rule: the phone joins an overlay network; the router still forwards nothing.

## Required configuration

`~/glucose/secret.env`, owned by the deploying user, mode `0600`:

```
API_SECRET=<32+ random bytes, hex — never printed, never committed>
GLUCOSE_READ_TOKEN=<16 hex character token from the Nightscout admin UI>
GLUCOSE_BIND_IP=<this host's LAN IPv4 address>
# optional, with the defaults shown:
GLUCOSE_HOST=<address the API client dials; defaults to GLUCOSE_BIND_IP>
GLUCOSE_PORT=1337
DISPLAY_UNITS=mg/dl
```

`glucoseledger.config.load_config()` is the only reader: it refuses a missing file, a
group/world-readable mode, a short secret, an all-interfaces bind address, or a
`DISPLAY_UNITS` outside `mg/dl | mmol`, and its error text names the key and the fix
without echoing a value. It has no default secret — generate one before the first start.

## Hardening environment

Set in the compose file, not overridable by accident:

- `AUTH_DEFAULT_ROLES=denied` — an unauthenticated caller gets no role, so `/api/v1/entries`
  answers 401 rather than a glucose series. `readable`, `careportal` and `admin` are all
  wrong here.
- `TIME_FORMAT=24` — quoted, because compose needs a string and `24` would parse as an int.
- `DISPLAY_UNITS=${DISPLAY_UNITS:-mg/dl}` — rendering only. Stored `sgv` stays mg/dL as
  delivered and every metric is computed in mg/dL; switching the display unit must never
  convert a stored value.

## Validate without starting anything

```sh
docker compose --env-file ~/glucose/secret.env -f deploy/docker-compose.yml config
```

A missing `API_SECRET` or `GLUCOSE_BIND_IP` fails this render loudly. It is a static
check — it does not prove the running bind.

## Prove the bind is LAN-only

```sh
docker compose --env-file ~/glucose/secret.env -f deploy/docker-compose.yml ps
ss -ltnp 'sport = :1337'   # host address must be the LAN IPv4, never 0.0.0.0 or [::]
```

Then from a second device on the LAN:

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://${GLUCOSE_BIND_IP}:${GLUCOSE_PORT}/api/v1/entries
```

`401` is the expected and passing answer; a JSON array means `AUTH_DEFAULT_ROLES` is
wrong and the stack comes down before anything else is investigated (M1-T4 automates
this check). From outside the LAN the same request must not resolve or connect at all.

The authorised form is `http://<token>@${GLUCOSE_BIND_IP}:${GLUCOSE_PORT}/api/v1`, which
is what xDrip+ is pointed at. The token is the 16 hex character prefix of the SHA-1 of
`API_SECRET`, so rotating the secret invalidates every token — that is the intended
revocation path and the reason the secret never leaves the `0600` file.

## Mongo is pinned, on purpose

`mongo:4.4` is the newest major that runs on this host: the debian2 CPU has no AVX and
MongoDB 5.0+ dies with SIGILL (`exit=132`), measured 2026-11-24 (see `../spike/README.md`).
Two consequences to keep in mind:

- Never point a newer Mongo major at the existing data directory. Take a dump first
  (M2-T1), restore into a fresh volume, then upgrade.
- Never replace the tag with `latest`. Both images are pinned by tag and a test fails the
  build if that stops being true.

## Operating notes

- `restart: unless-stopped` on both services; a host reboot brings the stack back.
- The Mongo data lives in the named volume `glucose-ledger_mongo_data`; the fixed project
  name keeps that identity regardless of the directory the file is run from.
- Plain HTTP on the LAN is the MVP's only transport. Do not put this stack on a public
  host, a shared VLAN, or a coffee-shop network.
