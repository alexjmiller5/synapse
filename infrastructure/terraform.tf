terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.9"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Add backend configuration for state management
  backend "gcs" {
    # bucket and prefix will be provided via backend-config in CI/CD
  }
}

provider "google" {
  project = local.config.gcp_project_id
  region  = local.config.region
}

provider "google-beta" {
  project = local.config.gcp_project_id
  region  = local.config.region
}