# FluentPet API Remake — Product Requirements

Version 1.1 · 2026-09-19 (§12 AI features added) · Owner: Yogesh Vitekar

## 1. Summary

Rebuild the FluentPet backend as a small FastAPI service. The current Rails 7 app (8,400 lines, 51 tables, ~130 routes, Sidekiq, AWS IoT, Auth0, Expo, Petcube, Datadog) is being replaced because the original funding ended and the client wants a free, cheap-to-run Play Store app. This is a greenfield rewrite: no data migration, no backward compatibility with old API versions.

**Goal.** A backend that runs for near zero cost, covers the core button-board logging product, and can be operated by one developer.

**Non-goals.** AWS IoT / MQTT inside the API, background queues, Petcube video, curriculum and education, interaction meanings dictionary, onboarding questionnaire, research surveys, the admin web console, Metabase dashboards, CSV reports, household analytics segments, outbound email.

## 2. Users and roles

| Role | How identified | What they can do |
|---|---|---|
| Member | Firebase ID token | Everything within their own household |
| Household admin | The user who created the household | Invite and remove members, plus member rights |
| Admin | Firebase custom claim `admin: true` | Impersonate any user with the `X-Login-As` header; every use is logged |
| Device script | Static key in `X-Device-Key` header | Only the `/device/*` routes |

A user belongs to exactly one household at a time. Signing in for the first time creates the user, a household, a human pusher named after the user, and one default button named `inaudible`.

## 3. Tech stack

All versions are the latest stable as of 2026-09-16, verified against PyPI and endoflife.date.

| Layer | Choice | Version |
|---|---|---|
| Runtime | Python | 3.14 |
| Framework | FastAPI | 0.141.1 |
| Server | Uvicorn | 0.53.0 |
| Validation, settings | Pydantic, pydantic-settings | 2.13.5, 2.15.0 |
| Database | PostgreSQL | 18 |
| ORM, driver | SQLAlchemy (async), asyncpg | 2.0.54, 0.31.0 |
| Migrations | Alembic | 1.20.0 |
| Auth and push | firebase-admin | 7.5.0 |
| Object storage client | boto3 against Cloudflare R2 | 1.43.95 |
| Outbound HTTP | httpx | 0.28.1 |
| CLI for cron jobs | Typer | 0.27.2 |
| Multipart uploads | python-multipart | 0.0.32 |
| Errors | sentry-sdk | 2.69.2 |
| Package manager | uv | 0.12.15 |
| Lint and format | ruff | 0.16.7 |
| Tests | pytest, pytest-asyncio | 9.1.1, 1.4.0 |

Deliberately excluded: Redis, Celery or any queue, WebSockets, Datadog, Terraform, ECS, EFS, SMTP.

## 4. Architecture

```
Android app ──Firebase ID token──▶ Cloud Run: fluentpet-api ──▶ Postgres (Neon or Cloud SQL)
                                          │                  ──▶ Cloudflare R2 (audio, avatars)
                                          │                  ──▶ FCM (push)
Device script ──X-Device-Key──▶ /device/* ┘
(subscribes to base MQTT elsewhere; calls the API over HTTPS)

Cloud Scheduler ──hourly──▶ Cloud Run Job: base-offline-check
```

**Hosting.** One container image serves both the API (Cloud Run service, min instances 0, max 3, 512 MiB, concurrency 80) and the cron command (Cloud Run Job triggered by Cloud Scheduler). Secrets live in GCP Secret Manager and are mounted as environment variables.

**Database.** Neon Postgres 18 on the free or Launch tier is the default because it scales to zero and costs nothing at this traffic. Cloud SQL Postgres 18 (db-f1-micro or Enterprise smallest) is the alternative if the client wants everything inside GCP. The code does not care which; only `DATABASE_URL` changes.

**Storage.** One R2 bucket with prefixes `audio/`, `avatars/`. R2 is chosen for zero egress fees, which matters because bases download audio clips. All reads go through short-lived presigned GET URLs. Uploads go through the API, which validates and writes with a presigned PUT or a direct put.

