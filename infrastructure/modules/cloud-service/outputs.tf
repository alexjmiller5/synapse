output "service_name" {
  description = "The name of the deployed service."
  value       = google_cloud_run_service.this.name
}

output "service_uri" {
  description = "The URI of the deployed service."
  value       = google_cloud_run_service.this.status[0].url
}