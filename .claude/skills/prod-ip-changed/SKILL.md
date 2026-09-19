---
name: prod-ip-changed
description: Recover prod after the EC2 box came up with a new public IP (new sslip.io hostname, cert, docs). Usage /prod-ip-changed <new ip>
disable-model-invocation: true
---

The prod URL is `https://<ip-with-dashes>.sslip.io`, so a stop/start of `fluentpet-api` changes the hostname, and Caddy needs a certificate for the new name before anything answers over HTTPS. `deploy/ec2.sh` derives the hostname from instance metadata and reloads Caddy, so the fix is always "run the deploy once more, then record the new URL".

Argument: the new public IP (`$ARGUMENTS`). Without one, ask for it (EC2 console → instance → *Public IPv4 address*); there is no `aws` CLI on this laptop to look it up.

## 1. Diagnose the new hostname

`HOST=<ip with dots replaced by dashes>.sslip.io`. Run `curl -sv --max-time 15 https://$HOST/healthz` and read it:

| Seen | Meaning | Go to |
|---|---|---|
| `HTTP/2 200` `{"ok":true}` | `fluentpet-boot.service` already healed it at boot | step 4 |
| `tlsv1 alert internal error` after `Connected to` | the box, but Caddy still holds the cert for the *old* name | step 2 |
| `Connection timed out` / `refused` | not the box: wrong IP, or the instance is not running | stop, ask |

Done when one row matched.

## 2. Re-run the deploy on the box

Either, both idempotent:

- **From here** — `git commit --allow-empty -m "deploy: new public IP" && git push origin main`. CI runs `deploy/ec2.sh` on the box through SSM (~5 min); watch it at `https://api.github.com/repos/yvhumancloud/fluentpet_server_remake/actions/runs?per_page=1`.
- **Faster, if the user has a Session Manager shell open** — they run `sudo systemctl start fluentpet-boot.service` (the same script, instant).

Done when the deploy has finished (CI run `completed`, or the user says so).

## 3. Wait for the certificate

Poll in the background, one line on exit:

```sh
for i in $(seq 1 30); do [ "$(curl -s -m 15 -o /dev/null -w '%{http_code}' https://$HOST/healthz)" = 200 ] && { echo ready; exit 0; }; sleep 10; done; echo "no cert after 5 min"
```

Issuance normally lands within a minute of the deploy. If it does not after 5 min, the likely cause is Let's Encrypt's per-domain quota, which every `sslip.io` user shares: the user checks on the box with `sudo docker compose -f /opt/fluentpet/compose.yml logs caddy --tail 30` — `rateLimited` confirms it, and only waiting (or an Elastic IP + real domain) fixes it. Done when `healthz` is 200.

## 4. Verify

`bash scripts/smoke.sh https://$HOST .env.prod` — done at 9 × `ok`.

## 5. Record the new URL

Replace the old hostname in `docs/HANDOFF.md` and `scripts/smoke.sh` (`grep -rn "sslip.io" docs scripts`), commit `docs: prod URL is <HOST>`, push. Update the prod URL in the project memory (`fastapi-rewrite-scope`). Done when `git status` is clean and the push succeeded.

Report the new URL and that the app's base URL must follow it. Each run of this skill is the cost of not having an Elastic IP; mention that once, no more.
