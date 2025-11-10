# Project Synapse 🧠

> An intelligent middleware for capturing thoughts and organizing them in Notion.

Synapse is a serverless application designed to eliminate the friction of manual data entry in Notion. It accepts unstructured, natural-language text, uses a multi-step AI chain to understand and structure the content, and then routes it to the correct database or page in a Notion workspace. The entire project is written in **Python**.

---

## Architecture Overview

- **IaC:** **Terraform** manages all GCP resources (Functions, IAM, Secrets) as versioned code.
- **CI/CD:** **GitHub Actions** deploys on push to `main` using **GCP Workload Identity Federation** for keyless authentication.
- **Google Cloud Functions**:
  - **`processor`**: HTTP-triggered API endpoint for categorizing data and sending it to Notion.
  - **`reporter`**: Cron-triggered (Cloud Scheduler) for twice-daily email summaries.
- **Gemini 2.5 Flash**: Low-latency intent classification and entity extraction.
- **Gemini 2.5 Pro**: Complex data structuring and schema-based decisions.
- **Google Secret Manager** stores all keys (Notion token, Gmail password), accessed via IAM.
- **Gmail**: Email reports sent by `reporter-function` via `smtplib` and a **Gmail App Password**.
- **API Gateway**: Secures and manages access to the `processor` function.

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

### Local Development

1. **Set up local development environment :**

   ```bash
   chmod +x ./scripts/setup-local-env.sh
   eval $(./scripts/setup_local_env.sh)
   ```

2. **Test Functionality Locally:**
- **Run the function(s) you are interested in testing:**

```bash
functions-framework --target=<name-of-python-function> --source=<path-to-python-service> --debug
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

```

### Required Secrets (Google Secret Manager)

- gemini-api-key
- notion-api-token
- notion-tasks-db-id
- notion-quick-notes-last-block-id
- gmail-sender-email
- gmail-app-password
- gmail-recipient-email

#### Setting and managing secrets

Use the scripts/manage_secrets.sh script to create and update secrets in Google Secret Manager.

### Config variables in config.yml

- `gmail-sender-email`: Gmail address for sending reports
- `gmail-recipient-email`: Email to receive reports
- `gcp-project-id`: GCP project ID
- `github-repo`: GitHub repository URL
- `region`: GCP region for cloud resources

---

## Deployment

### Using GitHub Actions

1. Push to the `main` branch
2. GitHub Actions will automatically run tests and deploy

---

## Useful Links

- [Gemini Studio Usage](https://aistudio.google.com/usage?project=nimble-acrobat-422115-q8)
- [Notion Developer Portal](https://www.notion.so/my-integrations)
- [Gemini Chat About Design](https://gemini.google.com/app/59fe912cc890af6d)
- [GCloud Project Functions](https://console.cloud.google.com/run?deploymentType=function&project=nimble-acrobat-422115-q8)
- [GitHub Repository](https://github.com/alexjmiller5/synapse)

## General thoughts and important TODOs

- Do all the todos in the comments scattered about
- how do i deal with auth wthin my shortcut to ping my cloud function?. What to do... have to figure that out
- Let the cloud functions do most things in one go. They’re really cheap and the logic for setting up aynschronous api calls and responses looks really complicated. My singular preocessor function might just be good for all the logic. I think the Gemini and notion APIs will be pretty much fast enough
- I can still use some kinda script to validate that the requests actually make sense with some kinda json validator. Maybe I can start with a template for adding a new page to a database (this’ll be the most common request) and patching the quick notes as the fallback. Those are the two key capabilities and ai doesn’t really have to write that json instead of writing json and using requests actually ill just use the notion client for python and give it the proper input for allxthe properties I need. Just have to convert it from the ais way of interpreting it vs the programmatic json notion client way of seeing the data
- something else good would be for this is making sure to store the logs of all the queries somewhere and how they were analyzed so i can manually mark them as success and failure. Also a marked on my notion on whether or no it was ai added.
- add dependabot to keep dependencies up to date functionality -- this would be a nice to have functionality that I'll just let sit there and eventually old dependencies will be updated automatically over time and I'll learn about how it works
- Add terragrunt to be able to manage the version of terraform in one place (the `config.yml`) instead of two -- also it gives flexibility in the future if i have mulitple environments. It also has an autoinit feature where I don't have to run terraform init
- terraform version is managed via the main.tf file in addition to the github actions env var -- those need to be kept in sync. Other dependency versions are just kept in the pyproject.toml
- i should honestly' make a separate infra repo for the terraform code and dpeloyment script
- Make it so that links are included in the links property if they're found in the quick note free test
- create an automated solution / docs for updating the secrets in GCP so that it's documented and not solely based manually updating them in the UI
- Also all i need for an ai summary is a cloud function webhook which is trigger by a notion automation every time a new task is made. or instead i could just have a webhook set up via the notion api that pings my cloud function when a new task is made. Then the cloud function can make a patch request to update the ai summarized title. This would be super useful because then every task would have an ai summarized title automatically
- cut down the scripts in the scripts folder. -- they are kind of verbose with a lot of comments and I haven't looked through them in depth
- add functionality where links are recognized by gemini and verified by code later to be actual links and then added to the list of links text proerpty in notion for tasks
- set up a container only keep latest 10 images in artifact registry to save on costs
- eventually I should set up the services to just call cloud run jobs in the background instead of cloud functions. This would be more scalable and I wouldn't have to worry about function timeouts. But for now functions are fine
- eventually make environment variables in deploy.yml to be uppercase since they're lowercase rn since they take from the config.yml file
- Add something about how when you add new config variables you gotta rerun the setup script to get them in the tfvars file
- refactor the setup local env script to work with python file structure / figure out how the functions framework works with local development and set it up with my project's structure. Do i want to have a bunch of duped pyproject.tomls???
- Add authentication 2.0 to the cloud function so that not just anyone can ping it there's a firebase api and something else i'll need -- i'll add this after i get the pipeline working
- make the terraform plan and apply step in the pipeline autmatically get the environment variables from the yaml file and set them as vars when they run instead of having them hardcoded
- learn more about uv and consider how to use it instead of stupid uv pip install -e everywhere
- Figure out why the terraform deployment service account doesn't have access to modify EVERYTHING -- it really should especailly iam roles
- Figure out if there's a better way to deploy each coud function separate isntead of the matrix -- they seem to be edpendent on each other rn and shouldn't be -- I'm considering giving each its own deployment pipeline
- add pertinent info to github step summary markdown file from pipeline
- pin my python version for my functions
- write a custom dockerfile to use uv for my cloud function instead of using buildpack because buildpack only supports requirements.txt
- add a min-instances=1 to the cloud run services to avoid cold starts -- this needs to be configured within terraform most likely
- Whenever I set up a Dockerfile, set up cloud run to use tailscale so that I can enable public access only within my tailscale network
- reorganize main.tf and delete unecessary comments
