# GlucoseLedger — Product Requirements Document

**Status:** Draft (not started — see "Pipeline status" at the end)
**Date:** 2026-11-24
**Author:** Hermes Agent (interactive session, at Jeff's request)
**Category:** Personal tooling / local-first health data pipeline
**Difficulty:** Intermediate — almost all ops, very little novel code
**MVP Timeline:** 10 build days + a 15-day sensor trial that runs underneath them

> **Naming note.** Deliberately not GlucoTrack / GlucoSense / GlucoseGuard. The
> tool stores and describes a series; the moment it is named after a *verdict*
> the numbers get read as one. §2 and the anti-goals depend on the name staying
> neutral — same reasoning as MoodLog.

---

## Executive Summary

GlucoseLedger is a self-hosted continuous-glucose pipeline: **Nightscout** in
Docker on the home server as the store and viewer, **xDrip+** on the phone as the
sensor reader and uploader, a **nightly ETL** that pulls the Nightscout API into
a local SQLite database, and a **weekly markdown report** that lands in Telegram
and in the Obsidian vault.

The interesting part is not the server — Nightscout installs in an afternoon and
has for a decade. The interesting part is the **data path**. The two no-prescription
sensors available in the US (Dexcom Stelo, Abbott Lingo) are deliberately closed:
Stelo has *no real-time sharing at all*, and Lingo offers a manual CSV export
once per day. Neither will hand you a live feed. So "self-hosted" here means one
specific thing: an open-source app on the phone reads the sensor directly over
Bluetooth and pushes every reading to a server you own, and no vendor cloud is in
the loop at any point. That is achievable, it is unofficial, and it is the risk
this document is mostly about.

**Build: 10 days.** The honest gate is not the build; it is **15 days of one real
sensor proving the read path holds** before a subscription is worth paying for.

---

## Problem Statement

1. **The sensor vendor is the only door, and the door is narrow.** Stelo runs its
   own app and shares data only with the vendor's own ecosystem (Clarity, and
   opt-in third parties like Oura). Lingo exports a CSV, once a day. Neither
   offers a documented real-time API to an individual.
2. **The vendor dashboards answer the wrong question.** They show a score, a
   monthly average and a pretty curve. The questions worth asking are
   *postprandial iAUC for this meal*, *time-in-range for this fortnight*,
   *variability*, *dawn delta*, *what changed when I moved dinner an hour later* —
   and none of them can be recomputed from a vendor app, because the vendor app
   will not show you its numbers.
3. **A CSV export is a photograph, not a database.** One file, once a day, no
   join to meals or sleep, no way to ask a six-month question. The moment you want
   a second question you are back to hand-manipulating exports.
4. **Hosted Nightscout services exist and are fine — but they reintroduce exactly
   the dependency this is trying to remove.** Paying a vendor to hold your CGM
   data is the same trade as letting the sensor vendor hold it, only with an extra
   party.
5. **This data is unusually sensitive and unusually continuous.** It is a health
   signal that also reveals meal times, sleep, exercise and travel. A leak is
   permanent and it is not a password you can rotate.

---

## Target Audience

**Primary:** Jeff — one user, Samsung A15 5G (Android), home Debian box already
running the Hermes gateway and cron scheduler.

**Secondary (if ever open-sourced):** self-hosters who already run a stack and
refuse to put health telemetry in someone else's cloud.

| Persona | Pain Point | GlucoseLedger Value |
|---|---|---|
| **Primary: self-hosted tinkerer** | Wants the CGM curve in his own database, joinable to everything else he tracks | Nightscout store + nightly SQLite mirror + reproducible metrics |
| **Secondary: n-of-1 experimenter** | Wants to test one food or habit and get a number, not a vibe | Pre-registered meal experiments with postprandial iAUC (§6, F11) |
| **Secondary: data-portability worrier** | Distrusts one-way vendor exports and dead-end apps | Append-only raw layer; the whole history is one `mongodump` or one SQLite file |

