variable "name" {
  description = "The name of the function."
  type        = string
}

variable "entry_point" {
  description = "The name of the Python function to execute."
  type        = string
}

variable "location" {
  description = "The region for the function."
  type        = string
}

variable "project_id" {
  description = "The GCP project ID."
  type        = string
}

variable "service_account_email" {
  description = "The service account email for the function."
  type        = string
}

variable "source_bucket_name" {
  description = "The name of the GCS bucket holding the source code."
  type        = string
}

variable "source_object_name" {
  description = "The name of the zip file in the bucket."
  type        = string
  default     = "placeholder.zip"
}

variable "runtime" {
  description = "The runtime environment for the function."
  type        = string
  default     = "python312"
}

variable "max_instance_count" {
  description = "The maximum number of instances."
  type        = number
}

variable "available_memory" {
  description = "The amount of memory available to the function."
  type        = string
}

variable "timeout_seconds" {
  description = "The timeout for the function in seconds."
  type        = number
  default     = 300
}

variable "event_trigger_topic_id" {
  description = "The ID of the PubSub topic to trigger this function. If null, no event trigger is created."
  type        = string
  default     = null
}