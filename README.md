# GlucoseLedger

Self-hosted continuous-glucose pipeline: Nightscout as the store on hardware we own,
xDrip+ on the phone as the sensor reader and uploader, a nightly SQLite mirror, and a
weekly markdown report delivered to Telegram and the Obsidian vault.

**No vendor cloud is in the data path.** The sensor vendor cannot see the readings after
they leave the app, because the app in use is not the vendor's (see `PRD.md` §CGM Data
Path Decision Record).

## Read order

1. `PRD.md` — requirements, data contracts, invariants, risks
2. `BUILD_PLAN.md` — the executable order of work, with `[HUMAN]` gates marked
3. `.task-progress.json` — the live tracker (the only tracker; `.hermes/` is deliberately
   unused because Hermes blocks unattended writes there)

## Status

Seeded 2026-11-24. **M0-T1 done:** the throwaway Nightscout staging stack renders,
verifies (authorised write + read-back) and tears itself down — see `spike/README.md`,
which also records that MongoDB 5.0+ cannot run on this host (no AVX). Still blocked on
H1 (buy one Stelo sensor pack) for the read-path gate; M1–M5 are machine-buildable
without hardware.

## Layout

```
deploy/     docker-compose stack for Nightscout + MongoDB (no secrets)
src/        the glucoseledger package (ETL, metrics, report)
tests/      pytest suite + invariant tests
fixtures/   checked-in API payloads and CSV samples
scripts/    backup / restore-drill / cron wrappers
spike/      the M0 read-path verdict, its evidence, and the throwaway staging stack
```

Deployment (not in this repo): `~/glucose/` for stack + data + secrets, `~/backups/glucose/`
for dumps.

## Deployment secrets

`~/glucose/secret.env` (mode `0600`, never in the repo) is read by
`glucoseledger.config.load_config()`. Keys: `API_SECRET` (32+ characters, no whitespace),
`GLUCOSE_READ_TOKEN` (the 16 hex character token from the Nightscout admin UI),
`GLUCOSE_BIND_IP` (LAN IPv4 — `0.0.0.0` is refused), plus optional `GLUCOSE_HOST`
(defaults to the bind address), `GLUCOSE_PORT` (`1337`) and `DISPLAY_UNITS` (`mg/dl`).
A missing file, a group/world-readable mode, or a malformed value fails with an actionable
message; errors and `repr()` never contain a credential.

## Not a medical device

Descriptive statistics only. No diagnosis, no dosing, no safety alerting. See the
anti-goals in `PRD.md`.
