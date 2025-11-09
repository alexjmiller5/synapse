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
    "artifactregistry.googleapis.com"
  ])

  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "terraform_state" {
  name     = "${var.project_id}-terraform-state"
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

resource "google_secret_manager_secret" "secrets" {
  for_each = toset([
    "gemini-api-key",
    "notion-api-token",
    "notion-tasks-db-id",
    "notion-quick-notes-last-block-id",
    "gmail-sender-email",
    "gmail-app-password",
    "gmail-recipient-email",
  ])

  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

# --- Eventarc Trigger for Reporter Service ---

resource "google_eventarc_trigger" "reporter_trigger" {
  name     = "reporter-trigger"
  location = var.region
  project  = var.project_id

  # 1. The event to listen for
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  # 2. The topic to listen on
  transport {
    pubsub {
      topic = google_pubsub_topic.reporter_topic.id
    }
  }

  # 3. The service to send the event to
  destination {
    cloud_run_service {
      service = module.reporter_service.service_name
      region  = var.region
    }
  }

  # 4. The service account for the trigger itself
  service_account = google_service_account.function_sa.email
}

# Allow the reporter service to be invoked by Eventarc
resource "google_cloud_run_service_iam_member" "reporter_invoker" {
  service  = module.reporter_service.service_name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"

  # This member is the SA that Eventarc uses
  member = "serviceAccount:${google_service_account.function_sa.email}"
}

# Allow the processor service to be invoked by the public
resource "google_cloud_run_service_iam_member" "processor_invoker" {
  service  = module.processor_service.service_name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Reporter Function Infrastructure ---

resource "google_pubsub_topic" "reporter_topic" {
  name = "reporter"

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

# --- Service Accounts ---

# SA for the Cloud Functions themselves
resource "google_service_account" "function_sa" {
  account_id   = "synapse-functions"
  display_name = "Synapse Cloud Functions Service Account"
}

# --- Workload Identity Federation (for GitHub Actions CI/CD) ---

resource "google_iam_workload_identity_pool" "github_pool" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  project                            = var.project_id
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

# --- CI/CD Service Accounts & Permissions ---

resource "google_service_account" "terraform_sa" {
  project      = var.project_id
  account_id   = "terraform-sa"
  display_name = "Terraform Service Account"
}

resource "google_service_account" "deploy_sa" {
  project      = var.project_id
  account_id   = "deploy-sa"
  display_name = "Deployment Service Account"
}

# Allow GitHub to impersonate the Terraform SA
resource "google_service_account_iam_member" "terraform_sa_wif_user" {
  service_account_id = google_service_account.terraform_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
  depends_on         = [google_iam_workload_identity_pool_provider.github_provider]
}

# Allow GitHub to impersonate the Deploy SA
resource "google_service_account_iam_member" "deploy_sa_wif_user" {
  service_account_id = google_service_account.deploy_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
  depends_on         = [google_iam_workload_identity_pool_provider.github_provider]
}

# --- Project-level Roles ---
resource "google_project_iam_member" "terraform_sa_iam_admin" {
  project = var.project_id
  role    = "roles/resourcemanager.projectIamAdmin" # 
  member  = google_service_account.terraform_sa.member
}

resource "google_project_iam_member" "deploy_sa_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = google_service_account.deploy_sa.member
}

resource "google_project_iam_member" "terraform_sa_roles" {
  project = var.project_id
  role    = "roles/editor" # TODO: Note: 'editor' is broad. Scope this down for production.
  member  = google_service_account.terraform_sa.member
}

resource "google_project_iam_member" "deploy_sa_run_admin" {
  project = var.project_id
  role    = "roles/run.admin" # <-- This is the new role
  member  = google_service_account.deploy_sa.member
}
resource "google_project_iam_member" "deploy_sa_iam_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = google_service_account.deploy_sa.member
}

# --- Function SA Roles ---
resource "google_project_iam_member" "function_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

resource "google_project_iam_member" "function_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

resource "google_project_iam_member" "function_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

module "processor_service" {
  source = "./modules/cloud-service" # <-- Note the new path

  name                  = "processor"
  location              = var.region
  project_id            = var.project_id
  service_account_email = google_service_account.function_sa.email
  max_instance_count    = 10
  available_memory      = "512Mi"
  available_cpu         = "1000m"
  timeout_seconds       = 300

  depends_on = [google_project_service.services]
}

module "reporter_service" {
  source = "./modules/cloud-service" # <-- Note the new path

  # --- Inputs ---
  name                  = "reporter"
  location              = var.region
  project_id            = var.project_id
  service_account_email = google_service_account.function_sa.email
  max_instance_count    = 1
  available_memory      = "256Mi"
  available_cpu         = "1000m"
  timeout_seconds       = 300

  depends_on = [google_project_service.services]
}