resource "google_api_gateway_api" "processor_api" {
  provider = google-beta
  project  = local.config.gcp_project_id
  api_id   = "processor-api"

  depends_on = [google_project_service.services]
}

resource "google_api_gateway_api_config" "processor_api_config" {
  provider = google-beta
  project  = local.config.gcp_project_id
  api      = google_api_gateway_api.processor_api.api_id

  api_config_id_prefix = "intaker-config-"

  openapi_documents {
    document {
      path = "processor-spec.yaml"
      contents = base64encode(templatefile("${path.root}/../api-gateways/intaker-spec-template.yaml", {
        intaker_service_url = module.intaker_service.service_url
      }))
    }
  }

  gateway_config {
    backend_config {
      google_service_account = google_service_account.api_gateway_sa.email
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    google_api_gateway_api.processor_api,
    module.intaker_service
  ]
}

resource "google_project_service" "api_gateway_managed_service" {
  provider           = google-beta
  project            = local.config.gcp_project_id
  service            = google_api_gateway_api.processor_api.managed_service
  disable_on_destroy = false
}

resource "google_api_gateway_gateway" "processor_gateway" {
  provider   = google-beta
  project    = local.config.gcp_project_id
  region     = local.config.region
  api_config = google_api_gateway_api_config.processor_api_config.id
  gateway_id = "processor-gateway"

  # This depends on the service being enabled, which depends on everything else
  depends_on = [google_project_service.api_gateway_managed_service]
}