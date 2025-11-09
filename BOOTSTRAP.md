### Bootstrap Process

1. **Authenticate as Admin:**

    ```bash
    gcloud auth application-default login --project=$(yq e '.gcp_project_id' config.yml)
    ```

2. **Modify Terraform Code:**

    * Commented out the entire `backend "gcs" {}` block in `infrastructure/terraform.tf`.

3. **Initialize Local Backend:**
    
    ```bash
    terraform init
    ```

4. **Apply Initial Infrastructure:**

    ```bash
    terraform apply
    ```

5. **Modify Terraform Code:**
    * Un-commented the `backend "gcs" {}` block in `infrastructure/terraform.tf`.

6. **Migrate State to GCS:**

    ```bash
    terraform init -migrate-state \
      -backend-config="bucket=$(yq e '.google_cloud_project_id' ../config.yml)-terraform-state" \
      -backend-config="prefix=synapse"
    ```
    * Confirmed `yes` at the prompt.

7. **Clean Up Local State:**

    ```bash
    rm terraform.tfstate
    ```
