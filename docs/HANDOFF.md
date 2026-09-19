# FluentPet API — handoff

Status on 2026-09-19: milestones 1–9 of the PRD are built, tested (111 endpoint tests,
`uv run pytest`) and **live in prod**: `https://47-130-5-81.sslip.io` (one EC2 `t3.micro` in
`ap-southeast-1`, Neon, R2, Firebase; `docs/AWS_SETUP.md` is what was actually done). Every push to
`main` migrates Neon and redeploys the box; `scripts/smoke.sh <url> .env.prod` is the acceptance
check (9/9 on the first deploy). The URL changes if the instance is stopped/started (no Elastic IP yet).

Spec: `PRD.md` in the old Rails repo (`fluentpet_server/PRD.md`). Where this document and the
PRD disagree, the tests win — they are the behaviour that actually ships.

## What it is

A FastAPI 0.141 / Python 3.14 backend for the FluentPet remake app. Postgres 18 via SQLAlchemy
2 async + Alembic, Firebase Auth for users, FCM for push, Cloudflare R2 (S3 API via boto3) for
audio and avatars, one EC2 box running docker compose (hosting moved from GCP to AWS to use credits;
App Runner was the plan but is unavailable on new-free-plan AWS accounts;
migrations run from CI, the base-offline check is an endpoint the user hits from a laptop).
No queue, no Redis, no AWS IoT — a device script you write talks to `/api/v1/device/*`.

```
app/
  main.py        app, routers, error envelope {error:{code,message}}, request log, /healthz
  settings.py    env vars (pydantic-settings, .env for local)
  db.py          engine (pool 5), one transaction per request, AfterCommit task list
  auth.py        Firebase token → user (first sign-in provisions household), X-Login-As, device key
  models.py      22 tables
  schemas.py     all request/response models
  routers/       one file per resource (me, household, pushers, preferences, push_tokens,
                 buttons, audios, bases, contexts, notes, interactions, search, stats, device)
  services/      the business rules: buttons (text→word), devices (linking), presses
                 (press grouping), interactions (modeling, merge, split, counters), push,
                 storage (R2), webhooks
  jobs/base_offline.py   hourly job (also exposed as POST /internal/base-offline, X-Job-Key)
alembic/         0001 schema+seeds, 0002 buttons.created_at, 0003 deferred press_order unique
tests/           httpx against real Postgres; Firebase/FCM/R2/webhooks faked at the client boundary
scripts/loadtest.py   seeds 50k interactions and times /interactions/search
```

## Running it

```sh
cp .env.example .env
docker compose up -d --build      # Postgres 18 (+ fluentpet_test DB) and the API on :8080 with reload
uv sync && uv run pytest          # 92 tests, ~5 s
open http://localhost:8080/docs   # OpenAPI (device routes are hidden on purpose)
PYTHONPATH=. uv run python scripts/loadtest.py   # search p95 at 50k interactions
```

Tests drop and re-create the schema on `fluentpet_test` every session, then truncate between
tests. Auth is faked by overriding `get_claims`; FCM, R2 and webhook HTTP are faked by the
`fcm`, `s3`, `webhooks` fixtures in `tests/conftest.py`.

## Configuration

