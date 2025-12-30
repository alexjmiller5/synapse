#!/bin/bash
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )
CONFIG_FILE="$PROJECT_ROOT/config.yaml"

check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "💥 Error: $1 is not installed." >&2
        echo "Please install $1 and try again." >&2
        exit 1
    fi
}

check_command "uv"
check_command "gcloud"
check_command "terraform"
check_command "yq"

echo "🚀 Setting up Python virtual environment and dependencies..."
uv sync
echo "✅ Python environment is ready."

project_id=$(yq e ".gcp_project_id" "$CONFIG_FILE")

if gcloud auth application-default print-access-token &>/dev/null; then
    echo "✅ GCP Application Default Credentials are active."
else
    echo "⚠️ GCP Application Default Credentials (ADC) not found." >&2
    echo "Your Python code needs these to access GCP services (like Secret Manager)." >&2
    echo "Please run the following command to log in:" >&2
    echo "" >&2
    echo "  gcloud auth application-default login --project=$project_id" >&2
    echo "" >&2
    exit 1
fi

echo "✅ Setup complete."
echo "👉 Run 'source .venv/bin/activate' to activate the virtual environment."