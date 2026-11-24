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

Seeded 2026-11-24. **No tasks executed.** Blocked on H1 (buy one Stelo sensor pack) for
the read-path gate; M1–M5 are machine-buildable without hardware.

## Layout

```
deploy/     docker-compose stack for Nightscout + MongoDB (no secrets)
src/        the glucoseledger package (ETL, metrics, report)
tests/      pytest suite + invariant tests
fixtures/   checked-in API payloads and CSV samples
scripts/    backup / restore-drill / cron wrappers
spike/      the M0 read-path verdict and its evidence
```

Deployment (not in this repo): `~/glucose/` for stack + data + secrets, `~/backups/glucose/`
for dumps.

## Not a medical device

Descriptive statistics only. No diagnosis, no dosing, no safety alerting. See the
anti-goals in `PRD.md`.
