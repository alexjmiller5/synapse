# Project Synapse 🧠

> An intelligent middleware for capturing thoughts and organizing them in Notion.

Synapse is a serverless application designed to eliminate the friction of manual data entry in Notion. It accepts unstructured, natural-language text, uses a multi-step AI chain to understand and structure the content, and then routes it to the correct database or page in a Notion workspace. The entire project is written in **Python**.

---

## Architecture Overview

- **IaC:** **Terraform** manages all GCP resources (Functions, IAM, Secrets) as versioned code.
- **CI/CD:** **GitHub Actions** deploys on push to `main` using **GCP Workload Identity Federation** for keyless authentication.
- **Google Cloud Functions**:
- **`intaker`**: HTTP-triggered API endpoint for categorizing data and sending it to Notion.
- **`reporter`**: Cron-triggered (Cloud Scheduler) for twice-daily email summaries.
- **Gemini 2.5 Flash**: Low-latency intent classification and entity extraction.
- **Gemini 2.5 Pro**: Complex data structuring and schema-based decisions.
- **Google Secret Manager** stores all keys (Notion token, Gmail password), accessed via IAM.
- **Gmail**: Email reports sent by `reporter-function` via `smtplib` and a **Gmail App Password**.
- **API Gateway**: Secures and manages access to the `intaker` function.

## Architecture Diagram
<!-- TODO: Create an architecture diagram of the flow of my services -->
<!-- ![Architecture Diagram](docs/synapse_architecture_diagram.png) -->

---

## Development Setup

### Required Tools

1. **Python 3.13+**
2. **uv**
3. **Google Cloud SDK**
4. **Terraform**

### Project Setup

#### Account Creation and Secrets Configuration

1. Create a Google Cloud Project
2. Save the settings to the config.yaml file as below

```yaml
gcp_project_id: <your-gcp-project-id>
gcp_project_number: <your-gcp-project-number>
region: <your-gcp-region>
github_repo: <your-github-repo>
```

##### Creating terraform backend bucket and migrating state for initial setup

1. **Authenticate as Admin:**

    ```bash
    gcloud auth application-default login --project=$(yq e '.gcp_project_id' config.yaml)
    ```

2. **Modify Terraform Code:**

    - Commented out the entire `backend "gcs" {}` block in `infrastructure/terraform.tf`.

3. **Initialize Local Backend:**

    ```bash
    terraform init
    ```

4. **Apply Initial Infrastructure:**

    ```bash
    terraform apply
    ```

5. **Modify Terraform Code:**
    - Un-commented the `backend "gcs" {}` block in `infrastructure/terraform.tf`.

6. **Migrate State to GCS:**

    ```bash
    terraform init -migrate-state \
      -backend-config="bucket=$(yq e '.google_cloud_project_id' ../config.yaml)-terraform-state" \
      -backend-config="prefix=synapse"
    ```

    - Confirmed `yes` at the prompt.

7. **Clean Up Local State:**

    ```bash
    rm terraform.tfstate
    ```

<!-- Gotta add however I got the ci / cd auth working cause that's part of the bootstrappinga -->

1. Generate and set the secrets via the set secrets script

```bash
./scripts/set_secrets.sh
```

### Local Development

1. **Set up local development environment :**

 ```bash
 ./scripts/setup_local_env.sh
 ```

1. **Test Functionality Locally:**

- **Run the function(s) you are interested in testing:**

```bash
cd <python-service-directory>
uv run functions-framework --target=<name-of-python-function> --source=<python-service-main-file> --debug
```

OR use the justfile command:

```bash
just run-<python-package-name>
```

- **Add the following to your shell's startup file:**

