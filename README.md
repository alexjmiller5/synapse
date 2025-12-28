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

1. Create a Google Cloud Project
2. Save the settings to the config.yml file as below

```yaml
gcp_project_id: <your-gcp-project-id>
gcp_project_number: <your-gcp-project-number>
region: <your-gcp-region>
github_repo: <your-github-repo>
```

1. Generate and set the secrets via the set secrets script

```bash
./scripts/set-secrets.sh
```

### Local Development

1. **Set up local development environment :**

 ```bash
 eval $(./scripts/setup_local_env.sh)
 ```

1. **Test Functionality Locally:**

- **Run the function(s) you are interested in testing:**

```bash
uv run functions-framework --target=<name-of-python-function> --source=<path-to-python-service> --debug
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
uv run --with requests send-local-requests.py local-requests.txt
```

- **Test infrastructure changes with Terraform:**

```bash
cd infrastructure/
terraform init
terraform plan -var="project_id=your-gcp-project-id" -var="region=your-gcp-region"
```

---

## Configuration

### Getting an API Key for Shortcuts

- Run the following commands to create or find and print the processor service API Key

```bash
# --- Configuration ---
export KEY_DISPLAY_NAME="Processor Gateway Key"
export API_ID="processor-api" # TODO:The ID set in the terraform code (Should be moved to the config.yaml)

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
chmod +x ./scripts/set-secrets.sh
./scripts/set-secrets.sh
```

### Config variables in config.yml

- `gmail_sender_email`: Gmail address for sending reports
- `gmail_recipient_email`: Email to receive reports
- `gcp_project_id`: GCP project ID
- `github_repo`: GitHub repository URL
- `region`: GCP region for cloud resources

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

---

---

## Useful Links