**AWS.** Not required. If outbound email is ever added, AWS SES is the cheapest transport and would be the one AWS service in play.

**Firebase.** One project provides Auth (email/password, Google sign-in) and Cloud Messaging. The backend uses a single service account.

**Background work.** Push sends and button webhooks run in FastAPI `BackgroundTasks` after the response is sent. There is one scheduled job. Nothing else needs a worker.

## 5. Data model

Nineteen tables. All ids are `bigint identity`. All timestamps are `timestamptz`. Soft delete (`deleted_at`) exists only on `interactions`, `notes`, and `buttons`, where the product has undo; everything else hard-deletes.

### Identity and household

- **households** — `id`, `name` (auto `FLUENT` + 5 digits, unique), `created_at`
- **users** — `id`, `firebase_uid` (unique), `email` (unique, lowercased), `full_name`, `timezone` (IANA, default UTC), `app_version`, `household_id`, `is_household_admin` (bool), `created_at`
- **household_invitations** — `id`, `household_id`, `inviting_user_id`, `email` (lowercased), `status` (`pending` | `accepted` | `rejected`), `created_at`, `expires_at` (72 hours). Unique on (`household_id`, `email`) where pending.
- **impersonation_logs** — `admin_user_id`, `target_user_id`, `method`, `path`, `count`, `window_start`

### Learners

- **learner_types** — `id`, `name` (seeded: dog, cat, other)
- **pushers** — `id`, `household_id`, `name`, `is_human`, `is_hidden`, `birth_date`, `sex`, `learner_type_id`, `sub_type`, `country`, `language`, `training_started_at`, `avatar_key` (R2), `interactions_count`. Unique on (`household_id`, lower(`name`)).

### Buttons and audio

- **button_concepts** — `id`, `concept` (seeded reference list)
- **audios** — `id`, `household_id`, `name`, `storage_key`, `crc32`, `byte_size`, `created_at`
- **buttons** — `id`, `household_id`, `text`, `word`, `normalized_word`, `introduced_at`, `button_concept_id`, `is_hidden`, `note`, `origin` (`app` | `connect`), `audio_id`, `webhook_url`, `deleted_at`. Unique on (`household_id`, lower(`text`)) where not deleted.
- **webhook_logs** — `button_id`, `url`, `status_code`, `requested_at`, `responded_at`

### Devices

- **bases** — `id`, `household_id`, `serial_number` (unique, 12 chars), `name`, `default_pusher_id`, `group_window_seconds` (default 15, range 0–3600), `fw_version`, `battery_level`, `battery_updated_at`, `last_online_at`, `reported_state` (jsonb, written by the device script), `created_by_user_id`, `created_at`
- **base_buttons** — `id`, `base_id`, `button_id` (unique), `button_serial_number` (uppercase, ≥3 chars, not all zeros, unique per household), `battery_level`, `battery_updated_at`, `last_online_at`, `desired_audio_id`, `desired_deleted` (bool), `desired_version` (int, bumps on every desired change), `applied_version` (int, set by the script)
- **device_events** — `id`, `base_id`, `button_serial_number`, `event_type`, `payload` (jsonb), `occurred_at`, `received_at`. Raw audit of what the script delivered. Unique on (`base_id`, `event_type`, `occurred_at`, `button_serial_number`) for idempotent delivery.

### Interactions

- **contexts** — `id`, `household_id` (null = global), `text`, `applies_to` (`human` | `learner` | `both`). Seeded globals include `Modeled` (human only). Unique on (`household_id`, lower(`text`)).
- **interactions** — `id`, `household_id`, `pusher_id` (null = unassigned), `note`, `occurred_at`, `device_timezone`, `origin` (`app` | `base` | `split`), `is_favourite`, `is_hidden`, `num_presses`, `duration_seconds`, `created_by_user_id`, `created_by_base_id`, `deleted_at`, `created_at`, `updated_at`. Index on (`household_id`, `occurred_at desc`) where not deleted and not hidden.
- **button_presses** — `id`, `interaction_id`, `button_id`, `press_order`, `occurred_at` (null when entered by hand), `reported_timestamp`. Unique on (`interaction_id`, `press_order`).
- **interaction_contexts** — (`interaction_id`, `context_id`) primary key
- **modeled_pushers** — (`interaction_id`, `pusher_id`) primary key
- **notes** — `id`, `household_id`, `text`, `occurred_at`, `device_timezone`, `is_favourite`, `is_hidden`, `created_by_user_id`, `deleted_at`, `created_at`