```bash
syn-local() {
local url="http://localhost:8080"
local input_text="$*"

if [ -z "$input_text" ]; then
echo "Usage: syn-local <text>"
return 1
fi

echo "🚀 Sending: '$input_text'..."

local payload
payload=$(python3 -c "import sys, json, base64; print(json.dumps({'message': {'data': base64.b64encode(sys.argv[1].encode('utf-8')).decode('utf-8'), 'attributes': {}}}))" "$input_text")

curl -X POST "$url" \
-H "Content-Type: application/json" \
-H "Ce-Id: 123456789" \
-H "Ce-Specversion: 1.0" \
-H "Ce-Type: google.cloud.pubsub.topic.v1.messagePublished" \
-H "Ce-Source: //pubsub.googleapis.com/projects/synapse/topics/local" \
-d "$payload"

echo ""
}
```

- **Send test requests to your local function:**

```bash
syn-local "Your test input text here"
```

- **Send local batch requests (fill the `local-requests.txt` file with your requests separated by newlines):**

```bash
just recept-local-batch
```

- **Test infrastructure changes with Terraform:**

```bash
cd infrastructure/
terraform init
terraform plan -var="project_id=your-gcp-project-id" -var="region=your-gcp-region"
```

---

## Configuration

### Getting an API Key for Receptors

- Run the following commands to create or find and print the processor service API Key

```bash
# --- Configuration ---
export KEY_DISPLAY_NAME="Intaker Gateway Key"
export API_ID="intaker-api" # TODO:The ID set in the terraform code (Should be moved to the config.yaml)

# --- Execution ---

# 1. Create the API Key
gcloud services api-keys create --display-name="$KEY_DISPLAY_NAME"

# 2. Programmatically get the Key Resource Name (projects/.../keys/...)
KEY_NAME=$(gcloud services api-keys list --filter="displayName:'$KEY_DISPLAY_NAME'" --format="value(name)" --limit=1)

# 3. Programmatically get the Managed Service Name for the Gateway
MANAGED_SERVICE=$(gcloud api-gateway apis describe "$API_ID" --format="value(managedService)")

# 4. Apply restrictions to the key using the retrieved service name
gcloud services api-keys update "$KEY_NAME" --api-target="service=$MANAGED_SERVICE"

# 5. Output the actual usable Key String
echo "Setup Complete. Your API Key is:"
gcloud services api-keys get-key-string "$KEY_NAME" --format="value(keyString)"
```

#### Setting and managing secrets

Use the scripts/manage_secrets.sh script to create and update secrets in Google Secret Manager.

```bash
./scripts/set_secrets.sh
```

### Config variables in config.yaml

- `gmail_sender_email`: Gmail address for sending reports
- `gmail_recipient_email`: Email to receive reports
- `gcp_project_id`: GCP project ID
- `github_repo`: GitHub repository URL
- `region`: GCP region for cloud resources

### Adding New Notion Databases in `databases.yaml`

To add a new Notion database to Synapse:

1. Add the Database ID to Google Secret Manager as `notion-<category>-db-id`.
2. Add the category definition to `databases.yaml` using the schema below.

#### 1. Database Level

| Field | Required | Usage |
| :--- | :--- | :--- |
| **`description`** | ✅ Yes | **The Classifier Prompt.** Used by the AI to decide if an incoming item belongs to this category. Be descriptive (e.g., *"A movie to watch..."* vs *"A generic task..."*). |
| **`properties`** | ✅ Yes | A dictionary mapping **Exact Notion Column Names** to their configuration rules. |

---

#### 2. Property Level

Each key under `properties` must match the column name in Notion exactly (case-sensitive).

##### Core Fields

- **`type`**: *(String)* Defines how the data is sent to Notion and how the AI validates it.
  - **Options:** `title`, `rich_text`, `url`, `date`, `select`, `multi_select`, `status`, `relation`, `checkbox`.
- **`required`**: *(Boolean)*
  - `true`: The AI is forced to generate a value for this field.
  - `false` (Default): The AI may leave this field empty/null if the user didn't provide info.
- **`instruction`**: *(String)* **The Extraction Prompt.** Tells the AI how to extract or format this specific data.
  - *Tip:* You can use placeholders `{current_date}` and `{raw_text}`.

