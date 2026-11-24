# Build Plan: GlucoseLedger

Self-hosted CGM pipeline — Nightscout store on debian2, xDrip+ reader on the phone,
nightly SQLite mirror, weekly markdown report. See `PRD.md` for the full requirements;
this file is the executable order of work. Where the two disagree, the PRD wins and
this file gets fixed.

- **Stack:** Docker + docker compose (Nightscout 15.x, MongoDB pinned) · Python 3.13
  (stdlib `sqlite3` + `pandas`/`numpy`) · pytest · ruff · Make
- **Runtime:** two LAN-bound containers on debian2 (`hostname: hermes`); ETL and
  reporting run as Hermes cron jobs; Telegram is the only outbound call
- **Deployment root:** `~/glucose/` (stack, data, secrets — **outside** the repo)
- **Backups:** `~/backups/glucose/`
- **Progress tracker:** `.task-progress.json` — the ONLY tracker. **Do not keep tracker
  state under `.hermes/`**: Hermes guards that directory against unattended writes, so a
  cron run cannot save its own progress there.
- **Commit convention:** `feat: <task-id> — <description>`, then a separate
  `chore: <task-id> — tracker update`. Never `git add -A` on the first commit — it sweeps
  the tracker in and the `chore:` commit comes up empty.

## Two Acceptance Regimes

Every exit criterion belongs to exactly one column. A human-verifiable criterion is never
allowed to masquerade as a build gate.

- **Machine gate** — `make verify` green (ruff + pytest), the `[auto]` invariants from
  PRD §Closed-World Invariants hold, and the task's own fixtures assert. Runs per task.
- **Human review** — does the report read honestly; does the stack survive a real week;
  does the number match what a human sees in the Nightscout UI. Runs at milestone
  boundaries only.

## Human-Gated Tasks

Tasks prefixed **`[HUMAN]`** need a purchase, a phone, or a judgement call. **The build
cron must never attempt them.** When the next dependency resolves to a `[HUMAN]` task, the
job reports `blocked on <task-id>` once, sets nothing else, and stops — it does not
improvise around it, and it does not "simulate" the hardware.

| ID | Human action | Unblocks |
|---|---|---|
| H1 | Buy **one** Stelo sensor pack | M0-T2 |
| H2 | Install xDrip+, pair the sensor (hardware option G6/G7, 4-digit transmitter ID) | M0-T3 |
| H3 | Approve the M0 verdict (proceed with subscription, or pivot to import-only) | M1-T1 |
| H4 | Read one real weekly report and sign off its wording | M5 close |
| H5 | Week-4 anxiety check: does the cadence help or harm? | M7 close |

---

## M0 — Read-Path Gate (human-led, days 1–15)

The one thing that cannot be engineered around: Stelo has no real-time sharing, so the
live path is xDrip+ pairing to the sensor and posting to Nightscout. Prove it on one
sensor before a subscription is bought.

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] H1 | **`[HUMAN]`** Buy one Stelo pack (not a subscription) | — | — |
| [ ] M0-T1 | Throwaway staging stack: compose file under `/tmp/ns-spike/`, no hardening, LAN bound, token printed | — | 0.5d |
| [ ] H2 | **`[HUMAN]`** Pair xDrip+ to the sensor; confirm readings 15–20 min after warmup | M0-T1, H1 | — |
| [ ] M0-T2 | Coverage probe: poll `/api/v1/entries` hourly for 24 h, write `spike/coverage.md` (readings found, cadence, gaps) | H2 | 0.5d |
| [ ] M0-T3 | **Verdict deliverable:** `spike/VERDICT.md` — PASS if 24 h coverage ≥ 90%, else the evidence and the pivot decision. *"This path cannot work, here is the evidence" is a successful outcome* | M0-T2 | 0.25d |
| [ ] H3 | **`[HUMAN]`** Approve the verdict | M0-T3 | — |

**Machine gate:** spike scripts run; `spike/coverage.md` exists with real measured numbers
(no placeholders — if there is no sensor yet, the task is blocked, not faked).
**Human review:** the verdict is believed by a person who watched the app.
**Exit:** a recorded verdict. A FAIL verdict pivots the plan to import-only and makes M6
the critical path — it does not stall the project.

## M1 — Stack & Access Control (days 1–2, runs in parallel with M0)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M1-T1 | `deploy/docker-compose.yml`: `nightscout/cgm-remote-monitor` 15.x + Mongo, **both pinned by tag**, named volumes, `restart: unless-stopped`, LAN bind | — | 0.5d |
| [ ] M1-T2 | Config loader: read `~/glucose/secret.env` (0600) — `API_SECRET`, read token, host, units; fail loudly if missing or group/world-readable | M1-T1 | 0.5d |
| [ ] M1-T3 | Hardening: `AUTH_DEFAULT_ROLES=denied`, `DISPLAY_UNITS=mg/dl`, `TIME_FORMAT=24`, no port forward documented in `deploy/README.md` | M1-T1 | 0.25d |
| [ ] M1-T4 | Auth proof test: unauthenticated `GET /api/v1/entries` → 401, authorised → 200. Skips (visibly) when no stack is deployed | M1-T2 | 0.5d |
| [ ] M1-T5 | `make stack-up` / `make stack-down` / `make stack-status`; verify data and auth survive a down/up cycle | M1-T1 | 0.25d |
| [ ] M1-T6 | Secret-leak guard test: grep tracked files for the token/JWT patterns, fail if found | M1-T2 | 0.25d |

