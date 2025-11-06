# Enable required APIs
resource "google_project_service" "services" {
  for_each = toset([
    "cloudfunctions.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com",
    "pubsub.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com"
  ])

  service            = each.value
  disable_on_destroy = false
}

# --- Storage Buckets ---

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

resource "google_storage_bucket" "function_bucket" {
  name     = "${var.project_id}-synapse-functions"
  location = var.region

  depends_on = [google_project_service.services]
}

# Create a placeholder zip file for initial deployment
resource "google_storage_bucket_object" "placeholder_source" {
  name    = "placeholder.zip"
  bucket  = google_storage_bucket.function_bucket.name
  content = base64decode("UEsDBAoAAAAIAAAAAACAAAAAAAAAAAAAAAAACAAAAG1haW4ucHlLycnPLFZIzMksyS9SSMPowSkpMrfSASkwAVBLBBQAAAAAAAAAAAAA")
}

# --- Secrets ---

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

# --- Reporting Function Infrastructure ---

resource "google_pubsub_topic" "reporting_topic" {
  name = "synapse-reporting"

  depends_on = [google_project_service.services]
}

resource "google_cloud_scheduler_job" "reporting_job" {
  name        = "synapse-daily-report"
  description = "Trigger daily Synapse reporting"
  schedule    = "0 8,20 * * *" # 8 AM and 8 PM daily
  time_zone   = "UTC"
  region      = var.region

  pubsub_target {
    topic_name = google_pubsub_topic.reporting_topic.id
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
}

# Allow GitHub to impersonate the Deploy SA
resource "google_service_account_iam_member" "deploy_sa_wif_user" {
  service_account_id = google_service_account.deploy_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
}

# --- Project-level Roles ---

resource "google_project_iam_member" "terraform_sa_roles" {
  project = var.project_id
  role    = "roles/editor" # TODO: Note: 'editor' is broad. Scope this down for production.
  member  = google_service_account.terraform_sa.member
}

resource "google_project_iam_member" "deploy_sa_cloudfunctions" {
  project = var.project_id
  role    = "roles/cloudfunctions.developer"
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

#
# ========== MODULE CALLS ==========
#
# Here is where we replace the two large resource blocks
# with two small, clean module calls.
#

module "ingestion_function" {
  source = "./modules/cloud-function"

  # --- Inputs ---
  name                   = "synapse-ingestion"
  entry_point            = "ingestion_function"
  location               = var.region
  project_id             = var.project_id
  service_account_email  = google_service_account.function_sa.email
  source_bucket_name     = google_storage_bucket.function_bucket.name
  max_instance_count     = 10
  available_memory       = "512Mi"
  # event_trigger_topic_id is left unset (defaults to null)

  depends_on = [google_project_service.services]
}

module "reporting_function" {
  source = "./modules/cloud-function"

  # --- Inputs ---
  name                   = "synapse-reporting"
  entry_point            = "reporting_function"
  location               = var.region
  project_id             = var.project_id
  service_account_email  = google_service_account.function_sa.email
  source_bucket_name     = google_storage_bucket.function_bucket.name
  max_instance_count     = 1
  available_memory       = "256Mi"
  event_trigger_topic_id = google_pubsub_topic.reporting_topic.id # This function gets a trigger

  depends_on = [google_project_service.services]
}