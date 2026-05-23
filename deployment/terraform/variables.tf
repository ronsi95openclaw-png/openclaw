variable "region" {
  description = "Cloud region for the OpenClaw instance."
  type        = string
  default     = "nyc3"
}

variable "instance_size" {
  description = "Droplet/VM size slug. Must provide at least 4 GB RAM and 2 vCPU."
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "environment" {
  description = "Deployment environment. Controls tagging and certain safety gates."
  type        = string
  default     = "demo"

  validation {
    condition     = contains(["demo", "staging", "production"], var.environment)
    error_message = "environment must be one of: demo, staging, production."
  }
}

variable "docker_image" {
  description = "Fully-qualified Docker image to deploy, including tag. e.g. ghcr.io/openclaw/openclaw:1.2.3"
  type        = string
  # No default — must be specified at apply time to prevent stale deploys.
}

variable "demo_mode" {
  description = "When true the bot runs in paper-trading mode only (DEMO_MODE=true). Never set to false without explicit operator approval."
  type        = bool
  default     = true
}

variable "api_key" {
  description = "DigitalOcean (or cloud provider) API key used by the Terraform provider."
  type        = string
  sensitive   = true
}

variable "crypto_api_key" {
  description = "Crypto.com exchange API key. Injected into the instance via an encrypted env file — never stored in state in plaintext."
  type        = string
  sensitive   = true
}

variable "crypto_api_secret" {
  description = "Crypto.com exchange API secret. Injected into the instance via an encrypted env file — never stored in state in plaintext."
  type        = string
  sensitive   = true
}

variable "telegram_bot_token" {
  description = "Telegram bot token for trade and HALT notifications. Optional — leave empty to disable Telegram alerts."
  type        = string
  sensitive   = true
  default     = ""
}

variable "monitoring_enabled" {
  description = "Enable cloud-provider native monitoring agent on the instance."
  type        = bool
  default     = true
}

variable "ssh_key_name" {
  description = "Name of the SSH key registered in DigitalOcean (or equivalent cloud provider) used for instance access."
  type        = string
  default     = "openclaw-deploy"
}

variable "ssh_allowed_cidrs" {
  description = "List of CIDR blocks allowed to reach SSH (port 22). Restrict to your operator IPs."
  type        = list(string)
  default     = []  # Must be explicitly set — empty = no SSH access allowed.
}

variable "allowed_cidrs" {
  description = "CIDR blocks allowed to reach the API (8000), Dashboard (3000), Prometheus (9090), and Grafana (3030) ports."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}
