# Production setup: AWS App Runner + Neon + Cloudflare R2 + Firebase (console walkthrough)

One environment (`prod`), region **`ap-southeast-1` (Singapore)** everywhere. Local development
stays on `docker compose`. After this one-time setup, every push to `main` migrates the database
and deploys.

Names the code expects (`.github/workflows/ci.yml`, `deploy/apprunner.json`) — keep them:

| Thing | Name |
|---|---|
| ECR repository | `fluentpet/api` |
| App Runner service | `fluentpet-api` |
| SSM parameters | `/fluentpet/prod/<VAR>` |
| IAM roles | `fluentpet-github-deploy`, `fluentpet-apprunner-ecr`, `fluentpet-apprunner-instance` |
| GitHub variables / secret | `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN` / `DATABASE_URL` |
| GitHub repo | `yvhumancloud/fluentpet_server_remake` |

Order matters: App Runner needs an image in ECR, and the image comes from CI, so GitHub is
wired up (step 5) *before* the App Runner service is created (step 6).

Keep a scratch note open; you will collect these values along the way:
`ACCOUNT_ID`, Neon URL, R2 account id / key id / secret, Firebase JSON, `DEVICE_API_KEY`,
`JOB_API_KEY`, App Runner URL.

---

## 1. Neon (database) — done, just grab the URL

Neon console → project → **Connect** → select the branch, database `neondb`, role, and make sure
**"Connection pooling" is OFF** (direct endpoint). Copy the string; it looks like

    postgresql://neondb_owner:XXXX@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require

Change the driver prefix and the SSL flag — this is `DATABASE_URL`:

    postgresql+asyncpg://neondb_owner:XXXX@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?ssl=require

## 2. Cloudflare R2 (files)

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket** → name `fluentpet`,
   location hint *Asia-Pacific (APAC)* → Create. Leave public access off (the API hands out
   15-minute presigned URLs).
2. R2 → **Manage R2 API Tokens** (right side) → **Create API token** →
   name `fluentpet-api`, permissions **Object Read & Write**, "Specify bucket(s)" → `fluentpet`
   → Create. Copy **Access Key ID** and **Secret Access Key** now (the secret is shown once).
3. On the same page the S3 endpoint is shown as `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`;
   that hex string is `R2_ACCOUNT_ID`. `R2_BUCKET` is `fluentpet`.

## 3. Firebase (auth + push)