| Var | Used for |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` (Neon: add `?ssl=require`) |
| `FIREBASE_CREDENTIALS_JSON` | service-account JSON (one line) for token verification + FCM |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | audio + avatars |
| `DEVICE_API_KEY` | shared secret for `/device/*` (`X-Device-Key`) |
| `JOB_API_KEY` | shared secret for `/internal/*` (`X-Job-Key`), sent by the user's local trigger script |
| `DEV_TOKENS` | dev only: `ann:ann@example.com:Ann,…` bearer tokens that bypass Firebase; refused when `ENV=prod` |
| `SENTRY_DSN` | optional |
| `ENV` | `dev` / `prod`; only used as the Sentry environment tag |

## How the API is shaped

* Every request runs in one DB transaction; anything that must happen *after* commit
  (push, webhook) is queued on `AfterCommit` (see `app/db.py` — FastAPI runs plain
  `BackgroundTasks` before dependency teardown, which is before commit).
* Errors: `{"error": {"code": "...", "message": "..."}}`. 401 no/invalid token, 403 not allowed,
  404 not yours/not found, 409 conflict, 422 validation (first field named in the message).
* Users: first Firebase sign-in creates user + household `FLUENT#####` + human pusher +
  `inaudible` button. Household scoping comes from the token, never from the body.
* Admin support: `X-Login-As: email` when the Firebase token has the `admin: true` custom claim;
  counted per hour in `impersonation_logs`.
* Invitations are in-app only (no email sending). Pending invitations show up on `GET /me`.

Rules that are easy to get wrong (all pinned by tests):

* **Button text** `services/buttons.py`: `text` (trimmed, whitespace collapsed) → `word` (lower,
  emoji stripped, trailing parenthetical and `!?.` removed) → `normalized_word` (synonym dict).
* **Unlink protocol**: unlink/hide/delete of a linked button sets `desired_deleted` and bumps
  `desired_version`; the `base_buttons` row stays until the device acks that version. A serial
  seen again after that is a *new* connect button (PRD §7).
* **Press grouping** `services/presses.py`: a base press joins the most recent base interaction
  on that base within `group_window_seconds` (also when it arrives late), duplicates are
  same-button-same-second, future timestamps clamp to now, epoch values ≥ 2×10⁹ are millis.
  Pusher = base default → owner's `default_pusher_id` preference → unassigned.
* **Modeling** `services/interactions.py::apply_modeling` runs after every interaction write.
* **Merge/split/bulk**: see `test_interactions.py` — those tests are the spec.
* **Search** `routers/search.py`: interactions and notes are UNIONed, counted, sorted, paged, and
  only the page is hydrated. Interaction-only filters (pusher/button/context/base, `presses`,
  `with_note=only`, `notes=exclude`) drop notes from the feed.
* **Push** `services/push.py`: multicast in chunks of 500, one `push_log` row per token, dead
  tokens deleted. `button_pressed` honours each member's `push_frequency`; `base_battery_low`
  and `base_fully_charged` are 1/user/24 h; `base_offline` once per outage (36–168 h quiet).
* **Webhooks**: only on new *base* presses; GET, 5 s timeout, 3 attempts, `webhook_logs` row per
  attempt (`GET /buttons/{id}/webhook-logs`). URLs may not point at loopback/private/metadata
  hosts (checked on save and again after DNS resolution).

## AI (milestones 7–9, PRD §12)

`app/services/ai.py` is the whole thing; `app/routers/ai.py` and `app/jobs/weekly_digest.py` are
thin. `client()` is the boundary `tests/conftest.py::FakeClaude` replaces (it runs the real tool
functions when a reply names them).

* **`POST /ai/log-text`** → `messages.parse` with a structured-output schema → a draft
  `InteractionIn`. Ids not in the household are dropped; a naive time is read on the user's
  clock; no time = now. The app confirms and posts it — the server never writes from AI output.
* **`POST /ai/chat`** → beta tool runner, ≤ 5 tool rounds, two `@beta_async_tool` closures over
  (session, user): `stats_summary` = `routers/stats.summary`, `search_interactions` =
  `routers/search.search` printed one line per row in the user's timezone. Tool errors (bad
  range, foreign pusher) go back to the model as `error: …`, never as an HTTP error. System
  block (rules + vocabulary) is cached; the clock is a second, uncached block. Stateless: the
  app sends the thread back (≤ 20 messages).
* **`POST /internal/weekly-digest`** (X-Job-Key): households with ≥ 3 interactions in 7 days and
  no digest in 6 → one `messages.create` per household with this week's + last week's stats per
  learner → push `weekly_digest` + a note `Weekly digest — …` (created_by_user_id null). A model
  failure skips the household; it is retried next run.
* `ai_log` (one row per successful call, in the request transaction) is the rate limiter and the
  spend meter: `select model, sum(input_tokens), sum(output_tokens) from ai_log group by 1`.
* Provider is a setting: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` point the same SDK at a
  Messages-compatible gateway (Token Harbor: `deepseek-v4.1-flash` is on their free tier).
  `settings.ai_is_claude` gates the Claude-only extras (effort, `messages.parse` schema,
  cache_control); other models get the schema in the prompt and the JSON parsed from text.
* Not done on purpose: MCP, RAG, streaming, stored chat threads (PRD §12.10).
* Ceiling: an AI request holds its pooled connection (of 5) for the model call, ~5–10 s. Fine at
  launch; if it bites, tools open their own `SessionLocal()` (PRD §12.8).

## Device script contract

`tests/test_device_contract.py` is the executable version. In short:

1. Every call sends `X-Device-Key`.
2. Boot: `PUT /device/bases/{serial}/state {reported_state, fw_version?, battery_level?}`.
3. `POST /device/events` with `button_seen` for each attached button; unknown serials become
   `Button {SERIAL}` buttons in the owner's app.
4. Loop: `GET /device/desired?serial_number=…` → download audio by `url`, verify `crc32`
   (decimal), flash, then `POST /device/desired/ack [{base_button_id, applied_version}]`.
   `desired_deleted: true` means remove the button from the base.
5. Stream `press`, `battery {level, charging?}`, `power {charging}`, `fully_charged`, `online`.
   Delivery is idempotent on (base, type, occurred_at, button serial): re-send whole batches
   after a crash, you get `duplicate` back. `occurred_at` accepts ISO 8601, epoch seconds or
   epoch millis.
6. `POST /device/audio-url {serial_number, audio_id}` if a file is ever needed out of band.

The base must be registered by its owner in the app first, otherwise events answer `unknown_base`.

## Load test (search)

`scripts/loadtest.py`, 50k interactions + 2k notes in one household, 200 requests with 10 in
flight, ASGI in-process against Docker Postgres on a laptop:

| scenario | p50 | p95 |
|---|---|---|
| feed page 1 | 73 ms | 106 ms |
| feed page 200 | 79 | 105 |
| pusher / button / context filters | 52–69 | 56–105 |
| text search (`ILIKE`) | 116 | 143 |
| notes only | 32 | 37 |

Budget in the PRD is 300 ms p95. Neon's free tier adds network and has less CPU; re-run
`scripts/loadtest.py` against a Neon branch before launch (set `DATABASE_URL`). If text search
ever matters, add a trigram index on `interactions.note` / `notes.text`.

## Decisions taken while building (not in the PRD)

* `GET /contexts?kind=` maps to `applies_to`: teacher = not learner-only, learner = not
  human-only, custom = my household. `Modeled` is human-only.
* `GET /buttons/{id}/webhook-logs` was added so users (and tests) can see hook outcomes.
* `GET /device/desired` requires `serial_number` so one device cannot list other households.
* Orphaned households (after accept/leave) are kept, not deleted.
* Bulk merge target is the first id given (`all: true` → the earliest interaction).
* `button_presses (interaction_id, press_order)` is a deferred unique constraint so renumbering
  in merge/split/patch is plain updates.
* Interactions without presses still count as interactions in search counts; stats per-day and
  per-hour buckets only count interactions that have presses (same as the Rails app).

## Known corners

* DNS-rebinding window on webhooks (resolve-then-connect). Pin the IP if it ever matters.
* Search is several statements (count, page, counts, hydrate), not the single window-function
  query the PRD imagined; it is well under budget, so it stayed simple.
* `base_offline` runs only when someone calls `/internal/base-offline` (`docs/AWS_SETUP.md` §7,
  triggered from the user's laptop by choice — no EventBridge); nothing calls it otherwise.
* No rate limiting on the device key; it is a long shared secret, rotate it in SSM Parameter Store and redeploy.
* `X-Login-As` trusts the Firebase `admin` custom claim; set it with the Admin SDK only.

## What's left after milestone 6

Hosting is done (`docs/AWS_SETUP.md`). Remaining, none of it code unless noted:

* Play Store account-deletion URL (PRD open question 4): a static page; `DELETE /me` already
  does the work.
* Re-run `scripts/loadtest.py` against Neon.
* Decide Google-only vs email/password sign-in (PRD open question 1) — Firebase console only.
* Elastic IP + a real domain before real users (sslip.io shares a Let's Encrypt rate limit;
  `HOST=` in `deploy/ec2.sh`).
* App e2e: real Firebase sign-in → `GET /me`; device script with the prod `X-Device-Key`.
* Sentry DSN into `/fluentpet/prod/SENTRY_DSN` if wanted (code already wired, blank = off).
