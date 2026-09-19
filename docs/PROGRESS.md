# FluentPet API — progress against the PRD

Status on 2026-09-19: **all six PRD milestones delivered and live in production.**
100 endpoint tests green (`uv run pytest`, ~7 s), ruff + pip-audit clean, CI deploys every push
to `main`. Prod URL: `https://13-214-182-0.sslip.io` (changes if the EC2 instance is stopped and
started — see "Hosting" below).

Companion docs: `PRD.md` (spec), `docs/HANDOFF.md` (architecture, rules, decisions, corners),
`docs/AWS_SETUP.md` (the cloud setup exactly as executed), `README.md` (run it locally).

## Milestones (PRD §10)

| # | Deliverable | Status | What shipped |
|---|---|---|---|
| 1 | Skeleton | Done | pydantic-settings, async SQLAlchemy 2 + Alembic (3 migrations), Firebase auth with first-sign-in provisioning, JSON error envelope, `/healthz` (no DB touch), JSON request logs with `request_id`/`user_id`/latency, CI (ruff, pip-audit, tests, image, migrate, deploy) |
| 2 | People | Done | `/me` (get/patch/delete incl. Firebase user deletion), household + members + invitations (create/accept/reject/delete/leave), pushers with R2 avatars, preferences, push tokens, `X-Login-As` for admins with impersonation log |
| 3 | Boards | Done | buttons (normalisation, hide, merge, unlink, webhook URL with SSRF guard, webhook logs), button concepts, Ogg Opus audios on R2 with presigned URLs, bases + linked base buttons with desired/applied versions |
| 4 | Logging | Done | interactions (create/patch/delete, presses, contexts, modeling), notes, teacher/learner/custom contexts, search (tabs, filters, text, paging, counts), merge, split, bulk assign/delete/merge, per-pusher stats, `/stats/summary` (totals, per-day, per-hour, combinations, contexts) |
| 5 | Devices | Done | `/device/*` with `X-Device-Key`: state sync, desired state, press/battery/online events, base press grouping window, FCM push sender with per-key rate limits, webhook delivery, `base_offline` job (HTTP-triggered), device-script contract test |
| 6 | Launch | Done | prod on AWS (EC2 + Neon + R2 + Firebase), CI/CD via GitHub OIDC, secrets in SSM Parameter Store, Sentry wired (DSN optional), search load test, smoke test script, seed for real accounts, handoff docs |

## What is running in production

| Piece | Where | Notes |
|---|---|---|
| API | 1× EC2 `t3.micro`, `ap-southeast-1`, `docker compose` (API + Caddy) | `deploy/ec2.sh` is the whole server config; Caddy gets a Let's Encrypt cert for `<ip>.sslip.io` |
| Database | Neon Postgres, Singapore, free tier | schema migrated from CI (`alembic upgrade head`) on every push |
| Files | Cloudflare R2 bucket `fluentpet` | 15-min presigned URLs; verified with a real avatar upload + fetch |
| Auth + push | Firebase project `fluentpet-remake` | ID tokens verified per request; FCM via Admin SDK |
| Secrets | SSM Parameter Store `/fluentpet/prod/*` | read by the box at deploy; rewritten into `.env` |
| Deploy | GitHub Actions → ECR → SSM Run Command on the box | red/green visible per push; `deploy/ec2.sh` re-runs itself at boot |
| Errors | Sentry | on as soon as `SENTRY_DSN` is set; blank = off |

Cost after the AWS credits: ~$14/month (EC2 + IPv4 + disk); everything else is on free tiers.

## Verified on prod (2026-09-18)

* `scripts/smoke.sh <url> .env.prod` — 9/9: health, auth envelope, dev tokens refused,
  device key → Neon round-trip, job endpoint, `/internal` hidden from OpenAPI, valid TLS.
* Real Firebase sign-in (test user `tgso…`, email/password): `/me`, search (1,870 items),
  buttons, bases.