- [Gemini Studio Usage](https://aistudio.google.com/usage?project=nimble-acrobat-422115-q8)
- [Notion Developer Portal](https://www.notion.so/my-integrations)
- [Gemini Chat About Design](https://gemini.google.com/app/59fe912cc890af6d)
- [GCloud Project Functions](https://console.cloud.google.com/run?deploymentType=function&project=synapse-477401)
- [GitHub Repository](https://github.com/alexjmiller5/synapse)
- [TMDB API Reference](https://developer.themoviedb.org/reference/authentication)

## General thoughts and important TODOs

### **Dev Coding Related Stuff**

- [ ] Do all the todos in the comments scattered about
- [x] how do i deal with auth wthin my shortcut to ping my cloud function?. What to do... have to figure that out
- [x] Let the cloud functions do most things in one go. They’re really cheap and the logic for setting up aynschronous api calls and responses looks really complicated. My singular preocessor function might just be good for all the logic. I think the Gemini and notion APIs will be pretty much fast enough
- [ ] I can still use some kinda script to validate that the requests actually make sense with some kinda json validator. Maybe I can start with a template for adding a new page to a database (this’ll be the most common request) and patching the quick notes as the fallback. Those are the two key capabilities and ai doesn’t really have to write that json instead of writing json and using requests actually ill just use the notion client for python and give it the proper input for allxthe properties I need. Just have to convert it from the ais way of interpreting it vs the programmatic json notion client way of seeing the data
- [ ] add dependabot to keep dependencies up to date functionality \-- this would be a nice to have functionality that I'll just let sit there and eventually old dependencies will be updated automatically over time and I'll learn about how it works
- [ ] Add terragrunt to be able to manage the version of terraform in one place (the config.yml) instead of two \-- also it gives flexibility in the future if i have mulitple environments. It also has an autoinit feature where I don't have to run terraform init
- [ ] terraform version is managed via the main.tf file in addition to the github actions env var \-- those need to be kept in sync. Other dependency versions are just kept in the pyproject.toml
- [ ] create an automated solution / docs for updating the secrets in GCP so that it's documented and not solely based manually updating them in the UI
- [ ] cut down the scripts in the scripts folder. \-- they are kind of verbose with a lot of comments and I haven't looked through them in depth
- [ ] set up a container only keep latest 10 images in artifact registry to save on costs
- [ ] eventually I should set up the services to just call cloud run jobs in the background instead of cloud functions. This would be more scalable and I wouldn't have to worry about function timeouts. But for now functions are fine
- [ ] eventually make environment variables in deploy.yml to be uppercase since they're lowercase rn since they take from the config.yml file
- [ ] Add something about how when you add new config variables you gotta rerun the setup script to get them in the tfvars file
- [ ] refactor the setup local env script to work with python file structure / figure out how the functions framework works with local development and set it up with my project's structure. Do i want to have a bunch of duped pyproject.tomls???
- [x] Add authentication to the cloud function so that not just anyone can ping it there's a firebase api and something else i'll need \-- i'll add this after i get the pipeline working
- [ ] make the terraform plan and apply step in the pipeline autmatically get the environment variables from the yaml file and set them as vars when they run instead of having them hardcoded
- [ ] learn more about uv and consider how to use it instead of stupid uv pip install \-e everywhere
- [ ] Figure out why the terraform deployment service account doesn't have access to modify EVERYTHING \-- it really should especailly iam roles
- [ ] Figure out if there's a better way to deploy each coud function separate isntead of the matrix \-- they seem to be edpendent on each other rn and shouldn't be \-- I'm considering giving each its own deployment pipeline
- [ ] add pertinent info to github step summary markdown file from pipeline
- [ ] pin my python version for my functions
- [ ] write a custom dockerfile to use uv for my cloud function instead of using buildpack because buildpack only supports requirements.txt
- [ ] add a min-instances=1 to the cloud run services to avoid cold starts \-- this needs to be configured within terraform most likely
- [ ] Whenever I set up a Dockerfile, set up cloud run to use tailscale so that I can enable public access only within my tailscale network
- [ ] reorganize main.tf and delete unecessary comments
- [ ] keep api gateway even with tailscale but write it with gcloud beta in terraform
- [ ] make the api gateway only deploy if the api-gateways folder is modified
- [ ] Figure out how to set up an src layout for my pyproject.toml to avoid issues with imports when running locally with functions framework and also then my deployment probably needs to be refacted cause the source / entrypoint will need the multiple files and shit
- [ ] move my gemini api key from my personal services project to my synpase project
- [ ] remove all the gpt comments that are unecessary
- [ ] Set up notion Properties to be mapped as IDs instead of names to avoid issues when i change the names of properties in notion
- [ ] Set up notion webhooks to notify me when properties are deleted or changed that are used in my code so i can update them accordingly
- [ ] Think of a way to avoid hardcoding the property ids directly in the code. Maybe have a config file in notion that maps property names to ids and have the code read that at runtime or deployment time -- try to think of some kinda code that determines the property ids based on the names at deployment time and stores them in environment variables or a config file and automatically figures out which property is which -- As I'm thinking about this, I did the DB ids as secrets which is fine, but I kinda wanna put all these property ids in my config.yaml or maybe make a yaml specifically for the processor function -- yeah that's the best idea -- just gonna be big yaml tables lmao coordinated with the db ids I've stored as secrets since that's how I'm determining which dbs to use for this project
- [ ] put all my secrets keys into my config.yaml -- leave the values out but this way my terraform code will create secrets from the names in the config.yaml (it'll just read every single name within a secret list and make em all) and my actualy code will read the keys from the config.yaml as well. My secret setup script can also read the secrets from the config.yaml and verify that the config.yaml is a mirror of gcp secret manager (rn it just checks gcp manager) for an extra step of verification
- [ ] make some kinda script which copies the config.yaml into the cloud run functions folders at deploy time so they have access to it and i can stop hardcoding stuff
- [ ] rewrok the setuplocalenv script to use uv correctly -- idrk how it works tbh there's uv sync, uv add, uv pip install, where does the venv get created? Also how does the pyproject.toml work with having various projects? Should they even have one at the root level? Maybe not? But it seems like maybe lmaooo
- [ ] It would be great to not be hardcoding my helper dbs --the logs and youtube-channels, but it is what it is for now
- [ ] make it so the hydration only happens on a per category/db basis after classification is done so it doesn't have to hydrate everything all at the beginning
- [ ] add checks at the beginning of my function to make sure that the synapse_config.yaml matches the actual notion db structure -- this way if i change something in notion and forget to update the config file, the function will error out and maybe send me an email? and notify me instead of just failing silently or misbehaving
- [ ] Make a standardized duplicate checking function instead of specific one for each db
- [ ] Separate the infra into a separate repo

### **Feature Related Stuff**

- [ ] something else good would be for this is making sure to store the logs of all the queries somewhere and how they were analyzed so i can manually mark them as success and failure. Also a marked on my notion on whether or no it was ai added.
- [ ] Make it so that links are included in the links property if they're found in the quick note free test
- [ ] Also all i need for an ai summary is a cloud function webhook which is trigger by a notion automation every time a new task is made. or instead i could just have a webhook set up via the notion api that pings my cloud function when a new task is made. Then the cloud function can make a patch request to update the ai summarized title. This would be super useful because then every task would have an ai summarized title automatically
- [ ] add functionality where links are recognized by gemini and verified by code later to be actual links and then added to the list of links text proerpty in notion for tasks
- [ ] write a validate db function which validates that a db needs a certain structure to be added to it and if it doesn't have that structure it gets sent to quick notes instead with information about the mismatch
- [ ] Get ai to help me make my giant conditional statements into a flowchart so I can see high level how requests are being processed and how the decisions are made by ai. Some decsions that are sticking out to me: intent getting from the ai, getting from the ai's knowledge (i.e. asking it to fill in the producer of a movie), summarizing a title/description, then this all plays into either page creation or page modification
- [ ] Fetch the notes of the project in addition to the titles to make sure the ai will be able to have the right when determining if a raw text belongs with a project
- [ ] Refactor this readme to be a clear guide on how to setup synapse from the ground up if nothing was created because rn there's a lot of stuff that's not actually useful or specific to my implementation of the project. Then there can be a section about how this project is designed and a section about how to use it well

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
- **Tasks (Default):** `Update dating profile` (Solo actions = Chore).
- **Projects:** `Refactor code $ Synapse` (**Strict Rule:** Must name project in context).
- **URLs:** `https://...` (Auto-routes to **YouTube**, **Bookmarks**, or **Podcasts** which are spotify and thisamericanlife urls).
- **People:** `Will Barlow Theo's Friend` (Pattern: `Name $ Company`).
- **Movies/TV:** `Love is blnd $ watched` (Typos are auto-fixed via TMDB).
- **Fun Activity/Bucket List:** `Skydiving $ bucket list` or `Walk around Seaport $ fun`.

- **Dates & Status**
- **Due Date:** `Cancel Uber One $ Jan 1` (Context overrides text).
- **Media Status:** `The Matrix $ movie priority` or `Severance $ show finished`.

- **Batch Example:**
- `Arun Vantage Senior Associate @ https://youtu.be/xyz @ Buy eggs $ groceries`
