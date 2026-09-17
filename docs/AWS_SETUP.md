# Production setup: one EC2 box + Neon + Cloudflare R2 + Firebase (console walkthrough)

One environment (`prod`), region **`ap-southeast-1` (Singapore)** everywhere. Local development
stays on `docker compose`. After this one-time setup, every push to `main` migrates the database
and deploys.

Why EC2: this account is on AWS's new free plan, where App Runner is not available (needs the
irreversible "advanced features" activation). One `t3.micro` running `docker compose` (the API +
Caddy for HTTPS) does the job; `deploy/ec2.sh` is the whole server config.

Names the code expects (`.github/workflows/ci.yml`, `deploy/ec2.sh`) — keep them:

| Thing | Name |
|---|---|
| ECR repository | `fluentpet/api` |
| EC2 instance tag | `Name` = `fluentpet-api` (CI deploys to whatever carries this tag) |
| SSM parameters | `/fluentpet/prod/<VAR>` |
| IAM roles | `fluentpet-github-deploy`, `fluentpet-ec2` |
| GitHub variables / secret | `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN` / `DATABASE_URL` |
| GitHub repo | `yvhumancloud/fluentpet_server_remake` |

Order matters: the box pulls its image from ECR, and the image comes from CI, so GitHub is
wired up (step 5) *before* the instance is launched (step 6).

Keep a scratch note open; you will collect these values along the way:
`ACCOUNT_ID`, Neon URL, R2 account id / key id / secret, Firebase JSON, `DEVICE_API_KEY`,
`JOB_API_KEY`, the instance's public IP.

---

## 1. Neon (database) — done, just grab the URL

Neon console → project → **Connect** → select the branch, database `neondb`, role, and make sure
**"Connection pooling" is OFF** (direct endpoint). Copy the string; it looks like

    postgresql://neondb_owner:XXXX@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require

Use it exactly as printed — this is `DATABASE_URL` (the app rewrites it for asyncpg: `+asyncpg`,
`ssl=`, drops `channel_binding`).

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

IAM → **Roles** → **Create role** → **Custom trust policy** (not the Web-identity wizard: it writes
the old `repo:org/repo:…` subject, but GitHub now sends `repo:org@OWNER_ID/repo@REPO_ID:…`). Get the
two ids with `curl -s https://api.github.com/repos/yvhumancloud/fluentpet_server_remake | jq '.owner.id, .id'`
and paste (replace `ACCOUNT_ID`, `OWNER_ID`, `REPO_ID`):

```json
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": { "StringEquals": {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub": "repo:yvhumancloud@OWNER_ID/fluentpet_server_remake@REPO_ID:ref:refs/heads/main" } } } ] }
```

→ Next → skip permissions → Next → name `fluentpet-github-deploy` → Create.

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

### 4d. EC2 instance role

IAM → Roles → Create role → **AWS service** → use case **EC2** → Next → tick
**`AmazonSSMManagedInstanceCore`** (lets CI run the deploy script on the box, and gives you a
browser shell without SSH) and **`AmazonEC2ContainerRegistryReadOnly`** (pull the image) → Next →
name `fluentpet-ec2` → Create. Open it → Add permissions → Create inline policy → JSON
(replace `ACCOUNT_ID`):

```json
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": ["ssm:GetParametersByPath","ssm:GetParameters","ssm:GetParameter"],
    "Resource": ["arn:aws:ssm:ap-southeast-1:ACCOUNT_ID:parameter/fluentpet/prod",
                 "arn:aws:ssm:ap-southeast-1:ACCOUNT_ID:parameter/fluentpet/prod/*"] },
  { "Effect": "Allow", "Action": "kms:Decrypt", "Resource": "*",
    "Condition": { "StringEquals": { "kms:ViaService": "ssm.ap-southeast-1.amazonaws.com" } } }
]}
```

→ name `read-secrets` → Create.

### 4e. Let CI reach the box

IAM → Roles → `fluentpet-github-deploy` → Add permissions → Create inline policy → JSON
(replace `ACCOUNT_ID`):

```json
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "ssm:SendCommand",
    "Resource": ["arn:aws:ssm:ap-southeast-1::document/AWS-RunShellScript",
                 "arn:aws:ec2:ap-southeast-1:ACCOUNT_ID:instance/*"] },
  { "Effect": "Allow", "Action": "ssm:ListCommandInvocations", "Resource": "*" }
]}
```

→ name `ssm-deploy` → Create.

## 5. GitHub → first deploy (migrates Neon, pushes the image)

Repo → **Settings → Secrets and variables → Actions**:

- **Variables** tab → New repository variable: `AWS_REGION` = `ap-southeast-1`;
  `AWS_DEPLOY_ROLE_ARN` = the ARN from 4b.
- **Secrets** tab → New repository secret: `DATABASE_URL` = the asyncpg URL from step 1.

Then **Actions** → the latest `ci` run → **Re-run failed jobs** (or push any commit to `main`).
The `migrate` and `build and push` steps must be green: schema is in Neon and `fluentpet/api:latest`
is in ECR (check ECR → `fluentpet/api` shows an image). The final `deploy` step fails until the
instance from step 6 exists — expected.

## 6. EC2 instance

