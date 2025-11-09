#!/bin/bash
set -e
#
# Synapse Project - Local Development Setup Script
#
# This script sets up your local shell for development. It:
# 1. Checks dependencies (uv, gcloud, terraform, yq).
# 2. Creates the local terraform.tfvars file from config.yml.
# 3. Ensures your Python virtual environment is set up and synced.
# 4. Checks your GCP "Application Default Credentials" (ADC).
#
# USAGE:
# 1. Make it executable: chmod +x ./scripts/setup-local-env.sh
# 2. Run it with 'eval': eval $(./scripts/setup-local-env.sh)
#
# The 'eval' is necessary to activate the virtual environment
# in your current shell.
# NOTE: This script can now be run from any directory.

# Find the project root (the directory this script's parent is in)
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )

CONFIG_FILE="$PROJECT_ROOT/config.yml"
TF_VARS_FILE="$PROJECT_ROOT/infrastructure/terraform.tfvars"
VENV_DIR="$PROJECT_ROOT/.venv"

# --- Helper Functions ---

# Function to check if a command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "💥 Error: $1 is not installed." >&2
        echo "Please install $1 and try again." >&2
        exit 1
    fi
}

# Function to read from config.yml
get_config() {
    yq e ".$1" "$CONFIG_FILE"
}

# --- Setup Functions ---

# 1. Check all required dependencies
check_dependencies() {
    check_command "uv"
    check_command "gcloud"
    check_command "terraform"
    check_command "yq"
}

# 2. Set up local terraform.tfvars
setup_terraform() {
    if [ -f "$TF_VARS_FILE" ]; then
        echo "✅ Terraform variables file already exists at $TF_VARS_FILE."
    else
        echo "🚀 Creating local $TF_VARS_FILE from config..."
        
        if [ ! -d "$PROJECT_ROOT/infrastructure" ]; then
            echo "Error: 'infrastructure' directory not found at $PROJECT_ROOT/infrastructure." >&2
            exit 1
        fi

        # Dynamically read all flat keys from config.yml and format as HCL
        yq e 'to_entries | .[] | select(.value | (tag != "!!map" and tag != "!!seq")) | .key + " = \"" + .value + "\""' "$CONFIG_FILE" > "$TF_VARS_FILE"
        
        echo "✅ Created $TF_VARS_FILE."
        echo "IMPORTANT: Make sure '$TF_VARS_FILE' is in your .gitignore file!"
    fi
}

# 3. Set up Python virtual environment
setup_python() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "🚀 Creating Python virtual environment with uv at $VENV_DIR..."
        # Run uv venv from the project root
        (cd "$PROJECT_ROOT" && uv venv)
    fi
    
    echo "📦 Installing service dependencies..."
    
    # Install services in editable mode into the root venv.
    # This reads their pyproject.toml files and installs
    # them AND their dependencies.
    # We use --python to ensure it installs into the venv
    # even though it's not activated in this subshell.
    uv pip install -e "$PROJECT_ROOT/services/processor" --python "$VENV_DIR/bin/python" --quiet
    uv pip install -e "$PROJECT_ROOT/services/reporter" --python "$VENV_DIR/bin/python" --quiet
    
    echo "✅ Python environment is ready."
}

# 4. Check GCP Authentication
check_gcloud_auth() {
    local project_id
    project_id=$(get_config "gcp_project_id")
    
    # Check for Application Default Credentials (ADC)
    if gcloud auth application-default print-access-token &>/dev/null; then
        echo "✅ GCP Application Default Credentials are active."
    else
        echo "⚠️ GCP Application Default Credentials (ADC) not found." >&2
        echo "Your Python code needs these to access GCP services (like Secret Manager)." >&2
        echo "Please run the following command to log in:" >&2
        echo "" >&2
        echo "  gcloud auth application-default login --project=$project_id" >&2
        echo "" >&2
        exit 1 # Exit because the application code will fail
    fi
    
    # Set the project for gcloud commands (good practice)
    gcloud config set project "$project_id" &> /dev/null
}


# --- Main Execution ---

main() {
    check_dependencies >&2
    setup_terraform >&2
    setup_python >&2
    check_gcloud_auth >&2
    
    # These commands MUST go to stdout to be captured by eval
    
    # 1. Activate the virtual environment
    echo "source $VENV_DIR/bin/activate"
    
    # 2. Set project ID as an env var (useful for testing, but not for secrets)
    local project_id
    project_id=$(get_config "gcp_project_id")
    echo "export GOOGLE_CLOUD_PROJECT=$project_id"

    # 3. Add project root to PYTHONPATH for local imports
    echo "export PYTHONPATH=$PROJECT_ROOT"

    echo "🎉 Local environment is configured!" >&2
    echo "Python virtual env is active and GCP auth is verified." >&2
    echo "Your application code will now fetch secrets directly." >&2
    echo "You can now run your services using the 'functions-framework' command." >&2
}

# Run the main function
main