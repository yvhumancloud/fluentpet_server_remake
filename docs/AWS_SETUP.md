# Production setup: AWS App Runner + Neon + Cloudflare R2 + Firebase

One environment (`prod`). Local development stays on `docker compose`. Everything below is a
one-time setup; after it, every push to `main` migrates the database and deploys.

Names used by `.github/workflows/ci.yml` and `deploy/apprunner.json` — keep them:

| Thing | Name |
|---|---|
| ECR repository | `fluentpet/api` |
| App Runner service | `fluentpet-api` |
| SSM parameters | `/fluentpet/prod/<VAR>` |
| IAM roles | `fluentpet-github-deploy`, `fluentpet-apprunner-ecr`, `fluentpet-apprunner-instance` |
| GitHub variables / secret | `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN` / `DATABASE_URL` |

Shell variables used in the commands (set them once):

```sh
export AWS_REGION=ap-southeast-1          # see step 0
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export GITHUB_REPO=OWNER/REPO             # e.g. yogesh/fluentpet-backend
```

## 0. Pick a region

Use one region for App Runner and Neon. App Runner is not in every region and Neon is not in
every region either; the overlap that matters:

| App users mostly in | Region |
|---|---|
| India / South-East Asia | `ap-southeast-1` (Singapore) |
| US | `us-east-1` |
| Europe | `eu-central-1` |

## 1. Neon (database) — free

1. https://console.neon.tech → New project → name `fluentpet`, Postgres 18, region from step 0.
2. Copy the **direct** (not pooled) connection string. Turn it into the asyncpg form:
   `postgresql://user:pass@ep-xxx.aws.neon.tech/neondb?sslmode=require`
   → `postgresql+asyncpg://user:pass@ep-xxx.aws.neon.tech/neondb?ssl=require`
3. Keep it; it goes into SSM (step 4c) and GitHub (step 5).

The pool is 5 connections per App Runner instance; with max 2 instances that is 10, well inside
Neon's free-tier limit, so no pooler is needed.

## 2. Cloudflare R2 (files) — free up to 10 GB

1. Cloudflare dashboard → R2 → Create bucket `fluentpet` (location hint: nearest to step 0).
   No public access, no custom domain — the API hands out 15-minute presigned URLs.
2. R2 → Manage R2 API Tokens → Create token: permission **Object Read & Write**, scoped to the
   `fluentpet` bucket. Note the **Access Key ID**, **Secret Access Key**, and the **Account ID**
   (shown on the R2 overview page / in the S3 endpoint `https://<account id>.r2.cloudflarestorage.com`).

## 3. Firebase (auth + push) — free (Spark)

1. https://console.firebase.google.com → Add project `fluentpet` (Analytics off is fine).
2. Authentication → Sign-in method → enable **Google**. (Decide now on email/password — PRD open
   question 1; the backend does not care.)
3. Add the Android app (package name from the app project) and download `google-services.json`
   for the app team. Cloud Messaging is on by default.
4. Project settings → Service accounts → **Generate new private key**. Minify to one line:
   `jq -c . firebase-key.json` → this is `FIREBASE_CREDENTIALS_JSON`.
5. Support admins (`X-Login-As`): give their Firebase user the custom claim once, e.g. with the
   Admin SDK in a Node/Python one-off: `auth.set_custom_user_claims(uid, {"admin": True})`.

## 4. AWS

### 4a. ECR repository

```sh
aws ecr create-repository --repository-name fluentpet/api --region $AWS_REGION \
  --image-scanning-configuration scanOnPush=true
```

### 4b. GitHub → AWS deploy role (OIDC, no keys)

```sh
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com    # skip if the account already has this provider

cat > /tmp/trust.json <<EOF
{ "Version": "2012-10-17", "Statement": [{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
    "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:$GITHUB_REPO:ref:refs/heads/main" }
  }}]}
EOF
aws iam create-role --role-name fluentpet-github-deploy --assume-role-policy-document file:///tmp/trust.json

cat > /tmp/deploy-policy.json <<EOF
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
  { "Effect": "Allow",
    "Action": ["ecr:BatchCheckLayerAvailability","ecr:BatchGetImage","ecr:CompleteLayerUpload",
               "ecr:InitiateLayerUpload","ecr:PutImage","ecr:UploadLayerPart"],
    "Resource": "arn:aws:ecr:$AWS_REGION:$ACCOUNT_ID:repository/fluentpet/api" }
]}
EOF
aws iam put-role-policy --role-name fluentpet-github-deploy --policy-name ecr-push \
  --policy-document file:///tmp/deploy-policy.json
```