**Machine gate:** compose config validates (`docker compose config`); auth test passes
against a deployed stack or reports `skipped: no stack`; secret grep clean.
**Human review:** n/a.

## M2 — Durability (days 3–4)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M2-T1 | `scripts/backup.sh`: `mongodump` → `~/backups/glucose/<YYYY-MM-DD>/`, 14-day rotation, non-zero exit on failure | M1-T1 | 0.5d |
| [ ] M2-T2 | `scripts/restore_drill.sh`: restore newest dump into a **scratch container**, reconcile entry count against live, print PASS/FAIL with both counts | M2-T1 | 0.5d |
| [ ] M2-T3 | Hermes cron wrapper for the backup; failure posts to Telegram (existing gateway) | M2-T1 | 0.25d |
| [ ] M2-T4 | Record measured drill runtime in `deploy/README.md` (target < 15 min) | M2-T2 | 0.25d |

**Machine gate:** backup produces a non-empty artifact; drill script runs end-to-end and
its reconciliation asserts rather than prints.
**Human review:** one drill executed for real by a person, counts agreed.

## M3 — ETL & Mirror (days 5–7)

Nightscout JSON fixtures are checked in, so this milestone does **not** wait for a sensor.

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M3-T1 | `fixtures/`: 4 documented API payloads — clean day, 6-h gap, warmup day, compression dip — plus a Lingo-style CSV | — | 0.5d |
| [ ] M3-T2 | SQLite schema + migrations (`entries_raw`, `treatments_raw`, `imports_raw`, `quality_flags`, `metrics_daily`, `metrics_windows`) | M3-T1 | 0.5d |
| [ ] M3-T3 | Nightscout client: paginate `/api/v1/entries` + `/api/v1/treatments` from a `_id` watermark; retries with backoff; never writes on parse failure | M3-T2 | 1d |
| [ ] M3-T4 | `INSERT OR IGNORE` on `_id`; idempotency test runs the ETL twice and asserts row counts unchanged | M3-T3 | 0.5d |
| [ ] M3-T5 | CSV importer → `imports_raw` only, with `source_file` provenance; test asserts live metrics are unaffected | M3-T2 | 0.5d |
| [ ] M3-T6 | CLI: `glucoseledger sync`, `glucoseledger status` (counts, last watermark, gaps) | M3-T3 | 0.5d |

**Machine gate:** ETL deterministic on fixtures; double-run produces no duplicates; importer
cannot reach a live metric.
**Human review:** `status` output is readable without the source.

## M4 — Metrics & Quality Flags (days 8–10)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M4-T1 | `metrics_daily`: mean, TIR/TBR/TAR, CV%, GMI, coverage %, gap count | M3-T2 | 1d |
| [ ] M4-T2 | `quality_flags`: warmup window, sensor-change seam, gap > 30 min, compression-suspect rule (night + sharp dip + fast recovery) | M4-T1 | 1d |
| [ ] M4-T3 | Min-data gate: `coverage_pct < 70` or `n < min_n` → `insufficient_data: true`, no number emitted | M4-T1 | 0.25d |
| [ ] M4-T4 | Golden fixtures: pin expected values for all four days, including the flagged ones | M4-T2 | 0.5d |
| [ ] M4-T5 | Determinism test: same input twice → byte-identical JSON, with a code-version stamp | M4-T1 | 0.25d |
| [ ] M4-T6 | Postprandial helper: 2-h iAUC + peak delta around a treatment, baseline defined explicitly (mean of the 15 min before the meal) | M4-T1, M3-T5 | 1d |

**Machine gate:** golden values match; determinism holds; flagged days are excluded from
the statistics they would distort **and still appear in the report's flag list** (never
deleted).
**Human review:** one hand-checked day — a human confirms the computed TIR matches the
Nightscout UI for that day.

## M5 — Report & Delivery (days 11–13)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M5-T1 | `report.py`: render markdown from the report contract, coverage **first**, then metrics, then flags | M4-T4 | 1d |
| [ ] M5-T2 | Versioned caveat block, rendered from the output (not the README) | M5-T1 | 0.25d |
| [ ] M5-T3 | Hermes cron: nightly `sync`, weekly report → Telegram + vault note | M5-T1, M3-T6 | 0.5d |
| [ ] M5-T4 | Wording test: banned-phrase assertion over the rendered report (no "you should", no diagnosis, no good/bad labels) | M5-T1 | 0.25d |
| [ ] M5-T5 | Exercise the `insufficient_data` path end-to-end with a fixture, and prove it ships as text rather than a silent skip | M5-T1 | 0.25d |
| [ ] H4 | **`[HUMAN]`** Read one real report; sign off wording | M5-T3 | — |

**Machine gate:** report renders from fixtures; wording test green; both branches
(sufficient / insufficient) produce output.
**Human review:** the wording sign-off (H4), and a plain question: would you act on this or
ignore it?