1. Firebase console → project → **Build → Authentication → Get started → Sign-in method** →
   enable **Google** (add a support email) → Save. (Add Email/Password too if the app will use
   it — the backend doesn't care.)
2. Project settings (gear) → **General → Your apps → Add app → Android**, enter the app's
   package name, download `google-services.json` → that file goes to the app project, not here.
3. Project settings → **Service accounts** → **Generate new private key** → a JSON file
   downloads. It must be one line for the env var:
   `jq -c . ~/Downloads/fluentpet-xxxx.json | pbcopy` (macOS) → this is `FIREBASE_CREDENTIALS_JSON`.
   Delete the downloaded file afterwards; it is a full-access key.
4. Cloud Messaging is on by default; nothing to enable.
5. Later, for support staff who need `X-Login-As`: set the custom claim once with the Admin SDK
   (`auth.set_custom_user_claims(uid, {"admin": True})`).

## 4. AWS — IAM, ECR, secrets

Sign in to the AWS console as an admin user. Set the region selector (top right) to
**Asia Pacific (Singapore) ap-southeast-1** and keep it there for everything below.
Your **Account ID** is under your name (top right) — note it.

### 4a. ECR repository

ECR → **Repositories** → **Create repository** → Private, name `fluentpet/api`,
"Scan on push" on → Create.

### 4b. GitHub identity provider + deploy role

IAM → **Identity providers** → **Add provider** → OpenID Connect →
Provider URL `https://token.actions.githubusercontent.com`, Audience `sts.amazonaws.com` → Add.
(If one with that URL already exists, skip.)

IAM → **Roles** → **Create role** → **Web identity** → Identity provider
`token.actions.githubusercontent.com`, Audience `sts.amazonaws.com`,
GitHub organization `yvhumancloud`, GitHub repository `fluentpet_server_remake`,
GitHub branch `main` → Next → skip permissions → Next → name `fluentpet-github-deploy` → Create.

Open the role → **Permissions → Add permissions → Create inline policy** → **JSON** tab, paste
(replace `ACCOUNT_ID`):

```json
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
  { "Effect": "Allow",
    "Action": ["ecr:BatchCheckLayerAvailability","ecr:BatchGetImage","ecr:CompleteLayerUpload",
               "ecr:InitiateLayerUpload","ecr:PutImage","ecr:UploadLayerPart"],
    "Resource": "arn:aws:ecr:ap-southeast-1:ACCOUNT_ID:repository/fluentpet/api" }
]}
```

→ name `ecr-push` → Create. Copy the role **ARN** (`arn:aws:iam::ACCOUNT_ID:role/fluentpet-github-deploy`).

### 4c. Secrets in SSM Parameter Store

Systems Manager → **Parameter Store** → **Create parameter**, nine times. Each: Standard tier,
Type **SecureString**, KMS key `alias/aws/ssm` (default):

| Name | Value |
|---|---|
| `/fluentpet/prod/DATABASE_URL` | the asyncpg URL from step 1 |
| `/fluentpet/prod/FIREBASE_CREDENTIALS_JSON` | the one-line JSON from step 3 |
| `/fluentpet/prod/R2_ACCOUNT_ID` | from step 2 |
| `/fluentpet/prod/R2_ACCESS_KEY_ID` | from step 2 |
| `/fluentpet/prod/R2_SECRET_ACCESS_KEY` | from step 2 |
| `/fluentpet/prod/R2_BUCKET` | `fluentpet` |
| `/fluentpet/prod/DEVICE_API_KEY` | random: `openssl rand -hex 32` — the device script will need it |
| `/fluentpet/prod/JOB_API_KEY` | random: `openssl rand -hex 32` — you send it from your laptop to run the base-offline check (step 7) |
| `/fluentpet/prod/SENTRY_DSN` | a single space for now (SSM refuses empty values); the app treats blank as off |

### 4d. App Runner roles

**ECR access role** — IAM → Roles → Create role → **Custom trust policy**, paste:

```json
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow",
  "Principal": { "Service": "build.apprunner.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
```

→ Next → search and tick **`AWSAppRunnerServicePolicyForECRAccess`** → Next →
name `fluentpet-apprunner-ecr` → Create.

**Instance role** (lets the running service read the secrets) — Create role → Custom trust policy:

```json
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow",
  "Principal": { "Service": "tasks.apprunner.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
```

→ Next → no managed policies → name `fluentpet-apprunner-instance` → Create. Open it →
Add permissions → Create inline policy → JSON (replace `ACCOUNT_ID`):

```json
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": ["ssm:GetParameters","ssm:GetParameter"],
    "Resource": "arn:aws:ssm:ap-southeast-1:ACCOUNT_ID:parameter/fluentpet/prod/*" },
  { "Effect": "Allow", "Action": "kms:Decrypt", "Resource": "*",
    "Condition": { "StringEquals": { "kms:ViaService": "ssm.ap-southeast-1.amazonaws.com" } } }
]}
```

→ name `read-secrets` → Create.

## 5. GitHub → first deploy (migrates Neon, pushes the image)

Repo → **Settings → Secrets and variables → Actions**:

- **Variables** tab → New repository variable: `AWS_REGION` = `ap-southeast-1`;
  `AWS_DEPLOY_ROLE_ARN` = the ARN from 4b.
- **Secrets** tab → New repository secret: `DATABASE_URL` = the asyncpg URL from step 1.

Then **Actions** → the latest `ci` run → **Re-run failed jobs** (or push any commit to `main`).
Green means: schema is in Neon and `fluentpet/api:latest` is in ECR. Check ECR → `fluentpet/api`
shows an image.

## 6. App Runner service

App Runner → **Create service**:

**Source and deployment**
- Repository type **Container registry**, provider **Amazon ECR**
- Container image URI → **Browse** → `fluentpet/api` → tag `latest`
- Deployment trigger **Automatic**
- ECR access role → **Use existing service role** → `fluentpet-apprunner-ecr`

**Configure service**
- Service name `fluentpet-api`
- Virtual CPU **0.25 vCPU**, memory **0.5 GB**
- Port **8080**
- Environment variables → Add: **Plain text** `ENV` = `prod`
- Environment variables → Add, nine times: source **SSM Parameter Store**, name = the variable
  (`DATABASE_URL`, `FIREBASE_CREDENTIALS_JSON`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `DEVICE_API_KEY`, `JOB_API_KEY`, `SENTRY_DSN`),
  value = the parameter ARN `arn:aws:ssm:ap-southeast-1:ACCOUNT_ID:parameter/fluentpet/prod/<NAME>`
- Auto scaling → **Custom configuration → Create**: name `fluentpet-small`, min size **1**,
  max size **2**, max concurrency **80**
- Health check → protocol **HTTP**, path **`/healthz`**, interval 10, timeout 5,
  healthy threshold 1, unhealthy threshold 3
- Security → Instance role → `fluentpet-apprunner-instance`
- Networking → incoming **Public endpoint**, outgoing **Public access**

→ Create & deploy. Status goes *Operation in progress* → **Running** in ~5 min. Copy the
**Default domain** (`xxxx.ap-southeast-1.awsapprunner.com`) — this is the API URL.

If it stays in progress and then fails: **Logs → Deployment logs / Application logs** — a
missing parameter or a wrong ARN shows there as a settings validation error at startup.

Check:

    curl https://xxxx.ap-southeast-1.awsapprunner.com/healthz     → {"ok":true}
    open https://xxxx.ap-southeast-1.awsapprunner.com/docs

`deploy/apprunner.json` in the repo is the same configuration for the CLI, if you ever recreate
the service.

## 7. `base_offline` check (no scheduler)

Nothing in the cloud runs it. Trigger it from your laptop whenever you want:

    curl -X POST -H "X-Job-Key: <JOB_API_KEY>" https://xxxx.ap-southeast-1.awsapprunner.com/api/v1/internal/base-offline

Response `{"pushed":N}` = number of `base_offline` notifications sent (once per outage, so
running it often is harmless). Add an EventBridge rule later if you ever want it automatic.

## 8. Smoke test

1. `curl https://<url>/healthz` → `{"ok":true}`
2. `curl https://<url>/api/v1/me` → 401 with the JSON error envelope (auth is on).
3. Sign in from the app (Firebase) → `GET /api/v1/me` → a fresh household.
   Without the app yet: Firebase console → Authentication → add a test user, then get an ID
   token via the REST API — or just wait for the app; the backend part is verified by 2.
4. Device: `curl -H "X-Device-Key: <DEVICE_API_KEY>" "https://<url>/api/v1/device/desired?serial_number=FPB000000001"` → `[]`
5. Job: the curl from step 7 → `{"pushed":0}`
6. Optional: `DATABASE_URL=<neon> PYTHONPATH=. uv run python scripts/loadtest.py` from your
   laptop to see search latency on Neon; afterwards delete the `load@example.com` household in
   Neon's SQL editor (`delete from users where email='load@example.com'; delete from households where id not in (select household_id from users);`).

## Cost and switching it off

| | after credits |
|---|---|
| App Runner 0.25 vCPU / 0.5 GB, 1 warm instance | ~$3–6 / month |
| ECR (a few images) | ~$0.10 |
| SSM, IAM, CloudWatch logs at this volume | $0 |
| Neon free (100 CU-hours, 0.5 GB, auto-suspend) | $0 |
| R2 (10 GB, no egress fees) | $0 |
| Firebase Spark | $0 |

App Runner never scales to zero, but it can be **paused** when nobody is using the app:
App Runner → service → **Actions → Pause** (no compute billed; URL down) / **Resume** (~2–3 min).
A push to `main` while paused does not deploy — after resuming, **Actions → Deploy** once.
Neon suspends itself after 5 idle minutes.

Rotate `DEVICE_API_KEY` / `JOB_API_KEY`: edit the SSM parameter, then App Runner →
**Actions → Deploy** so instances pick it up.

## Later, if wanted

- Custom domain: App Runner → service → **Custom domains → Link domain** (free cert), then
  add the CNAME records it shows at your DNS.
- Sentry: create a project, set `/fluentpet/prod/SENTRY_DSN`, Deploy.
- A `dev` service: repeat 4c/6 with `/fluentpet/dev/*` parameters and a Neon branch, and a second
  workflow deploying from a `dev` branch.
