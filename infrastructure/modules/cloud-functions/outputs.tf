output "uri" {
  description = "The public URI of the function."
  value       = google_cloudfunctions2_function.this.service_config[0].uri
}

output "name" {
  description = "The name of the function."
  value       = google_cloudfunctions2_function.this.name
}