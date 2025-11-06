terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Add backend configuration for state management
  backend "gcs" {
    # bucket and prefix will be provided via backend-config in CI/CD
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}