### Preferences and push

- **preferences** — (`user_id`, `key`) primary key, `value` (jsonb). Allowed keys: `activity_sort`, `button_sort`, `feature_flags`, `default_pusher_id`, `push_frequency` (`all` | `on_interaction` | `none`).
- **push_tokens** — `token` (primary key, FCM registration token), `user_id`, `platform`, `updated_at`
- **push_log** — `id`, `user_id`, `household_id`, `base_id`, `key`, `sent_at`, `error`. Index on (`user_id`, `key`, `sent_at`) for rate limiting.

## 6. API surface

Base path `/api/v1`. JSON only. Timestamps are ISO 8601 with offset. Booleans are real booleans. Lists take `page` (1-based) and `per_page` (default 45, max 200) and return `{ "items": [], "total": n, "page": n, "per_page": n }`. Errors return `{ "error": { "code": "not_found", "message": "..." } }` with the matching HTTP status. OpenAPI is served at `/docs`.

### Me and household

| Method | Path | Notes |
|---|---|---|
| GET | `/me` | User, household, pushers, feature flags. Creates user and household on first call |
| PATCH | `/me` | `full_name`, `timezone`, `app_version` |
| DELETE | `/me` | Deletes the user, their pusher, and their Firebase account. Blocked if they are admin of a household with other members. Required by Play Store policy |
| GET | `/household` | Household, members, pending invitations |
| POST | `/household/leave` | Not allowed for admin unless sole member |
| DELETE | `/household/members/{user_id}` | Admin only. Member returns to a fresh household |
| GET | `/household/invitations` | Sent (admin) and received (by my email) |
| POST | `/household/invitations` | Admin only. Body `{ email }`. Idempotent |
| DELETE | `/household/invitations/{id}` | Admin only |
| POST | `/household/invitations/{id}/accept` | Moves me to that household; rejects all my other pending invitations |
| POST | `/household/invitations/{id}/reject` | |

Invitations are delivered in-app, not by email: when a user signs in, `GET /me` and `GET /household/invitations` surface any pending invitation addressed to their email.

### Pushers

| Method | Path | Notes |
|---|---|---|
| GET | `/pushers` | `include_hidden` flag. Ordered by `interactions_count desc` |
| POST | `/pushers` | Name unique per household; a hidden pusher with the same name is un-hidden instead of erroring |
| GET | `/pushers/{id}` | |
| PATCH | `/pushers/{id}` | Including `is_hidden`. Hiding clears it as any base's default pusher and the user's `default_pusher_id` preference |
| DELETE | `/pushers/{id}` | Interactions keep `pusher_id` null afterward |
| PUT | `/pushers/{id}/avatar` | Multipart image ≤ 2 MiB, stored in R2 |
| GET | `/pushers/{id}/stats` | Most and least pressed buttons, top contexts, most frequent combination, days since first entry |
| GET | `/learner-types` | |

### Buttons and audio

| Method | Path | Notes |
|---|---|---|
| GET | `/buttons` | `sort` = `alphabet` \| `frequency` \| `date`, `include_hidden` |
| POST | `/buttons` | App buttons only. Duplicate text un-hides the existing button |
| GET | `/buttons/{id}` | Includes base link and press count |
| PATCH | `/buttons/{id}` | `text`, `note`, `introduced_at`, `button_concept_id`, `is_hidden`, `audio_id`, `webhook_url`. Setting `audio_id` on a linked button bumps `desired_version`. Hiding a linked button unlinks it |
| DELETE | `/buttons/{id}` | Soft delete; unlinks from base |
| POST | `/buttons/merge` | `{ source_id, target_id }`. Moves presses, base link, and audio to target; deletes source |
| POST | `/buttons/{id}/unlink` | Removes the base link and marks `desired_deleted` |
| GET | `/button-concepts` | |
| GET | `/audios` | |
| POST | `/audios` | Multipart Ogg Opus, ≤ 4096 bytes, 16 kHz. Server computes CRC32 and stores at `audio/h{household}/{id}.ogg` |
| DELETE | `/audios/{id}` | Buttons referencing it get `audio_id` null and a desired bump |
| GET | `/audios/{id}/url` | Presigned GET, 15 minutes |

