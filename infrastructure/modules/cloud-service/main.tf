resource "google_cloud_run_service" "this" {
  name     = var.name
  location = var.location
  project  = var.gcp_project_id

  # This creates an "empty shell."
  # Your CI/CD pipeline will create the first revision
  # by deploying a container image.
  template {
    spec {
      service_account_name = var.service_account_email
      timeout_seconds      = var.timeout_seconds

      # The 'containers' block is required.
      # We provide a placeholder image that will be immediately
      # replaced by your CI/CD pipeline on its first deploy.
      containers {
        name  = var.name
        image = "gcr.io/google-samples/hello-app:1.0"

        resources {
          limits = {
            memory = var.available_memory
            cpu    = var.available_cpu
          }
        }
      }
    }

    metadata {
      annotations = {
        # This maps the Cloud Function's 'max_instance_count'
        "autoscaling.knative.dev/maxScale" = var.max_instance_count
      }
    }
  }

  # This is the most important part. We tell Terraform to
  # create the service, but NEVER manage the 'template'.
  # The CI/CD pipeline owns the template from now on.
  lifecycle {
    ignore_changes = [
      # Ignore *only* the container image path.
      # This allows Terraform to keep managing memory, scaling,
      # and other settings, while the CI/CD pipeline
      # manages the code.
      template[0].spec[0].containers[0].image,
    ]
  }
}