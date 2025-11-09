variable "name" {
  description = "The name of the Cloud Run service."
  type        = string
}

variable "location" {
  description = "The region for the service."
  type        = string
}

variable "gcp_project_id" {
  description = "The GCP project ID."
  type        = string
}

variable "service_account_email" {
  description = "The service account email for the service."
  type        = string
}

variable "max_instance_count" {
  description = "The maximum number of instances."
  type        = number
  default     = 1
}

variable "available_memory" {
  description = "The amount of memory available to the service."
  type        = string
}

variable "available_cpu" {
  description = "The amount of CPU available to the service."
  type        = string
}

variable "timeout_seconds" {
  description = "The timeout for the service in seconds."
  type        = number
}