### Bases

| Method | Path | Notes |
|---|---|---|
| GET | `/bases` | Includes `reported_state`, battery, `last_online_at`, and linked buttons |
| POST | `/bases` | `{ serial_number, name }`. Serial must match the 12-character format. If the serial is registered to another household it is transferred: old links removed, new record under mine. Push `base_registered` |
| PATCH | `/bases/{serial}` | `name`, `default_pusher_id`, `group_window_seconds` |
| DELETE | `/bases/{serial}` | Removes base and links. Push `base_removed` |

### Interactions and notes

| Method | Path | Notes |
|---|---|---|
| POST | `/interactions/search` | Body: `page`, `per_page`, `sort` (`occurred_at_desc` \| `occurred_at_asc` \| `created_at_desc`), `tab` (`all` \| `assigned` \| `unassigned`), `filters`. Returns items plus `counts { communication, modeling, unassigned }` |
| POST | `/interactions` | `pusher_id`, `note`, `occurred_at`, `device_timezone`, `is_favourite`, `button_ids[]` in press order, `context_ids[]`, `modeled_pusher_ids[]` |
| GET | `/interactions/{id}` | |
| PATCH | `/interactions/{id}` | Same fields. Replacing `button_ids` reuses existing press rows where the button matches and keeps device timestamps |
| DELETE | `/interactions/{id}` | Soft delete |
| POST | `/interactions/{id}/merge` | `{ interaction_ids[] }`. See rules |
| POST | `/interactions/{id}/split` | One interaction per press. Rejected if any press lacks a device timestamp |
| POST | `/interactions/bulk` | `{ operation: assign \| delete \| merge, ids[] \| all: true, pusher_id? }` |
| POST | `/notes` | `text`, `occurred_at`, `device_timezone` |
| PATCH | `/notes/{id}` | |
| DELETE | `/notes/{id}` | Soft delete |
| GET | `/contexts` | `kind` = `teacher` \| `learner` \| `custom` |
| POST | `/contexts` | Custom context for my household |
| GET | `/stats/summary` | `pusher_id`, `from`, `to` (dates in the user's timezone, range ≤ 183 days). Buttons logged, buttons created, combinations, contexts, per day, per hour of day, totals |

**Search filters** (all optional): `pusher_ids[]`, `context_ids[]`, `button_ids[]`, `base_ids[]`, `match` (`any` \| `all`, applies to contexts and buttons), `text`, `from`, `to`, `notes` (`include` \| `only` \| `exclude`), `with_note` (same three values), `favourites` (same), `presses` (`all` \| `single` \| `multiple`), `include_hidden`. The response mixes interactions and notes ordered together; each item carries `type`.

### Preferences and push

| Method | Path | Notes |
|---|---|---|
| GET | `/preferences` | |
| PUT | `/preferences/{key}` | Key must be in the allowed list |
| PUT | `/push-tokens` | `{ token, platform }`. A token moves to the calling user if it was registered to someone else |
| DELETE | `/push-tokens/{token}` | |

### Device routes

Authenticated by `X-Device-Key`. Not visible in the public OpenAPI.

| Method | Path | Notes |
|---|---|---|
| POST | `/device/events` | Array of events, each `{ serial_number, button_serial_number?, type, occurred_at, payload }`. Types: `press`, `button_seen`, `battery`, `power`, `fully_charged`, `online`. Idempotent on the unique key. Returns per-event `created` or `duplicate` |
| PUT | `/device/bases/{serial}/state` | `{ reported_state, fw_version?, battery_level? }`. Sets `last_online_at` |
| GET | `/device/desired` | Base buttons where `desired_version > applied_version`, with audio presigned URL and CRC when `desired_audio_id` is set |
| POST | `/device/desired/ack` | `[{ base_button_id, applied_version }]` |
| POST | `/device/audio-url` | `{ serial_number, audio_id }`. Presigned URL plus CRC as decimal, which the firmware expects. 403 if the audio is not in the base's household |
| GET | `/healthz` | Database ping only. Public |

## 7. Business rules

**First sign-in.** Verify the Firebase token. Find the user by `firebase_uid`, else create user, household, human pusher named `full_name` (or the email local part), and the `inaudible` button, all in one transaction. The creator is the household admin.

**Press grouping from bases.** For each `press` event: clamp `occurred_at` to now if it is in the future; treat values ≥ 2×10⁹ as milliseconds. Find the most recent base-origin press for this base within `group_window_seconds`. If found, insert this press into that interaction in chronological position and renumber `press_order`. Otherwise create a new interaction with `origin = base`, `pusher_id = base.default_pusher_id` or the household's `default_pusher_id` preference, and `device_timezone` from the base owner. A press with the same button and same second as an existing press is a duplicate. Recompute `num_presses` and `duration_seconds` on every change.

**Unknown button serial.** A `press` or `button_seen` event whose `button_serial_number` is not linked on that base creates a new button (`origin = connect`, text `Button {serial}` made unique) and a `base_buttons` row. The same serial in a different household is a different button.

**Modeling.** After any interaction write: if the pusher is human, ensure the `Modeled` context is attached and, if the household has exactly one non-hidden learner, add that learner as the modeled pusher. If the pusher is a learner or null, remove `Modeled` and all modeled pushers. Contexts whose `applies_to` conflicts with the pusher kind are removed.

**Merge.** All interactions must belong to the caller's household. Target keeps the earliest `occurred_at`. Presses are renumbered by (`interaction occurred_at`, `press_order`, `press occurred_at`). Contexts and modeled pushers are unioned. Sources are soft-deleted. Counters recomputed.

**Split.** Requires every press to have `occurred_at`. Creates one interaction per press with `origin = split`, copying pusher, contexts, and modeled pushers; the original is soft-deleted.

**Button text.** Trim, collapse whitespace, strip emoji for matching. `word` is the text without a trailing parenthetical and without `!?.`; `normalized_word` applies a small synonym map (walkies→walk, eat→food, etc.) kept as a Python dict.

**Base transfer.** Registering a serial owned by another household unlinks its buttons there and creates the base under the caller. Both households get a push.

**Button webhooks.** When a base press creates a new press row and the button has `webhook_url`, issue one GET with a 5-second timeout after the response is sent, retry twice, and record the outcome in `webhook_logs`.

**Push notifications.** Keys and triggers:

| Key | Trigger | Rate limit |
|---|---|---|
| `base_registered`, `base_removed` | base create, transfer, delete | none |
| `base_battery_low` | battery event below 20% while not charging | 1 per user per 24 h |
| `base_fully_charged` | `fully_charged` event | 1 per user per 24 h |
| `base_offline` | hourly job: `last_online_at` between 36 h and 168 h ago and no `base_offline` push in that window | once per outage |
| `button_linked`, `button_unlinked` | base_buttons create and delete | none |
| `button_pressed` | new press from a base; respects `push_frequency` (`all` sends every press, `on_interaction` sends only the first press of an interaction, `none` sends nothing) | none |

Sends go to all household members' tokens through FCM multicast in chunks of 500. Tokens that return `Unregistered` or `SenderIdMismatch` are deleted. Every attempt is written to `push_log`.

**Impersonation.** `X-Login-As: <email>` is honored only when the caller's Firebase token carries the admin claim. The request runs as that user. Each (admin, target, method, path) is counted per hour in `impersonation_logs`.

## 8. Non-functional requirements

**Security.** Firebase ID tokens verified on every request with revocation checks off (cheaper; sessions expire within an hour anyway). Credentials accepted only from headers. Every query is scoped by `household_id` from the authenticated user, never from the request body. Device key compared with `secrets.compare_digest`. Uploads validated by size and magic bytes before they reach R2. No stack traces in responses. Dependency audit with `uv pip audit` in CI.

**Performance.** P95 under 300 ms for the search endpoint with 50k interactions per household. Feed queries use one SQL statement with window functions and `GROUP BY ... HAVING` for press-count filters; no per-filter round trips and no in-memory set intersection. Connection pool of 5 per instance, which fits Neon's free tier when Cloud Run maxes at 3 instances.

**Cost target.** Under 5 USD per month at up to 1,000 monthly active households: Cloud Run scale-to-zero, Neon free tier, R2 free tier (10 GB, no egress), Firebase Spark plan, Sentry developer plan.

**Observability.** JSON logs to stdout with `request_id`, `user_id`, `household_id`, latency. Cloud Logging picks them up. Sentry for exceptions. `/healthz` for Cloud Run health checks.

**Testing.** Endpoint tests with httpx against a real Postgres (Docker locally, a Neon branch in CI). The press-grouping, merge, split, and modeling rules each get direct unit tests. CI runs ruff, tests, and builds the image on every pull request; deploy to Cloud Run on merge to `main`.

**Environments.** `dev` (Neon branch, Firebase dev project) and `prod`. Configuration is entirely environment variables: `DATABASE_URL`, `FIREBASE_CREDENTIALS_JSON`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `DEVICE_API_KEY`, `SENTRY_DSN`, `ENV`.

## 9. Repository layout

```
fluentpet-api/
  app/
    main.py            app factory, routers, exception handlers, middleware
    settings.py        pydantic-settings
    db.py              async engine, session dependency
    auth.py            Firebase verification, current_user, login-as, device key
    models.py          SQLAlchemy tables
    schemas/           Pydantic request and response models, one file per resource
    routers/           one file per resource plus device.py
    services/
      interactions.py  search query builder, create/update, merge, split, recompute
      grouping.py      base press grouping
      buttons.py       text normalization, merge, unlink
      push.py          FCM sender and rate limit
      storage.py       R2 client, presign, upload validation
      webhooks.py      button webhook delivery
    jobs/
      base_offline.py  Typer command
  alembic/
  tests/
  Dockerfile
  pyproject.toml
  cloudrun.yaml
```

## 10. Milestones

| # | Deliverable | Scope |
|---|---|---|
| 1 | Skeleton | Settings, DB, Alembic initial migration, Firebase auth dependency with first-sign-in provisioning, error envelope, `/healthz`, CI, Cloud Run deploy of `dev` |
| 2 | People | `/me`, household, invitations, pushers with avatars, preferences, push tokens |
| 3 | Boards | Buttons, concepts, audios on R2, bases, base buttons |
| 4 | Logging | Interactions, notes, contexts, search, merge, split, bulk, stats |
| 5 | Devices | `/device/*`, press grouping, push sender, webhooks, offline job, device script contract test |
| 6 | Launch | Prod project, Firebase prod, Sentry, load test of search, handoff doc |

Each milestone ends with endpoint tests green and a deploy to `dev`.

## 11. Open questions

1. Google sign-in only, or also email/password in Firebase Auth? Affects nothing in the backend but must be decided before the app ships.
2. Does the device script need raw base logs stored, or are typed events enough? The PRD assumes typed events only; `device_events.payload` can carry the raw line if wanted.
3. Should the 183-day cap on stats stay, or is the new app fine with a 90-day cap for cheaper queries?
4. Confirm the Play Store account-deletion flow: in-app `DELETE /me` is sufficient, but a web URL for deletion requests is also required by policy and would be a static page, not an API concern.

## 12. AI features

Added 2026-09-19. Three features, each one call to the Claude API made from inside a request handler or the job command. Same shape as the rest of the service: no queue, no worker, no new hosting. **Not MCP** (the model call runs in-process, next to the DB; tools are Python functions) and **not RAG** (a household's data is structured and small; the model fetches it through tools, not embeddings).

### 12.1 Stack additions

| Layer | Choice | Version |
|---|---|---|
| Model client | `anthropic` Python SDK, `AsyncAnthropic`, beta tool runner | 1.7.0 (2026-09-18) |
| Model | `claude-opus-5`; env `AI_MODEL` overrides (model is the main cost lever, see 12.7) | — |

New environment variables: `ANTHROPIC_API_KEY` (blank = every AI endpoint answers 503 `ai_unavailable`, the app must work fully without it), `AI_MODEL` (default `claude-opus-5`). Stored in SSM like the other secrets.

Client settings: `timeout=30`, `max_retries=1` (worst case 60 s wall clock). Prompt caching on the system block. Adaptive thinking (the model default), `effort: low` for chat and log-by-text, `medium` for the digest.

### 12.2 Data model

One table.

- **ai_log** — `id`, `user_id` (null for the digest), `household_id`, `kind` (`chat` | `log_text` | `digest`), `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `latency_ms`, `created_at`. Index on (`user_id`, `kind`, `created_at`) for rate limiting and on (`household_id`, `kind`, `created_at`) for the digest. One row per successful model call, written in the request transaction; a failed call is a 503 that rolls the request back, so failures are logged (and go to Sentry), not stored. This is also the spend meter: `sum(tokens) by model, month`.

No chat history table: the app sends the thread back on every turn (the Messages API is stateless anyway). No stored drafts: log-by-text returns a draft the app posts through the existing `POST /interactions`.

### 12.3 API

| Method | Path | Notes |
|---|---|---|
| POST | `/ai/chat` | Body `{ messages: [{ role: user \| assistant, content }] }`, ≤ 20 messages, last one `user`, each ≤ 2,000 chars. Returns `{ reply, remaining_today }`. 429 `rate_limited` after 30 turns per user per rolling 24 h |
| POST | `/ai/log-text` | Body `{ text }` ≤ 1,000 chars. Returns `{ draft: { pusher_id, button_ids[], context_ids[], occurred_at, note }, unmatched_words[] }`. Draft only: the app shows it, the user confirms, the app calls `POST /interactions`. 429 after 50 per user per 24 h |
| POST | `/internal/weekly-digest` | `X-Job-Key`. Triggered by hand like `base_offline`. Returns `{ households: n, sent: n, skipped: n }` |

Errors: 503 `ai_unavailable` for any Anthropic error, timeout, or blank key (logged to Sentry); 422 for body validation as everywhere else. Household scoping comes from the token: tools receive the authenticated `user`, never ids from the model.

### 12.4 Chat

System prompt (cached): today's date in the user's timezone, the household's pushers (id, name, human/learner, type), the non-hidden button vocabulary (id, text), the contexts, and rules: answer only from tool results, never invent presses, keep replies under 120 words, reply in the user's language, say so when a question is outside the data.

Tools (`@beta_async_tool`, wrapping existing functions; the model sees names and docstrings only):

| Tool | Wraps | Bounds |
|---|---|---|
| `stats_summary(pusher_id, from, to)` | `routers/stats.py::summary` | ≤ 183 days; lists trimmed to top 10 |
| `search_interactions(pusher_id?, button_ids?, context_ids?, from?, to?, text?, limit)` | `routers/search.py` with `SearchIn` | `limit` ≤ 50, one compact line per row: `09-17 07:42 Rex: OUTSIDE, PLAY [Morning] "note"` |

Loop: `client.beta.messages.tool_runner`, at most 5 tool rounds, `max_tokens` 1,024 for the reply. "Summarise the last few days" is one `stats_summary` call; "when does Rex ask for outside at night" is one `search_interactions` call. A reply that arrives without tool use is fine (greetings, clarifications).

### 12.5 Log by text

Input: free text such as *"Rex pressed outside then play, we went to the park around 8"*. Prompt: pushers, buttons, contexts (ids and text), now in the user's timezone. Output through structured outputs (`messages.parse` against the draft schema, `strict`), so the response always validates. The server then drops any id that is not in the household's lists (a hallucinated id has no word to report); `unmatched_words` comes from the model and the app offers "create button" for those. A time without an offset is read on the user's clock; no time means now. `occurred_at` null means now; relative phrases ("this morning", "around 8") resolve against the user's timezone. Nothing is written.

### 12.6 Weekly digest

For each household with ≥ 1 non-deleted interaction in the last 7 days and no `ai_log` row of kind `digest` in the last 6 days: build the week's `stats_summary` per non-hidden learner plus the previous week's for comparison, one model call, ≤ 3 sentences (*"Rex said OUTSIDE 12 times this week, mostly 7–8 am, up from 5. New word: LOVE, first pressed Tuesday."*). Deliver as a push `weekly_digest` to all members (new key, no rate limit beyond once per household per week, ignores `push_frequency` because it is not a press) and as a note (`created_by_user_id` null, text prefixed `Weekly digest — `) so it lives in the feed. Households without learners or with fewer than 3 interactions get no digest.

### 12.7 Cost

Per-call estimates at list prices, cached system prompt, `effort: low`:

| Call | Tokens in / out | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|---|
| Chat turn (one tool round) | ~6.5k / 300 | $0.04 | $0.016 | $0.008 |
| Log by text | ~1.5k / 100 | $0.01 | $0.004 | $0.002 |
| Digest, per household | ~2k / 150 | $0.014 | $0.006 | $0.003 |

At 100 active households, 20 % using chat 10 turns a week, every household getting a digest: Opus ≈ $38 / month, Sonnet ≈ $15, Haiku ≈ $7. **This sits on top of the $5 infrastructure target and scales with users; the model choice and the per-user caps are the controls.** `AI_MODEL` switches without a code change. Decision on the launch model is open (12.9).

### 12.8 Non-functional

- **Security.** Tools never accept ids from the model that are not checked against the household through the existing `get_pusher`-style loaders. Model output is never executed or written directly; log-by-text produces a draft, the digest text is stored as-is in a note. Prompt content the user controls (chat text, note text) is treated as data.
- **Privacy.** Pusher names, button texts, notes and timestamps are sent to Anthropic's API under its commercial terms (not used for training by default; verify the current policy before launch). No emails or Firebase ids leave. The app shows a one-time consent screen before the first AI call and records it client-side; the Play Store privacy policy gets one line naming the processor.
- **Concurrency.** An AI request holds its pooled DB connection, idle in transaction, for the length of the model call (5–10 s). With `pool_size=5` this caps concurrent AI requests at about 4 before other requests queue. Accepted for launch; the upgrade path is tools opening their own `SessionLocal()` and the endpoint releasing the request session before the model call.
- **Observability.** Every successful call writes `ai_log`; failures are a warning log line with the SDK error class; request log line unchanged.
- **Testing.** The Anthropic client is faked at its boundary in `tests/conftest.py` (`claude` fixture) like `fcm` and `s3`; endpoint tests cover the happy path, the 429, the blank-key 503, id validation in log-by-text, and once-per-week for the digest. The two tool functions get one direct test each proving household scoping. No live API calls in CI.

### 12.9 Milestones

| # | Deliverable | Scope |
|---|---|---|
| 7 | AI foundation + log by text | SDK, settings, `ai_log` migration, client fake, `/ai/log-text`, rate limit |
| 8 | Chat | `/ai/chat`, the two tools, prompt caching, tool-round cap |
| 9 | Digest | `/internal/weekly-digest`, push key `weekly_digest`, digest note |

Open before milestone 7: launch model (`claude-opus-5` unless changed), whether the digest is weekly or daily (daily is 7× the cost).

### 12.10 Backlog (not in scope)

Explain-this-interaction, next-button suggestion, auto `button_concept_id` on create, natural-language search → `SearchFilters`, streaming replies, stored chat threads, per-user AI gating. MCP and RAG stay out for the reasons above; revisit MCP only if an external client (Claude Desktop, a third-party agent) needs the data, RAG only if free-text notes outgrow tools — and then Postgres FTS before pgvector.
