resource "google_cloudfunctions2_function" "this" {
  name     = var.name
  location = var.location

  build_config {
    runtime     = var.runtime
    entry_point = var.entry_point
    source {
      storage_source {
        bucket = var.source_bucket_name
        object = var.source_object_name
      }
    }
  }

  service_config {
    max_instance_count    = var.max_instance_count
    available_memory      = var.available_memory
    timeout_seconds       = var.timeout_seconds
    service_account_email = var.service_account_email

    environment_variables = {
      GOOGLE_CLOUD_PROJECT = var.project_id
    }
  }

  # This block only gets added if var.event_trigger_topic_id is NOT null
  dynamic "event_trigger" {
    for_each = var.event_trigger_topic_id != null ? [1] : []
    content {
      trigger_region = var.location
      event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
      pubsub_topic   = var.event_trigger_topic_id
      retry_policy   = "RETRY_POLICY_DO_NOT_RETRY"
    }
  }

  lifecycle {
    ignore_changes = [
      # Ignore any changes to the entire 'source' block within 'build_config'
      build_config[0].source,
    ]
  }
}