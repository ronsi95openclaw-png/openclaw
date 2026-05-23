output "instance_ip" {
  description = "Public IPv4 address of the OpenClaw instance."
  value       = digitalocean_droplet.openclaw.ipv4_address
}

output "api_endpoint" {
  description = "Base URL for the OpenClaw FastAPI backend."
  value       = "http://${digitalocean_droplet.openclaw.ipv4_address}:8000"
}

output "dashboard_url" {
  description = "URL for the Next.js React dashboard."
  value       = "http://${digitalocean_droplet.openclaw.ipv4_address}:3000"
}

output "grafana_url" {
  description = "URL for the Grafana monitoring dashboard."
  value       = "http://${digitalocean_droplet.openclaw.ipv4_address}:3030"
}

output "prometheus_url" {
  description = "URL for the Prometheus metrics server."
  value       = "http://${digitalocean_droplet.openclaw.ipv4_address}:9090"
}

output "health_check_url" {
  description = "Health probe endpoint — returns 200 when the bot API is operational."
  value       = "http://${digitalocean_droplet.openclaw.ipv4_address}:8000/api/health"
}
