# One-Time Project Bootstrap Guide

This document outlines the one-time "chicken and egg" process that was performed to initialize this project's infrastructure. This process is **not** part of the normal development workflow and only needs to be done once.

## The Problem (The "Chicken & Egg")

The project's CI/CD pipeline (`terraform.yaml`) is designed to authenticate using a GCP Service Account (`terraform-sa`) and store its state in a GCS bucket (`...-terraform-state`).

However, on the very first run, neither the Service Account nor the GCS bucket existed. The pipeline would fail because it couldn't authenticate or find its state.

## The Solution (The "Bootstrap")

The solution was to use a human admin's local credentials to run Terraform *first*. This "bootstrapped" the core infrastructure, allowing the CI/CD pipeline to take over for all future runs.

## Steps Performed

1.  **Authenticated as Human Admin:**
    We used the `gcloud` SDK to log in with a human user account that had `Owner` or `Editor` permissions on the project.
    ```
    gcloud auth application-default login --project=$(yq e '.google_cloud_project_id' config.yaml)
    ```

2.  **Prepared Local Environment:**
    We ran the local setup script to prepare the terminal.
    ```
    eval $(./local_setup.sh)
    ```

3.  **Initialized Terraform Locally:**
    We navigated to the `infrastructure/` directory and ran `terraform init`. Because no backend configuration was provided, Terraform defaulted to using the **local backend**, creating a `terraform.tfstate` file on the local disk.

4.  **Applied Initial Infrastructure:**
    We ran `terraform apply`. Using the local human admin's credentials, Terraform successfully created all the resources defined in the code. This included:
    * The `...-terraform-state` GCS bucket.
    * The `terraform-sa` and `deploy-sa` Service Accounts.
    * The Workload Identity Federation settings.
    * All other resources (Cloud Functions, Pub/Sub, etc.).

5.  **Migrated State to GCS:**
    After the `apply` finished, the state file was still on the local disk, but the remote GCS bucket now existed. We ran `terraform init` *again*, this time providing the backend configuration to migrate the state.
    ```
    terraform init -migrate-state \
      -backend-config="bucket=$(yq e '.google_cloud_project_id' ../config.yaml)-terraform-state" \
      -backend-config="prefix=synapse"
    ```
    Terraform detected the local state, connected to the GCS backend, and prompted to copy the state. We confirmed `yes`.

## Conclusion

This one-time process successfully created the core infrastructure and moved the `terraform.tfstate` file into its permanent home in the GCS bucket.

All future infrastructure changes are now handled exclusively by the GitHub Actions CI/CD pipeline, as intended.