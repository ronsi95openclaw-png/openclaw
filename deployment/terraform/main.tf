terraform {
  required_version = ">= 1.5"

  required_providers {
    # DigitalOcean provider — replace with your preferred cloud provider.
    # For AWS: registry.terraform.io/hashicorp/aws
    # For GCP: registry.terraform.io/hashicorp/google
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.36"
    }
  }

  # Remote state — configure backend for your environment.
  # backend "s3" {
  #   bucket = "openclaw-tfstate"
  #   key    = "deployment/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

# ── Provider ──────────────────────────────────────────────────────────────────

provider "digitalocean" {
  token = var.api_key
}

# ── SSH Key (existing key registered in DigitalOcean) ─────────────────────────

data "digitalocean_ssh_key" "deploy_key" {
  name = var.ssh_key_name
}

# ── Firewall (fail-closed: only named ports admitted) ─────────────────────────

resource "digitalocean_firewall" "openclaw" {
  name = "openclaw-${var.environment}-fw"

  droplet_ids = [digitalocean_droplet.openclaw.id]

  # SSH
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_allowed_cidrs
  }

  # FastAPI backend
  inbound_rule {
    protocol         = "tcp"
    port_range       = "8000"
    source_addresses = var.allowed_cidrs
  }

  # Next.js dashboard
  inbound_rule {
    protocol         = "tcp"
    port_range       = "3000"
    source_addresses = var.allowed_cidrs
  }

  # Prometheus
  inbound_rule {
    protocol         = "tcp"
    port_range       = "9090"
    source_addresses = var.allowed_cidrs
  }

  # Grafana
  inbound_rule {
    protocol         = "tcp"
    port_range       = "3030"
    source_addresses = var.allowed_cidrs
  }

  # Allow all outbound traffic (exchange API calls, Telegram, etc.)
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  tags = ["openclaw", "trading-bot", "demo-mode", var.environment]
}

# ── Droplet / VM ──────────────────────────────────────────────────────────────

resource "digitalocean_droplet" "openclaw" {
  name   = "openclaw-${var.environment}"
  region = var.region
  size   = var.instance_size  # 4 GB RAM, 2 vCPU

  image = "ubuntu-22-04-x64"

  ssh_keys = [data.digitalocean_ssh_key.deploy_key.id]

  tags = ["openclaw", "trading-bot", "demo-mode", var.environment]

  # Monitoring enabled for DO-native metrics
  monitoring = var.monitoring_enabled

  # Bootstrap: install Docker and run the OpenClaw container
  user_data = templatefile("${path.module}/cloud-init.tpl", {
    docker_image       = var.docker_image
    demo_mode          = tostring(var.demo_mode)
    crypto_api_key     = var.crypto_api_key
    crypto_api_secret  = var.crypto_api_secret
    telegram_bot_token = var.telegram_bot_token
    environment        = var.environment
  })
}

# ── cloud-init template (inline for self-contained module) ───────────────────
# Written to disk so templatefile() can reference it during plan/apply.
resource "local_file" "cloud_init_template" {
  filename = "${path.module}/cloud-init.tpl"
  content  = <<-TEMPLATE
#!/bin/bash
set -euo pipefail

# System update
apt-get update -y
apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Install Docker Compose
apt-get install -y docker-compose-plugin

# Create openclaw user
useradd -m -s /bin/bash openclaw
usermod -aG docker openclaw

# Write environment file (secrets never hit disk as shell history)
mkdir -p /etc/openclaw
chmod 700 /etc/openclaw
cat > /etc/openclaw/secrets.env <<'ENVFILE'
DEMO_MODE=${demo_mode}
CRYPTOCOM_API_KEY=${crypto_api_key}
CRYPTOCOM_API_SECRET=${crypto_api_secret}
TELEGRAM_BOT_TOKEN=${telegram_bot_token}
ENVIRONMENT=${environment}
ENVFILE
chmod 600 /etc/openclaw/secrets.env
chown root:root /etc/openclaw/secrets.env

# Create data directory
mkdir -p /opt/openclaw/data
chown -R openclaw:openclaw /opt/openclaw

# Pull and run container
docker pull ${docker_image}
docker run -d \
  --name openclaw \
  --restart unless-stopped \
  --env-file /etc/openclaw/secrets.env \
  -p 8000:8000 \
  -p 3000:3000 \
  -p 9090:9090 \
  -v /opt/openclaw/data:/app/data \
  ${docker_image}

echo "OpenClaw bootstrap complete"
TEMPLATE
}

# ── Health probe (null_resource with local-exec) ──────────────────────────────

resource "null_resource" "health_check" {
  depends_on = [digitalocean_droplet.openclaw]

  triggers = {
    droplet_id = digitalocean_droplet.openclaw.id
  }

  provisioner "local-exec" {
    command = <<-CMD
      echo "Waiting for OpenClaw API to become healthy..."
      MAX_ATTEMPTS=30
      ATTEMPT=0
      until curl -sf "http://${digitalocean_droplet.openclaw.ipv4_address}:8000/api/health" > /dev/null 2>&1; do
        ATTEMPT=$((ATTEMPT + 1))
        if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
          echo "Health check timed out after $MAX_ATTEMPTS attempts"
          exit 1
        fi
        echo "Attempt $ATTEMPT/$MAX_ATTEMPTS — retrying in 10s..."
        sleep 10
      done
      echo "Health check passed!"
    CMD
  }
}