##### Logic Control

- **`virtual`**: *(Boolean)*
  - `true`: **Hides this field from the AI.** The AI will not see or output this field. It is populated strictly by Python code (e.g., `Date Created`, calculated statuses).
  - `false` (Default): The AI attempts to extract this data.
- **`allowlist`**: *(List of Strings)*
  - Defines valid options for `select`, `multi_select`, or `status` types.
  - The AI is strictly restricted to these values (it becomes an `enum` in the JSON schema).
  - If this is omitted, the AI may select any of the valid options defined in Notion.
- **`create_new`**: *(Boolean)*
  - `true`: overrides the strictness of `allowlist`. The AI is shown the existing options but is **allowed** to generate new strings (useful for dynamic Tags/Cities).
  - `false` (Default): The AI must pick from the `allowlist` or the runtime inventory; if it invents a new tag, validation will fail.

---

## Deployment

### Using GitHub Actions

1. Push to the `main` branch
2. GitHub Actions will automatically run tests and deploy

---

## Useful Commands

- The API Gateway creates a managed service by default called `api-gateway-<api-id>.<project-id>.cloud.goog`. To find the details of the API Gateway use one of the following commands:

```bash
gcloud endpoints services list \
--project="<project-id>"

gcloud api-gateway apis describe processor-api
```

- Find the URL of the deployed `processor` api gateway URL:

```bash
gcloud api-gateway gateways describe processor-gateway \
--location=us-east1 \
--project=<project-id>
```

- Add a package to a specific service's pyproject.toml and install it in the local development environment:

```bash
uv add --package <workspace-member-name> <package-name>
```

- Delete a secret from `gcloud`

```bash
gcloud secrets delete <secret-name> --project=<project-id>
```

---

## Useful Links

- [Gemini Studio Usage](https://aistudio.google.com/usage?project=nimble-acrobat-422115-q8)
- [Notion Developer Portal](https://www.notion.so/my-integrations)
- [Gemini Chat About Design](https://gemini.google.com/app/59fe912cc890af6d)
- [GCloud Project Functions](https://console.cloud.google.com/run?deploymentType=function&project=synapse-477401)
- [GitHub Repository](https://github.com/alexjmiller5/synapse)
- [TMDB API Reference](https://developer.themoviedb.org/reference/authentication)

## Synapse Prompting Guide

- **Core Syntax**
- **`@` Splitter:** Separate multiple distinct items in one message.
- **`$` Context:** Define the Project, Date, Status, or specific Category.

- **Defaults (If NOT specified)**
- **Tasks:** Status: `To Do` | Tag: `Chore` | Date: `Today`
- **Movies/TV:** Status: `Not Started`
- **YouTube:** Status: `Watched` | Date Watched: `Today`
- **Podcasts:** Status: `Not Started`
- **Fun Activities:** Status: `To Do`
- **Groceries:** Status: `On List`

- **Category Cheatsheet**
- **Tasks (Default):** `Update hinge profile` (Solo actions = Chore).
- **Projects:** `Refactor code $ Synapse` (**Strict Rule:** Must name project in context).
- **URLs:** `https://...` (Auto-routes to **YouTube**, **Bookmarks**, or **Podcasts** which are spotify and thisamericanlife urls).
- **People:** `Will Tkay Caleb's Friend` (Pattern: `Name $ Company`).
- **Movies/TV:** `Love is blnd $ watched` (Typos are auto-fixed via TMDB).
- **Fun Activity/Bucket List:** `Skydiving $ bucket list` or `Walk around Seaport $ fun`.

- **Dates & Status**
- **Due Date:** `Cancel Uber One $ Jan 1` (Context overrides text).
- **Media Status:** `The Matrix $ movie priority` or `Severance $ show finished`.

- **Batch Example:**
- `Rishi TDP Senior Associate @ https://youtu.be/xyz @ Buy eggs $ groceries`