### 4c. Secrets in SSM Parameter Store (free)

```sh
put() { aws ssm put-parameter --region $AWS_REGION --type SecureString --overwrite --name "/fluentpet/prod/$1" --value "$2"; }
put DATABASE_URL 'postgresql+asyncpg://…?ssl=require'          # from step 1
put FIREBASE_CREDENTIALS_JSON "$(jq -c . firebase-key.json)"     # from step 3
put R2_ACCOUNT_ID '…'; put R2_ACCESS_KEY_ID '…'; put R2_SECRET_ACCESS_KEY '…'; put R2_BUCKET fluentpet
put DEVICE_API_KEY "$(openssl rand -hex 32)"                      # give this to the device script
put JOB_API_KEY "$(openssl rand -hex 32)"                         # used by EventBridge in 4f
put SENTRY_DSN ''                                                 # fill in when you have Sentry
```

### 4d. App Runner roles

```sh
# lets App Runner pull from ECR
aws iam create-role --role-name fluentpet-apprunner-ecr --assume-role-policy-document '{
  "Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name fluentpet-apprunner-ecr \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess

# lets the running service read the SSM secrets
aws iam create-role --role-name fluentpet-apprunner-instance --assume-role-policy-document '{
  "Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Service":"tasks.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
cat > /tmp/instance-policy.json <<EOF
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": ["ssm:GetParameters","ssm:GetParameter"],
    "Resource": "arn:aws:ssm:$AWS_REGION:$ACCOUNT_ID:parameter/fluentpet/prod/*" },
  { "Effect": "Allow", "Action": "kms:Decrypt", "Resource": "*",
    "Condition": { "StringEquals": { "kms:ViaService": "ssm.$AWS_REGION.amazonaws.com" } } }
]}
EOF
aws iam put-role-policy --role-name fluentpet-apprunner-instance --policy-name read-secrets \
  --policy-document file:///tmp/instance-policy.json
```

### 4e. First image, then the service

App Runner needs an image to exist before the service can be created. Easiest: finish step 5
(GitHub) first and push to `main` — CI migrates Neon and pushes `fluentpet/api:latest`.
(Or build once locally: `aws ecr get-login-password | docker login --username AWS --password-stdin
$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com`, `docker build --platform linux/amd64 -t …/fluentpet/api:latest .`, push,
and run `DATABASE_URL=… uv run alembic upgrade head` yourself.)

```sh
# caps cost: 1 warm instance, at most 2, 80 concurrent requests each
ASC=$(aws apprunner create-auto-scaling-configuration --region $AWS_REGION \
  --auto-scaling-configuration-name fluentpet-small --min-size 1 --max-size 2 --max-concurrency 80 \
  --query AutoScalingConfiguration.AutoScalingConfigurationArn --output text)

sed -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" -e "s/REGION/$AWS_REGION/g" -e "s|AUTOSCALING_ARN|$ASC|" \
  deploy/apprunner.json > /tmp/apprunner.json
aws apprunner create-service --region $AWS_REGION --cli-input-json file:///tmp/apprunner.json
aws apprunner list-services --region $AWS_REGION      # wait for Status RUNNING (~5 min)
```

Note the `ServiceUrl` (`xxxx.$AWS_REGION.awsapprunner.com`). Check:

```sh
curl https://SERVICE_URL/healthz            # {"ok":true}
open https://SERVICE_URL/docs
```

### 4f. Hourly `base_offline` job (EventBridge → the API)

