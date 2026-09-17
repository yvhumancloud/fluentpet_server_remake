# FluentPet API

FastAPI backend. Spec: `PRD.md` in the Rails repo. Start with `docs/HANDOFF.md`.

## Local

```sh
cp .env.example .env
docker compose up -d --build     # Postgres 18 on :5432 (dev + test DBs), API on :8080 with reload
uv sync && uv run pytest         # tests hit the compose Postgres (fluentpet_test)
```

`docker compose up db` alone if you'd rather run the API with `uv run uvicorn app.main:app --reload`.

### Demo data for e2e

```sh
PYTHONPATH=. uv run python scripts/seed.py                      # wipes + recreates the demo households
PYTHONPATH=. uv run python scripts/seed.py --days 180 --per-day 10  # ~1,800 interactions, 5 s
curl -H "Authorization: Bearer ann" localhost:8080/api/v1/me
```

Prod has no dev tokens: sign in from the app once, then fill *your* household from the EC2 box
(same region as Neon, secrets already in the container):

```sh
cd /opt/fluentpet && sudo docker compose exec api python -m scripts.seed --email you@gmail.com --days 180 --per-day 10
```

It wipes and rebuilds that one household (you stay admin, Firebase uid unchanged); re-run any time.

`DEV_TOKENS` in `.env` makes `Bearer ann|bob|dan` stand in for Firebase (refused when `ENV=prod`).
Ann is the admin of a household with Bob, learners Rex and Tom, 12 buttons, a base `FPB000000001`
with three linked buttons, `--days` of interactions and notes; Dan has an empty household.
Device calls use `X-Device-Key` from `.env`.
Tests re-run migrations from scratch each session; override the DB with `TEST_DATABASE_URL`.

## Deploy

One EC2 box (`docker compose`: API + Caddy for HTTPS, see `deploy/ec2.sh`) + Neon + Cloudflare R2
+ Firebase. One-time setup: `docs/AWS_SETUP.md`. After that, CI (`.github/workflows/ci.yml`) tests
every PR and, on `main`, runs `alembic upgrade head` against Neon, pushes the image to ECR and runs
`deploy/ec2.sh` on the box via SSM. Repo variables `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`; secret `DATABASE_URL`.

The base-offline check is not scheduled anywhere; run it from your laptop:
`curl -X POST -H "X-Job-Key: $JOB_API_KEY" https://<url>/api/v1/internal/base-offline`
(`python -m app.jobs.base_offline` does the same against the local DB).

## Device script

Device routes live under `/api/v1/device/*`, need `X-Device-Key`, and are hidden from `/docs`.
`tests/test_device_contract.py` is the executable protocol: boot → `PUT bases/{serial}/state` →
`POST events` (`button_seen`) → poll `GET desired` / `POST desired/ack` → stream `press`,
`battery`, `power`, `fully_charged`, `online` events. Event delivery is idempotent on
(base, type, occurred_at, button serial); timestamps may be ISO 8601, epoch seconds, or epoch millis.

## Load test

    PYTHONPATH=. uv run python scripts/loadtest.py    # seeds 50k interactions once, prints p50/p95 per search scenario