EC2 → **Instances** → **Launch instances**:

- **Name** `fluentpet-api` (this becomes the `Name` tag CI targets — exact spelling)
- **AMI** Amazon Linux 2023 (64-bit x86); **Instance type** `t3.micro`
- **Key pair** → *Proceed without a key pair* (shell access is via Systems Manager, no SSH)
- **Network settings** → Edit: **Auto-assign public IP** *Enable*; **Create security group**
  named `fluentpet-api`; inbound rules: **HTTPS** from Anywhere-IPv4 and **HTTP** from
  Anywhere-IPv4 (Caddy needs 80 for the certificate). Remove the SSH rule.
- **Storage** 8 GiB gp3 (default)
- **Advanced details** → **IAM instance profile** `fluentpet-ec2`; scroll to **User data**, paste:

  ```
  #!/bin/bash
  curl -fsSL https://raw.githubusercontent.com/yvhumancloud/fluentpet_server_remake/main/deploy/ec2.sh | bash
  ```

→ **Launch instance**. Open it and copy the **Public IPv4 address** (e.g. `13.212.34.56`).

The URL is the IP with dashes on sslip.io: **`https://13-212-34-56.sslip.io`**. First boot takes
~3 minutes (installs Docker, pulls the image, gets a Let's Encrypt certificate). Check:

    curl https://13-212-34-56.sslip.io/healthz     → {"ok":true}
    open https://13-212-34-56.sslip.io/docs

If it doesn't come up: Instance → **Connect → Session Manager → Connect** gives a shell;
`sudo cat /var/log/cloud-init-output.log` shows the first boot, `cd /opt/fluentpet && sudo docker compose logs`
the containers. A settings validation error at startup means a wrong SSM parameter.

No Elastic IP for now: the IP (and therefore the URL and certificate) changes whenever the
instance is **stopped and started** (not on reboot). After a start, the API is back at the new
`https://<new-ip-with-dashes>.sslip.io` automatically once `deploy/ec2.sh` has run again — push to
`main`, or run the user-data command from a Session Manager shell.

From now on every push to `main` ends with CI running `deploy/ec2.sh` on the box (Systems Manager →
**Run Command → Command history** shows the output). To redeploy without a code change:
Actions → latest run → Re-run failed jobs, or run the same curl-pipe-bash from a Session Manager shell.

## 7. `base_offline` check (no scheduler)

Nothing in the cloud runs it. Trigger it from your laptop whenever you want:

    curl -X POST -H "X-Job-Key: <JOB_API_KEY>" https://<url>/api/v1/internal/base-offline

Response `{"pushed":N}` = number of `base_offline` notifications sent (once per outage, so
running it often is harmless). Add an EventBridge rule later if you ever want it automatic.

## 8. Smoke test

Put the prod `DEVICE_API_KEY` and `JOB_API_KEY` (values from SSM) in a local `.env.prod`
(gitignored), then from the repo:

    scripts/smoke.sh https://<ip-with-dashes>.sslip.io .env.prod

Nine `ok` lines and exit 0: health, auth envelope, dev tokens refused, device key + Neon
round-trip (`[]`), base-offline job (`{"pushed":0}`), `/internal` hidden from OpenAPI.

Then sign in from the app (Firebase) → `GET /api/v1/me` → a fresh household. Optional:
`DATABASE_URL=<neon> PYTHONPATH=. uv run python scripts/loadtest.py` to see search latency on Neon;
afterwards delete the `load@example.com` household in Neon's SQL editor
(`delete from users where email='load@example.com'; delete from households where id not in (select household_id from users);`).

## Cost and switching it off

| | after credits |
|---|---|
| EC2 `t3.micro` + 8 GiB disk + public IPv4, always on | ~$14 / month |
| ECR (a few images) | ~$0.10 |
| SSM, IAM, CloudWatch at this volume | $0 |
| Neon free (100 CU-hours, 0.5 GB, auto-suspend) | $0 |
| R2 (10 GB, no egress fees) | $0 |
| Firebase Spark | $0 |

Nobody using the app: EC2 → instance → **Instance state → Stop** (no compute billed, URL down).
**Start** brings it back in ~1 min at a **new IP** (see step 6). Neon suspends itself after
5 idle minutes.

Rotate `DEVICE_API_KEY` / `JOB_API_KEY` / any secret: edit the SSM parameter, then redeploy
(push, re-run the deploy job, or run `deploy/ec2.sh` on the box) — the script rewrites `.env`.

## Later, if wanted

- Stable address: EC2 → **Elastic IPs → Allocate → Associate** with the instance (same price as
  the auto IP while attached), then the URL never changes.
- Custom domain: an A record to the IP, then change `HOST=` in `deploy/ec2.sh` — Caddy does the
  rest. Needed before anything serious: sslip.io shares one Let's Encrypt rate limit with everyone
  else using it (if Caddy logs `too many certificates already issued`, wait an hour and re-run).
- Sentry: create a project, set `/fluentpet/prod/SENTRY_DSN`, redeploy.
- A `dev` box: repeat 4c/6 with `/fluentpet/dev/*` parameters, a Neon branch and tag `Name=fluentpet-api-dev`,
  plus a second workflow deploying from a `dev` branch.
