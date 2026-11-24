# M0 read-path spike — throwaway Nightscout staging stack

This directory holds the disposable Nightscout instance used to answer the M0 question:
**can xDrip+ on the phone push readings to a server in this house?** It is not the
production stack (`deploy/`, M1) and it is not a substitute for the sensor (H1/H2).

Everything here is deliberately unhardened. `AUTH_DEFAULT_ROLES` is left at the image
default, so anonymous reads are allowed while the spike runs — that is the state M1-T3
is meant to close. The only protections kept even here are LAN-only binding, no
published database port, and a generated secret that never enters the repo.

## Files

| File | Role |
|---|---|
| `docker-compose.spike.yml` | the stack: `nightscout/cgm-remote-monitor:15.0.9` + `mongo:4.4`, both pinned |
| `../scripts/ns_spike.sh` | render → up → verify → down, and `url`/`status` while it runs |

The renderer writes everything to `/tmp/ns-spike` (dir `0700`): the compose copy, a
0600 `secret.env` with 32 random bytes of `API_SECRET`, a 0600 `spike.env` for compose,
and `read.token` (the SHA1 Nightscout access token). `down` removes the containers, the
volumes and the directory — the spike leaves no key material behind.

## Procedure

```bash
bash scripts/ns_spike.sh up      # render, start, wait for the API, POST + read back a probe
bash scripts/ns_spike.sh url     # the xDrip+ base URL: http://<token>@<lan-ip>:1337/api/v1
bash scripts/ns_spike.sh status  # compose ps + how many entries the API returns
bash scripts/ns_spike.sh down    # stop, delete volumes, delete /tmp/ns-spike
```

`up` exits non-zero if the API never answers, if any container exits first (it prints
that container's last log lines), or if the probe reading does not come back from
`GET /api/v1/entries.json`.

## Measured on debian2, 2026-11-24 18:57 UTC

- Stack up: `mongo` + `nightscout` both `running`; `/` answered HTTP 200 in **3 s**.
- Authorised POST of one `sgv` 137 reading (`device: spike-probe`) → HTTP 200, and the
  same reading came back from `GET /api/v1/entries.json`; Mongo held 1 matching document
  (`db.entries.countDocuments({device:"spike-probe"})` → `1`). The write path is real,
  not a buffer.
- Anonymous `GET /api/v1/entries.json` → **200** (unhardened default — the number M1-T3
  must turn into 401).
- Bind: `ss -ltn` shows `192.168.0.10:1337` only. Not `0.0.0.0`. No port forward was
  added; the Mongo port is not published at all.
- Footprint: nightscout 62 MiB RSS + mongo 80 MiB RSS ≈ **142 MiB** of a 1 GiB target.
- Images: `nightscout/cgm-remote-monitor:15.0.9` 455 MB, `mongo:4.4` 594 MB.

## Finding that changes M1: this CPU has no AVX

`mongo:7.0` never boots on this host. It is not a config problem:

- `docker inspect` on the dead container: `exit=132` (SIGILL), mongo log line:
  *"MongoDB 5.0+ requires a CPU with AVX support, and your current system does not
  appear to have that!"*
- `/proc/cpuinfo` on debian2: **Intel Pentium Gold G5420** — a Pentium part with AVX
  fused off. No `avx`/`avx2` flag is exposed to the container.
- Consequence: every MongoDB 5.0+ image dies the same way. `mongo:4.4` is the last major
  that runs here, and Nightscout 15.0.9 connects to it and writes without complaint.

So the stack is pinned to `mongo:4.4` here, and `deploy/` (M1-T1) must do the same or the
production stack will crash-loop in exactly this way. If the data directory is ever moved
to a host with AVX, take a `mongodump` first — the 4.4 → newer jump is not an in-place
upgrade (PRD §Risk: never point a newer major at an old data dir).

## What this is not

It proves the *server* side accepts an authorised write and serves it back. It says
nothing about whether a sensor can reach it: that needs H1 (a Stelo pack), H2 (xDrip+
pairing) and M0-T2 (the 24 h coverage probe), which measures the real upload cadence and
gaps. A green run here is a precondition, not the verdict.
