# 3. Create the API resource
resource "google_api_gateway_api" "processor_api" {
  provider = google-beta
  project  = var.gcp_project_id
  api_id   = "processor-api"

  depends_on = [google_project_service.services]
}

resource "time_sleep" "wait_for_api_service" {
  # This implicit dependency is okay, but you can also make it explicit
  # by adding depends_on = [google_api_gateway_api.processor_api]
  create_duration = "15m" # 30-60 seconds is usually enough
}

# 4. Enable the managed service for the API
resource "google_project_service" "api_gateway_managed_service" {
  provider           = google-beta # Use beta provider for consistency
  project            = var.gcp_project_id
  service            = google_api_gateway_api.processor_api.managed_service
  disable_on_destroy = false # Keep it enabled
  depends_on = [
    time_sleep.wait_for_api_service,
    google_api_gateway_api.processor_api # Good to be explicit
  ]
}

# 5. Create the API Config
resource "google_api_gateway_api_config" "processor_api_config" {
  provider = google-beta
  project  = var.gcp_project_id
  api      = google_api_gateway_api.processor_api.api_id

  # This creates a new unique config ID on every change,
  # forcing a new revision.
  api_config_id_prefix = "intaker-config-"

  # This dynamically renders your spec file with the intake service's URL
  openapi_documents {
    document {
      path = "processor-spec.yml"
      contents = base64encode(templatefile("${path.root}/../api-gateways/intaker-spec-template.yml", {
        # This assumes your "intaker_service" module has an output named "service_url"
        # See "Required Change" section below.
        intaker_service_url = module.intaker_service.service_url
      }))
    }
  }

  # This replaces your pipeline's --backend-auth-service-account flag
  gateway_config {
    backend_config {
      google_service_account = google_service_account.api_gateway_sa.email
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    google_project_service.api_gateway_managed_service,
    module.intaker_service
  ]
}

# 6. Create the Gateway to make the config live
resource "google_api_gateway_gateway" "processor_gateway" {
  provider   = google-beta
  project    = var.gcp_project_id
  region     = var.region
  api_config = google_api_gateway_api_config.processor_api_config.id
  gateway_id = "processor-gateway"

  depends_on = [google_api_gateway_api_config.processor_api_config]
}