## M6 — Import-Only Path (post-MVP; **critical path** if M0 verdicts FAIL)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M6-T1 | Lingo/Clarity CSV normaliser into the `entries_raw`-compatible shape, tagged `source=import` | M3-T5 | 1d |
| [ ] M6-T2 | Import-scoped metrics view that never mixes with live data | M6-T1 | 0.5d |
| [ ] M6-T3 | Report variant for daily-cadence data (no TIR claims on 5-minute exports it cannot support) | M6-T2 | 1d |

## M7 — Experiments & Reach (post-MVP)

| Task | Description | Depends On | Est. |
|---|---|---|---|
| [ ] M7-T1 | N-of-1 experiment runner: pre-registered config, fixed N, washout, pre-declared metric | M4-T6 | 1.5d |
| [ ] M7-T2 | Result output with a sample-size warning and a "inconclusive" branch | M7-T1 | 0.5d |
| [ ] M7-T3 | Tailscale (or WireGuard) access so the phone can upload from outside the LAN | M1-T5 | 1d |
| [ ] M7-T4 | Grafana read-only dashboard over the SQLite mirror (optional) | M4-T1 | 1.5d |
| [ ] H5 | **`[HUMAN]`** Week-4 check: is this helping or feeding anxiety? | M5-T3 | — |

---

## Execution Rules

1. Read `.task-progress.json` **first**, then this file, then `PRD.md`. The tracker is
   fresher than the checkboxes.
2. Resolve the next task by **dependency order**, never visual order. Ties: lowest
   milestone, then lowest task number. **Resolution considers only non-`[HUMAN]` tasks:**
   a machine task is ready when all of its *machine* dependencies are done. Human tasks
   never block machine work — M0's sensor work and M1–M5's build work are independent
   tracks, and treating H1 as a blocker would stall the whole plan behind a purchase.
3. Exactly **one task per run**, then stop. A job that gets ahead produces an unreviewed
   diff nobody reads.
4. `[HUMAN]` tasks are never attempted. List the ones still pending as an
   `awaiting human:` line in the report; never simulate the hardware, never mark one done.
5. Read the existing source before writing new code; extend tests with every task.
6. `make verify` before every commit. Commit `feat:` then a separate `chore:` for the
   tracker. No remote → commit locally and skip the push.
7. When every task is done (or all remaining are `[HUMAN]`), set `complete: true` in the
   tracker and answer `[SILENT]`.
8. Never touch the user's Telegram chat to test a notification path — use a local harness.

## Verification Gate (per task, blocks the commit)

- [ ] Code implemented; tests added or updated for the new behaviour
- [ ] `[auto]` invariants relevant to the task hold on **all** fixtures
- [ ] `make verify` green (ruff clean, pytest green)
- [ ] Tracker updated, committed as its own `chore:` commit, pushed
- [ ] Any measured number quoted in docs was produced by a command in this run

## Prohibitions — the invariants that must not be weakened

Restated from PRD §Closed-World Invariants as hard limits. Each names the failure mode.

- **Never interpolate, forward-fill, or zero-fill a gap.** A six-hour hole across a meal is
  the measurement. Report it; do not fabricate a line through it.
- **Never join `imports_raw` into a live metric.** An offline CSV must not be able to
  inflate coverage or time-in-range.
- **Never emit a metric below the data threshold.** `coverage_pct < 70` or `n < min_n` →
  `insufficient_data: true`. If a fixture makes this fire, the fixture is the specification
  — fix the code, never the fixture.
- **Never make the pipeline non-deterministic** to get a test green (no clock reads, no
  randomness, no dict-order dependence in outputs).
- **Never commit a secret.** Tokens live in `~/glucose/secret.env` (0600); a test fails the
  build if a token appears in tracked files.
- **Never predict or prescribe.** No dosing, no diagnosis, no "you should", no good/bad
  labels in any rendered string.
- **Never delete a flagged reading.** Flags annotate; only the exclusion set removes a
  reading from a statistic, and the flag list still reports it.

## Review Loop (milestone boundaries — the artifact)

Not a vibe check; a repeatable procedure with a log.

1. Load the affected milestone's outputs (fixtures, or the live stack if deployed).
2. Walk the checklist: every `[review]`-adjacent invariant; the human-review line for that
   milestone; one number re-derived by hand from raw rows.
3. Log each defect: `{ severity: sev-1 (wrong number / data loss) | sev-2 (wording, UX),
   where, repro }`.
4. Fix, then re-walk only the affected area.
5. Exit rule: zero sev-1; ≤ 2 sev-2. Record the log in `.task-progress.json` under
   `review_log`.

## Current Status

- **Created:** 2026-11-24 · repo seeded, **no tasks executed**
- **Next up:** H1 (buy one Stelo pack) and M1-T1 (compose) — they are independent;
  M1–M5 are machine-buildable without a sensor, M0 needs hardware.
- **Known unknowns:** real xDrip+ upload cadence, real 24-h coverage, whether Stelo's app
  and xDrip+ coexist on one phone without fighting over the BLE connection.
