resource "google_project_service" "services" {
  for_each = toset([
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com",
    "pubsub.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "cloudbuild.googleapis.com",
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "artifactregistry.googleapis.com",
    "apigateway.googleapis.com",
    "servicemanagement.googleapis.com",
    "servicecontrol.googleapis.com",
    "places-backend.googleapis.com",
    "apikeys.googleapis.com"
  ])

  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "terraform_state" {
  name     = "${var.gcp_project_id}-terraform-state"
  location = var.region

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
}

# --- API Keys ---

resource "google_apikeys_key" "places_key" {
  provider     = google-beta
  name         = "synapse-places-api-key"
  display_name = "Synapse Places API Key"
  project      = var.gcp_project_id

  restrictions {
    api_targets {
      service = "places-backend.googleapis.com"
    }
  }

  depends_on = [google_project_service.services]
}

# --- Secrets ---

resource "google_secret_manager_secret" "secrets" {
  for_each = toset([
    "gemini-api-key",
    "spotify-client-id",
    "spotify-client-secret",
    "tmdb-api-key",
    "notion-integration-token",
    "notion-trips-db-id",
    "notion-tasks-db-id",
    "notion-groceries-db-id",
    "notion-cheers-note-last-block-id",
    "notion-card-games-list-id",
    "notion-fun-activities-db-id",
    "notion-people-db-id",
    "notion-places-db-id",
    "notion-quick-notes-last-block-id",
    "notion-languages-db-id",
    "notion-ideas-db-id",
    "notion-bucket-list-db-id",
    "notion-movies-db-id",
    "notion-tv-episodes-db-id",
    "notion-tv-shows-db-id",
    "notion-podcasts-db-id",
    "notion-youtube-videos-db-id",
    "notion-youtube-channels-db-id",
    "notion-books-db-id",
    "notion-video-games-db-id",
    "notion-quotes-db-id",
    "notion-bookmarks-db-id",
    "notion-logs-db-id",
  ])

  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret" "places_api_key_secret" {
  secret_id = "google-places-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "places_api_key_version" {
  secret      = google_secret_manager_secret.places_api_key_secret.id
  secret_data = google_apikeys_key.places_key.key_string
}

# --- Eventarc Trigger for Reporter Service ---

resource "google_eventarc_trigger" "reporter_trigger" {
  name     = "reporter-trigger"
  location = var.region
  project  = var.gcp_project_id

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = google_pubsub_topic.reporter_topic.id
    }
  }

  destination {
    cloud_run_service {
      service = module.reporter_service.service_name
      region  = var.region
    }
  }

  service_account = google_service_account.function_sa.email
}

resource "google_cloud_run_service_iam_member" "processor_worker_invoker" {
  service  = module.processor_worker.service_name
  location = var.region
  project  = var.gcp_project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.function_sa.email}"
  depends_on = [
    module.processor_worker
  ]
}

# --- Pub/Sub Topics & Subscriptions ---

resource "google_pubsub_subscription" "processor_subscription" {
  name  = "processor-jobs-sub"
  topic = google_pubsub_topic.processor_topic.name

  ack_deadline_seconds = 600

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  push_config {
    push_endpoint = module.processor_worker.service_url

    oidc_token {
      service_account_email = google_service_account.function_sa.email
    }
  }

  depends_on = [module.processor_worker]
}

resource "google_pubsub_topic" "reporter_topic" {
  name       = "reporter"
  depends_on = [google_project_service.services]
}

resource "google_pubsub_topic" "processor_topic" {
  name       = "processor-jobs"
  depends_on = [google_project_service.services]
}

resource "google_cloud_scheduler_job" "reporter_job" {
  name             = "synapse-daily-report"
  description      = "Trigger daily Synapse reporter"
  schedule         = "0 8,20 * * *" # 8 AM and 8 PM daily
  time_zone        = "UTC"
  region           = var.region
  attempt_deadline = "180s"

  pubsub_target {
    topic_name = google_pubsub_topic.reporter_topic.id
    data       = base64encode(jsonencode({}))
  }

  depends_on = [google_project_service.services]
}

# --- Workload Identity Federation (for GitHub Actions CI/CD) ---

resource "google_iam_workload_identity_pool" "github_pool" {
  project                   = var.gcp_project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  project                            = var.gcp_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Actions Provider"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == \"${var.github_repo}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# --- Service Accounts for CI/CD ---

resource "google_service_account" "terraform_sa" {
  project      = var.gcp_project_id
  account_id   = "terraform-sa"
  display_name = "Terraform Service Account"
}

resource "google_service_account" "deploy_sa" {
  project      = var.gcp_project_id
  account_id   = "deploy-sa"
  display_name = "Deployment Service Account"
}

resource "google_service_account_iam_member" "terraform_sa_wif_user" {
  service_account_id = google_service_account.terraform_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
  depends_on         = [google_iam_workload_identity_pool_provider.github_provider]
}

resource "google_service_account_iam_member" "deploy_sa_wif_user" {
  service_account_id = google_service_account.deploy_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
  depends_on         = [google_iam_workload_identity_pool_provider.github_provider]
}

resource "google_cloud_run_service_iam_member" "reporter_invoker" {
  service  = module.reporter_service.service_name
  location = var.region
  project  = var.gcp_project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.function_sa.email}"
}