### Anti-goals (non-negotiable)

- **Not a diagnostic tool.** It does not diagnose diabetes, prediabetes, insulin
  resistance, or anything else. Diagnosis is A1c plus a lab, full stop.
- **Not a dosing tool and not a closed-loop component.** No insulin suggestions,
  no bolus maths, no OpenAPS/AAPS/Trio integration. That is a different project
  with a different risk class and it is explicitly out of scope.
- **Not a safety-alerting system.** No hypo/hyper alarms presented as protection.
  If it ever alerts, it is a convenience notification with a disclaimer, never a
  thing anyone is meant to rely on.
- **No vendor cloud in the default path.** Gluroo's Global Connect is documented
  only as a fallback, and the document says so where it appears.
- **No gamification.** No "glucose score", no streaks, no grades. A score invites
  optimising the score.
- **Not a food database or a diet app.** Meals are labels on events, nothing more.

---

## CGM Data Path Decision Record

This is the section the project lives or dies on, so it is a decision record, not
a feature list. Facts below were checked on **2026-11-24**; re-verify before
committing money, because vendor app behaviour changes without notice.

| Option | Sensor | Reader / transport | Live? | Verdict |
|---|---|---|---|---|
| **A. Stelo → xDrip+ → Nightscout** | Dexcom Stelo (OTC, $89/mo sub or $99/2-sensor pack) | xDrip+ on Android, hardware option "Dexcom G6/G7/1/1+", 4-digit transmitter ID | Yes (~5 min cadence) | **Recommended.** Only path that is live, local and cloud-free |
| B. Stelo → Gluroo Global Connect | Stelo | Gluroo app (free) exposes a Nightscout-compatible API | Yes | Fallback only — Gluroo is a vendor cloud; defeats the purpose |
| C. Stelo → Zukka / xDrip4iOS | Stelo | iOS third-party readers exist | Yes | Not applicable — Android phone |
| D. Rx G7 → xDrip+ or Dexcom Share | Dexcom G7 (prescription) | Native share exists; Dexcom's own API is available to approved apps | Yes | Better data path, but needs a prescription and a clinical reason |
| E. Lingo → CSV → importer | Abbott Lingo (OTC, $84/4 weeks) | Manual export, once per day, 5-minute values | No | Import path only (F9). Fine for retrospective, useless for live |
| F. Libre 3 → Juggluco → Nightscout | FreeStyle Libre 3 | Requires a prescription sensor; regional BLE support varies | Yes | Viable if a Libre prescription ever exists |

**Chosen: A, with E as the offline import path and B named as the fallback if the
xDrip+ route breaks.** Rationale: A is the only option that satisfies all three
constraints at once — live data, no vendor cloud, runs on hardware already owned.

**The honest caveats, stated here rather than buried in risks:**

- **Stelo has no real-time sharing capability.** This is the vendor's design, not
  a missing setting. Anything that says otherwise is describing a workaround.
- **The xDrip+ route is unofficial.** It works because Stelo speaks a Dexcom BLE
  protocol that xDrip+ already implements, and the community has documented the
  pairing (hardware option G6/G7, 4-digit transmitter ID, readings appear 15–20
  minutes after warmup). A firmware change can end it. Hence M0.