* Full create → read → update → delete pass on every resource as that user, including R2 avatar
  upload, webhook SSRF rejection, merge, search filters/tabs, bulk ops, stats, invitations,
  bases — then the seeded data was back to exactly 1,870 items.
* Seeded test household: `scripts/seed.py --uid … --days 180 --per-day 10` → 1,834 interactions,
  12 buttons, base `FPB000000001` with 3 linked buttons, notes, preferences, an open invitation.

## Where we deviated from the PRD, and why

| PRD said | We did | Why |
|---|---|---|
| GCP Cloud Run | AWS EC2 (App Runner was the first choice) | $100 AWS credits; App Runner turned out to be unavailable on new free-plan AWS accounts without an irreversible account conversion; Lambda/Cloud Run request billing were out because pushes and webhooks run after the response |
| Scheduled `base_offline` job | `POST /api/v1/internal/base-offline` triggered by hand from a laptop | your call: no cron in the cloud for now; EventBridge can be added later |
| `dev` + `prod` environments | `prod` only; local `docker compose` is dev | one small box; a `dev` box is a copy of the same steps |
| Google Secret Manager | SSM Parameter Store | free; Secrets Manager is $0.40/secret |
| Single window-function search query | a few plain statements | measured well under the 300 ms p95 budget, so it stayed simple |
| `uv pip audit` | `pip-audit` on the exported lock | same check |
| `cloudrun.yaml`, Typer job command | `deploy/ec2.sh`, plain `python -m app.jobs.base_offline` | fewer moving parts |

Other decisions are listed in `docs/HANDOFF.md` → "Decisions taken while building".

## Numbers

* 100 endpoint tests, 25 test files, real Postgres, no DB assertions — HTTP and rule functions only.
* Search load test (50k interactions, laptop, Docker Postgres): worst p95 143 ms (text search)
  vs. 300 ms budget; feed pages ~105 ms p95. Not yet re-run against Neon.
* Seed at 180 days × ~10/day: 1,834 interactions in 5 s locally, ~15 s on the box against Neon.

## Timeline

| Date | What |
|---|---|
| ≤ 2026-09-17 | Milestones 1–5 built test-first; initial commit |
| 2026-09-17 | GitHub OIDC → AWS (trust policy needs the new `repo:org@ID/repo@ID` subject), first Neon migration from CI, `DATABASE_URL` accepted as Neon prints it, App Runner found unavailable → EC2 + Caddy; first prod deploy; smoke test 9/9; `HANDOFF` updated |
| 2026-09-17 | Seed script grown to `--days/--per-day/--email/--uid`; test user's household seeded on prod |
| 2026-09-18 | Instance stop/start → boot-time redeploy added; missing `FIREBASE_CREDENTIALS_JSON` parameter found via the new token-rejection log and fixed; real-auth e2e pass on every endpoint |

## Open items (none block the app)

PRD §11 open questions, still yours to decide:

1. Google-only vs email/password sign-in — Firebase console only; the test user uses email/password today.
2. Raw base logs vs typed device events — typed only, `device_events.payload` can carry the raw line.
3. 183-day vs 90-day stats cap — 183 kept.
4. Play Store account-deletion web page — static page; `DELETE /me` does the work.

Operational follow-ups, in the order I'd do them:

* Elastic IP (same price as the auto IP) + a real domain in `deploy/ec2.sh` — sslip.io shares one
  Let's Encrypt rate limit with everyone; do this before real users.
* Delete the leftovers in AWS: IAM roles `fluentpet-apprunner-*`, SSM parameters
  `FIREBASE_CREDENTIALS_JSON` (no path) and `/fluentpet/prod` (if not already done).
* Re-run `scripts/loadtest.py` against Neon once; delete the `load@example.com` household after.
* Set `SENTRY_DSN` when you want error reporting.
* App-side: point the app at the prod URL, sign in, and try the Opus audio upload path (the only
  endpoint not exercised with a real file yet).