resource "google_service_account" "function_sa" {
  account_id   = "synapse-functions"
  display_name = "Synapse Cloud Functions Service Account"
}

resource "google_service_account" "api_gateway_sa" {
  project      = var.gcp_project_id
  account_id   = "api-gateway-sa"
  display_name = "API Gateway Invoker SA"
}
resource "google_cloud_run_service_iam_member" "intaker_gateway_invoker" {
  service    = module.intaker_service.service_name
  location   = var.region
  project    = var.gcp_project_id
  role       = "roles/run.invoker"
  member     = google_service_account.api_gateway_sa.member
  depends_on = [module.intaker_service]
}

resource "google_project_iam_member" "deploy_sa_apigateway_admin" {
  project = var.gcp_project_id
  role    = "roles/apigateway.admin"
  member  = google_service_account.deploy_sa.member
}

resource "google_project_iam_member" "deploy_sa_api_gateway_sa_user" {
  project = var.gcp_project_id
  role    = "roles/iam.serviceAccountUser"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "cloud_build_artifact_writer" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}
resource "google_project_iam_member" "terraform_sa_iam_admin" {
  project = var.gcp_project_id
  role    = "roles/resourcemanager.projectIamAdmin" # 
  member  = google_service_account.terraform_sa.member
}
resource "google_project_iam_member" "deploy_sa_artifact_writer" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.writer"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "terraform_sa_roles" {
  project = var.gcp_project_id
  role    = "roles/editor" # TODO: Note: 'editor' is broad. Scope this down for production.
  member  = google_service_account.terraform_sa.member
}
resource "google_project_iam_member" "deploy_sa_run_admin" {
  project = var.gcp_project_id
  role    = "roles/run.admin"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_iam_user" {
  project = var.gcp_project_id
  role    = "roles/iam.serviceAccountUser"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_run_developer" {
  project = var.gcp_project_id
  role    = "roles/run.developer"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_artifact_admin" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.admin"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_cloudbuild_editor" {
  project = var.gcp_project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "cloud_build_run_invoker" {
  project = var.gcp_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}

# --- Function SA Roles ---

resource "google_project_iam_member" "function_pubsub_publisher" {
  project = var.gcp_project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

resource "google_project_iam_member" "function_secrets" {
  project = var.gcp_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}
resource "google_project_iam_member" "function_logging" {
  project = var.gcp_project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}
resource "google_project_iam_member" "function_monitoring" {
  project = var.gcp_project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}
resource "google_project_iam_member" "function_artifact_reader" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}
resource "google_project_iam_member" "deploy_sa_storage_admin" {
  project = var.gcp_project_id
  role    = "roles/storage.admin"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_service_usage" {
  project = var.gcp_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "cloud_build_service_usage" {
  project = var.gcp_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}
resource "google_project_iam_member" "cloud_build_run_admin" {
  project = var.gcp_project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}
resource "google_project_iam_member" "cloud_build_artifact_admin" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.admin"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}
resource "google_project_iam_member" "cloud_build_storage_admin" {
  project = var.gcp_project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}
resource "google_service_account_iam_member" "cloud_build_sa_user" {
  service_account_id = google_service_account.function_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.gcp_project_number}@cloudbuild.gserviceaccount.com"
}

# --- Cloud Run Services ---

module "intaker_service" {
  source = "./modules/cloud-service"

  name                  = "intaker"
  location              = var.region
  gcp_project_id        = var.gcp_project_id
  service_account_email = google_service_account.function_sa.email
  max_instance_count    = 1
  min_instance_count    = 1
  available_memory      = "512Mi"
  available_cpu         = "1000m"
  timeout_seconds       = 30

  depends_on = [google_project_service.services]
}

module "reporter_service" {
  source = "./modules/cloud-service"

  name                  = "reporter"
  location              = var.region
  gcp_project_id        = var.gcp_project_id
  service_account_email = google_service_account.function_sa.email
  max_instance_count    = 1
  available_memory      = "256Mi"
  available_cpu         = "1000m"
  timeout_seconds       = 300

  depends_on = [google_project_service.services]
}

module "processor_worker" {
  source = "./modules/cloud-service"

  name                  = "processor"
  location              = var.region
  gcp_project_id        = var.gcp_project_id
  service_account_email = google_service_account.function_sa.email
  max_instance_count    = 1
  available_memory      = "512Mi"
  available_cpu         = "1000m"
  timeout_seconds       = 300

  depends_on = [google_project_service.services]
}