- **Dexcom's official API v3 does not cover Stelo** — it covers G6/G7/ONE. If the
  official API is ever used, note it runs on 2-hour OAuth tokens with 30-day query
  windows and, in the US, roughly a one-hour data delay (secondary source — verify
  against Dexcom's own docs before relying on it).
- **Self-hosting does not remove the phone.** Bluetooth range is metres; the
  server cannot see the sensor. The phone is the reader, permanently. What
  self-hosting removes is the *cloud*, not the handset.

---

## Feature Requirements

### Must-Have (MVP — 10 days)

| ID | Feature | Description | Priority |
|---|---|---|---|
| F1 | Nightscout stack | `docker compose`: `nightscout/cgm-remote-monitor` (v15.0.9 at time of writing) + MongoDB, pinned tags, `~/glucose/stack/`, `restart: unless-stopped`. Mongo major version pinned deliberately — the docs warn that a newer Mongo must not be pointed at an old data directory. | P0 |
| F2 | Auth & exposure control | `API_SECRET` generated (32+ bytes) and stored `0600`; `AUTH_DEFAULT_ROLES=denied`; site bound to LAN only (no port forward). A proof test asserts an unauthenticated request gets 401. | P0 |
| F3 | Live ingest path | xDrip+ on the A15 paired to a Stelo sensor, uploading to `https://<token>@<host>/api/v1`. Verified by watching `/api/v1/entries` grow with no manual step. | P0 |
| F4 | Backups + a restore drill | Nightly `mongodump` to `~/backups/glucose/`, 14-day rotation. **Restore is executed once into a scratch container and the entry count reconciled** — an unverified backup is a hope. | P0 |
| F5 | Nightly ETL | Pull `/api/v1/entries` + `/api/v1/treatments` → SQLite. Append-only raw tables, idempotent on `(_id)`, re-run safe. | P0 |
| F6 | Core metrics | Per-day and per-window: mean, time-in-range (70–180), time-below/above, CV%, GMI, coverage %, gap count. Deterministic, unit-tested against fixtures. | P0 |
| F7 | Weekly report | Markdown to Telegram + a note in the vault: coverage first, then metrics, then the caveat block. "Insufficient data" instead of a number when coverage or N is under threshold. | P0 |
| F8 | Data-quality flags | Warmup periods, sensor-change seams, multi-hour gaps, and compression-suspect lows (night-time + sharp isolated dip + fast recovery) flagged in the derived layer and excluded from the statistics that would be distorted by them. Never deleted. | P0 |
| F9 | CSV import path | Importer for Lingo / Clarity exports into an `imports/` table, clearly separated from live data. Never merged into the raw live series — merging would double-count. | P1 |
| F10 | Meal events + postprandial response | Log meals as Nightscout treatments (carbs + note), then compute 2-hour iAUC and peak delta per meal, with the pre-meal baseline defined explicitly. | P1 |
| F11 | N-of-1 experiment runner | A pre-registered test (e.g. protein-first vs carbs-first, walk vs sit after dinner): fixed N, washout, pre-declared metric, result printed with a sample-size warning. | P2 |
| F12 | Grafana dashboard | Optional read-only dashboard over the SQLite/Postgres mirror for exploration. Not required for the report. | P2 |
| F13 | Remote access | Tailscale (or WireGuard) so the phone can upload from outside the LAN without opening a port. LAN-only ships first. | P1 |
| F14 | Non-clinical alerts | Opt-in notification when a reading crosses a personal threshold, phrased as information, with the anti-goal text in the message. | P3 |
| F15 | Multi-user / publish | Strip secrets and personal config; document the stack; publish if it survives 90 days. | P3 |

### Deliberately excluded (and why)

- **A custom web UI.** Nightscout already has one, and it is the thing the
  ecosystem's followers and apps expect. Writing a second one is vanity.
- **Forking Nightscout.** AGPL, and the fork would be maintained forever for no
  benefit. Consume its API instead.
- **Any insulin/bolus feature.** See anti-goals.

---

## Technical Architecture

### High-Level Design

```
  [Dexcom Stelo sensor]  ──BLE──▶  [Samsung A15: xDrip+]
                                        │  HTTPS POST, token in URL
                                        │  (LAN, or Tailscale from outside)
                                        ▼
                        ┌───────────────────────────────────────┐
                        │ debian2 (hostname: hermes)            │
                        │   docker compose                      │
                        │   ├─ nightscout/cgm-remote-monitor    │
                        │   │    :1337  /api/v1/entries         │
                        │   └─ mongo:7 (pinned)  ← mongodump/24h│
                        │   bind: LAN only, AUTH_DEFAULT_ROLES  │
                        └───────────────┬───────────────────────┘
                                        │ nightly pull (API, read token)
                                        ▼
                        ┌───────────────────────────────────────┐
                        │ ~/glucose/data/glucose.sqlite         │
                        │   entries_raw  (append-only)          │
                        │   treatments_raw                      │
                        │   imports_raw  (CSV backfill)         │
                        │   metrics_daily / metrics_windows     │
                        └───────────────┬───────────────────────┘
                                        │ weekly cron (Hermes)
                                        ▼
                        [Telegram report + vault note (markdown)]
```

- **Stack:** Docker (29.9.1 present) + docker compose; Nightscout 15.x; MongoDB
  pinned minor; Python 3 (stdlib `sqlite3` + `pandas`/`numpy` for the metrics);
  Hermes cron for the nightly ETL and the weekly report; existing Telegram gateway
  for delivery; the Obsidian vault on rpi4 for the note.
- **Runtime:** two containers, LAN-bound, no inbound internet exposure in MVP.
  Outbound network calls: none except Telegram delivery.
- **Repo:** `~/code/software-projects/glucose-ledger/` (code, tests, runbooks).
  Deployment lives outside the repo at `~/glucose/` (stack, data, secrets);
  backups at `~/backups/glucose/`.

### Host Decision (measured, 2026-11-24 12:43 CST)

| Host | RAM (avail) | Disk free | Docker | Uptime | Verdict |
|---|---|---|---|---|---|
| **debian2 / hermes** | 15 GiB (9.9 GiB avail) | 95 GB on `/` (55% used) | 29.9.1 active | 36 days | **Chosen.** Room for both containers plus years of data |
| rpi4 | 3.7 GiB (2.6 GiB avail) | 5.6 GB of 28 GB (80% used) | 29.6.1 active | 45 days | Ready standby for the report job; disk too tight to be primary |
| debian1 | 31 GB | 1 TB | — | **unreachable** (`no route to host`) | Out. Not a 24/7 host — confirmed by its own state, not by policy |

The server only needs to answer HTTP and hold a small Mongo: the whole stack
should sit under 1 GiB RSS. debian2 is up 36 days because it is the gateway host;
the stack inherits that.

### Key Design Decisions

1. **Nightscout is the store, not the product.** We consume `/api/v1/*` and never
   fork it. Consequence: we inherit MongoDB and an AGPL upgrade path — accepted,
   because the alternative is writing and maintaining a CGM app.
2. **The phone stays the reader.** Consequence: self-hosting removes the cloud but
   not the handset dependency; a flat battery is a data gap no server-side design
   can fix. Say it plainly rather than pretending the server is the whole system.
3. **xDrip+ over the vendor app for Stelo (M0 gate).** Unofficial, open source,
   and the only live local path. Consequence: prove it on one sensor before
   buying a subscription — that ordering is a build task, not advice.
4. **Raw is append-only; every derived number is recomputable.** Consequence: a
   metric that turns out to be wrong is a code fix and a re-run, never a data loss.
5. **Nightly batch ETL, not a streaming pipeline.** A 5-minute sensor does not
   need a message bus. Consequence: the report is at most a day stale, which no
   question in §2 cares about.
6. **Gaps are recorded, flagged, and never interpolated.** Interpolating a
   six-hour phone-death across a meal would invent the exact response being
   measured.
7. **Report goes where the user already looks (Telegram + vault).** A dashboard
   nobody opens is not a deliverable; a weekly markdown note is.
8. **Two independent copies of the data, one restore drill.** Mongo for the app,
   SQLite for analysis, nightly dump on disk. The drill is what makes them matter.

### Performance Targets

| Concern | Target |
|---|---|
| Sensor reading → visible in Nightscout | ≤ 2 min (xDrip+ upload interval, not a server constraint) |
| Stack footprint | ≤ 1 GiB total RSS for both containers |
| Store growth | ~288 entries/day ≈ 105k/yr ≈ 15–35 MB/yr in Mongo |
| Nightly ETL runtime | < 30 s over 1 year of history |
| Backup + restore drill | < 15 min, scripted |
| Data completeness | ≥ 90% of expected readings per day (i.e. ≤ 28 missing of 288), excluding declared warmup |
| Weekly report delivery | within 5 min of the cron trigger, or an explicit failure message |

---

## Data Contracts

Nightscout is the source of truth; the SQLite mirror is a projection of it. Two
schemas, both fixed here so the ETL and the metrics cannot drift apart.

**Nightscout `/api/v1/entries` (fields used, verbatim from the API):**

| Field | Type | Notes |
|---|---|---|
| `_id` | string | Mongo ObjectId — the dedupe key for the mirror |
| `sgv` | int | mg/dL as delivered; **never converted in place** |
| `date` | int | epoch ms UTC |
| `dateString` | ISO8601 | server-rendered; display only, never the sort key |
| `type` | enum | `sgv` (sensor) \| `mbg` (meter) \| `cal` |
| `direction` | enum | trend arrow, e.g. `Flat`, `FortyFiveUp` |
| `device` | string | uploader identity, e.g. `xDrip-LimiTTer` |
| `delta` | float \| null | vendor-computed delta; recomputed locally instead of trusted |

**Nightscout `/api/v1/treatments`** — meal/event log: `eventType` (`Meal Bolus`,
`Carb Correction`, `Note`), `created_at`, `carbs` (g), `notes` (free text = meal
description), `enteredBy`.

**`~/glucose/data/glucose.sqlite`:**

| Table | Shape | Rule |
|---|---|---|
| `entries_raw` | mirror of the API row + `fetched_at` | append-only; `INSERT OR IGNORE` on `_id` |
| `treatments_raw` | mirror of the API row + `fetched_at` | append-only; same rule |
| `imports_raw` | CSV rows from Lingo/Clarity + `source_file`, `source` | kept **separate** from `entries_raw`; never unioned into live stats |
| `quality_flags` | `entry_id`, `flag` (`warmup`, `gap`, `compression_suspect`, `sensor_change`) | derived, rebuildable |
| `metrics_daily` | `date_local`, `n`, `coverage_pct`, `mean`, `tir_pct`, `below_pct`, `above_pct`, `cv_pct`, `gmi` | derived, rebuildable |
| `metrics_windows` | `window_start`, `window_days`, `n`, `coverage_pct`, metric columns, `insufficient_data` | derived, rebuildable |

**Report contract** (stdout JSON, and the numbers used to render the markdown):

```
{
  "window_days": 7,
  "n_entries": 1934, "coverage_pct": 95.9, "gap_count": 2,
  "mean": 104.2, "tir_pct": 88.1, "below_pct": 1.2, "above_pct": 10.7,
  "cv_pct": 21.4, "gmi_pct": 5.6,
  "meals_logged": 12, "meals_analysed": 9,
  "flags": ["warmup:2026-11-30", "gap:2026-12-02T03:10/07:40"],
  "insufficient_data": false,
  "caveat_version": "2026-11-24"
}
```

`tir_pct`, `cv_pct` and `gmi_pct` are descriptive statistics of a series. They are
never labelled good/bad, and never accompanied by advice.

---

## Closed-World Invariants

- **[auto]** Every derived number is reproducible: same raw tables in → byte-identical
  JSON out, with the code version stamped in the output.
- **[auto]** Raw tables are append-only. A re-run of the ETL cannot duplicate a row
  (`INSERT OR IGNORE` on `_id`) and cannot delete one.
- **[auto]** `imports_raw` is never joined into a live metric; an offline CSV cannot
  inflate coverage or time-in-range.
- **[auto]** No metric is emitted for a window with `coverage_pct < 70` or
  `n < min_n`; the output sets `insufficient_data: true` instead.
- **[auto]** Gaps appear as gaps: no interpolation, no forward-fill, no zero-fill,
  anywhere in the pipeline.
- **[auto]** All timestamps stored UTC with epoch ms preserved; local rendering uses
  `America/Chicago` explicitly, never the system default.
- **[auto]** Unauthenticated reads of the Nightscout API return 401 (asserted by a
  test, on the deployed instance, not on a mock).
- **[auto]** Backup artifact exists for yesterday, is non-empty, and its entry count
  reconciles with the live collection.
- **[auto]** No secrets in the repo; `API_SECRET` and read tokens live in `0600`
  files under `~/glucose/`, and a grep test fails the build if a token appears in
  tracked files.
- **[review]** Weekly report is read by a human and contains the caveat block
  verbatim (checked by reading one real report).
- **[review]** Report wording describes the series, never prescribes behaviour
  (no "you should", no dosing, no risk prediction).

---

## Milestones & Timeline

| Milestone | Name | Days | Exit Criteria |
|---|---|---|---|
| **M0** | **Read-path spike (gated, before spending)** | 1–15 | One real Stelo sensor paired to xDrip+; ≥ 24 h of readings land in the Nightscout instance with `device` naming xDrip+. **If this fails, the project stops here** and the fallback decision in §Data Path is taken |
| M1 | Stack + access control | 1–2 | compose up on debian2; 401 on unauthenticated read; LAN bind proven from a second device; restart survives `docker compose down/up` |
| M2 | Durability | 3–4 | Nightly `mongodump` on cron; **one restore executed** into a scratch container with entry count reconciled; retention prunes at 14 days |
| M3 | ETL + metrics | 5–7 | `entries_raw` growing nightly; idempotency proven by running twice; fixtures produce the specified metric values; determinism test green |
| M4 | Report + quality flags | 8–10 | Two consecutive weekly reports delivered to Telegram and the vault; coverage printed first; warmup/gap/compression flags present; "insufficient data" path exercised by a fixture |
| M5 (post-MVP) | Meals + experiments | +7 | F10 iAUC computed for 10 logged meals; F11 runner completes one pre-registered experiment |
| M6 (post-MVP) | Remote access, dashboard | +5 | xDrip+ uploads over Tailscale from outside the LAN; Grafana reads the mirror |

---

## Budget

Computed, not estimated by hand (`$89 × 12`, `$84 × 13`, `$99 × 12` — verified
prices, 2026-11-24):

| Line | Basis | Per month | Per year |
|---|---|---|---|
| Stelo subscription (2 sensors / 30 days) | $89/mo auto-renew | $89.00 | **$1,068** |
| Stelo one-time packs (2 sensors / 30 days) | $99 per pack × 12 | $99.00 | $1,188 |
| Lingo (import-only path) | $84 per 4 weeks × 13 cycles | $91.00 | $1,092 |
| Server, storage, backups | existing debian2 hardware | $0 | $0 |
| Software (Nightscout, Mongo, xDrip+, Grafana) | AGPL / open source | $0 | $0 |
| **Total, chosen path (A)** | | **$89.00** | **$1,068** |

The only real cost is the sensor subscription. Note the asymmetry: the cheapest
option (Lingo) is also the one with no live feed, so it buys a retrospective
pipeline, not this one.

---

## Task List (Grouped by Milestone)

### M0 — Read-path spike
- [ ] T1 Buy **one** Stelo sensor pack (not a subscription) and install the Stelo app
- [ ] T2 Install xDrip+ from `jamorham.github.io`; grant all permissions and unrestricted battery
- [ ] T3 Pair: hardware option "Dexcom G6/G7/1/1+", 4-digit transmitter ID; confirm readings 15–20 min after warmup
- [ ] T4 Stand up a throwaway Nightscout (compose, no hardening) and point xDrip+ at `https://<token>@<host>/api/v1`
- [ ] T5 Watch `/api/v1/entries` for 24 h unassisted; record the measured upload cadence and any gaps
- [ ] T6 **Decision gate:** if 24 h coverage ≥ 90%, proceed and buy the subscription. If not, stop and take fallback B/E

### M1 — Stack + access control
- [ ] T7 `~/glucose/stack/docker-compose.yml`: Nightscout 15.x + Mongo (both pinned), volumes on `/`
- [ ] T8 Generate `API_SECRET` (32 bytes hex) into `~/glucose/secret.env`, `chmod 600`
- [ ] T9 Env: `MONGO_CONNECTION`, `DISPLAY_UNITS=mg/dl`, `AUTH_DEFAULT_ROLES=denied`, `TIME_FORMAT=24`, LAN bind
- [ ] T10 Test: unauthenticated `GET /api/v1/entries` → 401 (automated, against the live instance)
- [ ] T11 Test: authorised read with the SHA1 token → 200; document the exact URL form
- [ ] T12 Survivability: `docker compose down && up` → data and auth intact

### M2 — Durability
- [ ] T13 Nightly `mongodump` script → `~/backups/glucose/<date>/`, 14-day rotation
- [ ] T14 Restore drill: restore the newest dump into a scratch container; reconcile entry count
- [ ] T15 Write the drill as a script so it is repeatable, and record the measured runtime
- [ ] T16 Cron the backup; alert on failure through the existing gateway

### M3 — ETL + metrics
- [ ] T17 Nightly fetcher: `/api/v1/entries` + `/api/v1/treatments` since last `_id` watermark → SQLite
- [ ] T18 Schema + `INSERT OR IGNORE` on `_id`; idempotency test (run twice, assert no change)
- [ ] T19 `metrics_daily`: mean, TIR/TBR/TAR, CV%, GMI, coverage %, gap count
- [ ] T20 `quality_flags`: warmup window, sensor-change seam, gap > 30 min, compression-suspect rule
- [ ] T21 Fixtures: a clean day, a day with a 6-h gap, a warmup day, a compression dip — expected values pinned
- [ ] T22 Determinism test: same input twice → identical JSON; code version stamped
- [ ] T23 Min-data gate: coverage < 70% or `n < min_n` → `insufficient_data: true`

### M4 — Report + honesty pass
- [ ] T24 Weekly cron: analyse → render markdown → Telegram + vault note
- [ ] T25 Report template: coverage first, metrics second, flags third
- [ ] T26 Caveat block (versioned, verbatim, in the output rather than the README)
- [ ] T27 Wording review: no advice, no prediction, no good/bad labels
- [ ] T28 Two real consecutive weeks delivered and read

---

## Success Metrics

**Machine-verifiable**
- All `[auto]` invariants hold over the full history (a single `make verify` run).
- xDrip+ → Nightscout coverage ≥ 90% of expected readings over 14 consecutive days.
- ETL is idempotent and deterministic; metrics reproduce bit-identically from raw.
- Restore drill completes under 15 min with reconciled counts.
- Unauthenticated API access returns 401 on the deployed instance.
- Nightly runtime < 30 s per year of history.

**Human-verifiable**
- **Coverage is the real metric:** 30 days with ≥ 90% readings and no silent gaps
  longer than a couple of hours.
- One meal experiment completed end-to-end with a number attached (F11), and the
  result either changed a behaviour or was honestly inconclusive.
- The weekly report is read every week for a month — and does not induce anxiety.
  Explicit check at week 4: if staring at the curve is producing stress rather
  than information, the framing has failed and the cadence drops to monthly.
- At least one decision (a food, a meal time, an evening walk) is made from the
  data rather than from a guess.

---

## Risk & Mitigation

| Risk | Why it's scary | Mitigation | Fallback |
|---|---|---|---|
| **xDrip+ ↔ Stelo pairing is unofficial** | A vendor firmware change can kill the whole live path; discovered after paying for months | M0 gate: 24 h of real readings before buying a subscription | Gluroo Global Connect (cloud, degrades the privacy goal) or Lingo CSV import (loses live) |
| **Stelo has no real-time sharing, by design** | Any integration is a workaround on borrowed time | State it in the README and in the report footer; keep the CSV importer working | Retrospective-only pipeline from daily CSV export |
| **Android battery management kills the background reader** | Silent gaps exactly where the interesting data is (overnight) | Unrestricted battery + wake lock; coverage % printed every report makes gaps visible immediately | Second cheap Android phone kept on charge as a dedicated reader |
| **Host downtime / reboot** | CGM reads stop; report may not run | `restart: unless-stopped`; xDrip+ buffers locally and backfills on reconnect | Run the report job from rpi4 against the same API |
| **Mongo version upgrade trap** | Docs warn that a newer major version must not be pointed at an old data dir; a careless `latest` pull corrupts the store | Pin both images by tag; upgrade only with a dump taken first | Restore the dump into a fresh volume |
| **Exposed instance** | A misconfigured `AUTH_DEFAULT_ROLES` turns a home server into a public glucose feed | LAN bind + 401 proof test in CI-ish cron; no port forwarding in MVP | Take the stack down; rotate `API_SECRET`; restore from dump |
| **Over-measurement / health anxiety** | A CGM produces 288 numbers a day and no context; the failure mode is obsessing over a laggy, noisy signal | Weekly cadence, coverage-first reports, no alarms, no scores, explicit week-4 check | Drop to monthly reports; keep the collector running silently |
| **Noise read as signal** | Interstitial lag (5–15 min), first-24-h warmup, compression lows — all produce artefactual "spikes" and "crashes" | Warmup/compression flags excluded from stats; meal responses measured on 2-h windows, not single points | Report only mean/TIR/CV and drop per-meal analysis |
| **Cost creep** | $89/mo forever for a hobby; $1,068/yr is real money | M0 gate before subscribing; pause the subscription in months with no experiment running; imported history survives a pause | Cancel and run the pipeline on imported CSVs |

---

## Open Decisions (before M1, not blockers for M0)

1. **Sensor.** Stelo (recommended, live path exists) vs Lingo (cheaper per month,
   import-only). M0 settles it with evidence rather than argument.
2. **Remote access.** LAN-only first; Tailscale afterwards if uploads from outside
   home matter. No port forward, ever.
3. **Report destination.** Telegram + vault is assumed. If the vault note is more
   trouble than it's worth on rpi4, keep Telegram only.
4. **Retention.** Raw entries kept forever (they are tiny). Whether to keep the
   Mongo store forever or archive to SQLite after 12 months — decide at month 12.
5. **Nightscout UI vs a small custom view.** Assumed: use Nightscout's. Revisit
   only if the UI is actually painful in daily use.

---

## Pipeline status

This is a **hand-authored PRD for a personal deployment, not a pipeline project**.
There is no `project_ideas` row, no repo, no `.hermes/status.json`, and no build
cron pointed at it. Creating those is a separate, explicit hand-off — do not read
this document as a queued build. The paired `BUILD_PLAN.md` (task-level, with the
machine/human acceptance regimes) is the next artifact if this draft is approved.

---

## Conclusion

Self-hosted glucose tracking is 80% operations and 20% new code, and the operations
are the part that decides whether it holds: a pinned stack, a proven read path, a
backup that has actually been restored, and a report that prints its own coverage
before it prints anything else.

The one thing that cannot be engineered around is the data path. Stelo does not
want to share in real time, so the honest plan buys **one** sensor, proves xDrip+
can read it and push it to a server in the house, and only then spends $1,068 a
year. If that spike fails, the fallback is a good retrospective pipeline built on
daily CSVs — smaller, still self-hosted, and no vendor cloud anywhere in it.
