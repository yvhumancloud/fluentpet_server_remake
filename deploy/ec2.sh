#!/bin/bash
# FluentPet API on one EC2 box: docker compose running api + caddy (auto-HTTPS on <ip>.sslip.io).
# Idempotent. Runs at first boot (user data) and on every deploy (CI -> SSM Run Command):
#   curl -fsSL https://raw.githubusercontent.com/yvhumancloud/fluentpet_server_remake/main/deploy/ec2.sh | bash
set -euo pipefail
export AWS_DEFAULT_REGION=ap-southeast-1
mkdir -p /opt/fluentpet && cd /opt/fluentpet

command -v docker >/dev/null || { dnf install -y docker && systemctl enable --now docker; }
PLUGIN=/usr/local/lib/docker/cli-plugins/docker-compose
[ -x "$PLUGIN" ] || { mkdir -p "$(dirname "$PLUGIN")" &&
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" -o "$PLUGIN" &&
  chmod +x "$PLUGIN"; }

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
HOST="${IP//./-}.sslip.io"  # ponytail: no domain yet; changes with the IP on stop/start. Elastic IP + real domain later.
REGISTRY="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"

# SSM /fluentpet/prod/* -> .env, single-quoted (the Firebase JSON has spaces and double quotes)
aws ssm get-parameters-by-path --path /fluentpet/prod --with-decryption --output json |
  python3 -c "import json,sys; [print(p['Name'].rsplit('/',1)[1] + \"='\" + p['Value'].strip() + \"'\") for p in json.load(sys.stdin)['Parameters']]" > .env
echo "ENV=prod" >> .env
chmod 600 .env

cat > compose.yml <<EOF
services:
  api:
    image: $REGISTRY/fluentpet/api:latest
    env_file: .env
    restart: unless-stopped
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes: ["caddy:/data", "./Caddyfile:/etc/caddy/Caddyfile"]
volumes:
  caddy:
EOF
printf '%s {\n\treverse_proxy api:8080\n}\n' "$HOST" > Caddyfile

# Re-run at every boot: without an Elastic IP the address (hence hostname + cert) changes on stop/start
cat > /etc/systemd/system/fluentpet-boot.service <<'EOF'
[Unit]
Description=FluentPet: re-run deploy/ec2.sh for the current public IP
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'curl -fsSL https://raw.githubusercontent.com/yvhumancloud/fluentpet_server_remake/main/deploy/ec2.sh | bash'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable -q fluentpet-boot.service

aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker compose pull -q
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
echo "API: https://$HOST"