```sh
JOB_KEY=$(aws ssm get-parameter --region $AWS_REGION --with-decryption --name /fluentpet/prod/JOB_API_KEY --query Parameter.Value --output text)
CONN=$(aws events create-connection --region $AWS_REGION --name fluentpet-job --authorization-type API_KEY \
  --auth-parameters "ApiKeyAuthParameters={ApiKeyName=X-Job-Key,ApiKeyValue=$JOB_KEY}" \
  --query ConnectionArn --output text)
DEST=$(aws events create-api-destination --region $AWS_REGION --name fluentpet-base-offline \
  --connection-arn $CONN --http-method POST \
  --invocation-endpoint https://SERVICE_URL/api/v1/internal/base-offline \
  --query ApiDestinationArn --output text)

aws iam create-role --role-name fluentpet-events-invoke --assume-role-policy-document '{
  "Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam put-role-policy --role-name fluentpet-events-invoke --policy-name invoke --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"events:InvokeApiDestination\",\"Resource\":\"$DEST\"}]}"

aws events put-rule --region $AWS_REGION --name fluentpet-base-offline-hourly --schedule-expression "rate(1 hour)"
aws events put-targets --region $AWS_REGION --rule fluentpet-base-offline-hourly \
  --targets "Id=api,Arn=$DEST,RoleArn=arn:aws:iam::$ACCOUNT_ID:role/fluentpet-events-invoke"
```

Manual run any time: `curl -X POST -H "X-Job-Key: $JOB_KEY" https://SERVICE_URL/api/v1/internal/base-offline`.

## 5. GitHub

Repository → Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Variable | `AWS_REGION` | from step 0 |
| Variable | `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::ACCOUNT_ID:role/fluentpet-github-deploy` |
| Secret | `DATABASE_URL` | the Neon asyncpg URL (CI runs `alembic upgrade head` with it) |

Push to `main`. The `deploy` job migrates, builds, pushes `:latest`; App Runner picks it up
(auto-deploy) and swaps instances once `/healthz` passes.

## 6. Smoke test

1. `curl https://SERVICE_URL/healthz`
2. Sign in from the app (Firebase) → `GET /api/v1/me` returns a fresh household.
3. Device: `curl -H "X-Device-Key: …" "https://SERVICE_URL/api/v1/device/desired?serial_number=X"` → `[]`.
4. `POST /api/v1/internal/base-offline` with the job key → `{"pushed":0}`.
5. Optional: `DATABASE_URL=<neon> PYTHONPATH=. uv run python scripts/loadtest.py` to see search
   latency on Neon (then delete the `load@example.com` household from Neon's SQL editor).

## Cost (after the credits)

| | |
|---|---|
| App Runner 0.25 vCPU / 0.5 GB, 1 warm instance | ~$3–6 |
| ECR (a few images) | ~$0.10 |
| EventBridge, SSM, IAM | $0 |
| Neon free tier (0.5 GB storage, scales to zero) | $0 |
| R2 (10 GB, no egress fees) | $0 |
| Firebase Spark (Auth, FCM) | $0 |

App Runner never scales to zero, but it can be paused when nobody is using the app (no compute
billed while paused; the URL is down; a push to `main` while paused does not deploy):

```sh
SERVICE_ARN=$(aws apprunner list-services --region $AWS_REGION --query "ServiceSummaryList[?ServiceName=='fluentpet-api'].ServiceArn" --output text)
aws apprunner pause-service  --region $AWS_REGION --service-arn $SERVICE_ARN
aws apprunner resume-service --region $AWS_REGION --service-arn $SERVICE_ARN   # ~2–3 min; then
aws apprunner start-deployment --region $AWS_REGION --service-arn $SERVICE_ARN # if an image was pushed meanwhile
```

Neon suspends itself after 5 idle minutes; nothing to do there.

Rotate `DEVICE_API_KEY` / `JOB_API_KEY`: `put` the new value in SSM, then
`aws apprunner start-deployment --service-arn …` so instances pick it up.

## Later, if wanted

* Custom domain: App Runner → Custom domains (free cert), then point a CNAME at it.
* A `dev` service: repeat 4c/4e with `/fluentpet/dev/*` parameters and a Neon branch, and a
  second workflow deploying from a `dev` branch.
* Sentry: create a project, `put SENTRY_DSN …`, redeploy.
