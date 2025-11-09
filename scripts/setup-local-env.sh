#!/bin/bash
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )

CONFIG_FILE="$PROJECT_ROOT/config.yml"
TF_VARS_FILE="$PROJECT_ROOT/infrastructure/terraform.tfvars"
VENV_DIR="$PROJECT_ROOT/.venv"
SERVICES_DIR="$PROJECT_ROOT/services"

check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "💥 Error: $1 is not installed." >&2
        echo "Please install $1 and try again." >&2
        exit 1
    fi
}

get_config() {
    yq e ".$1" "$CONFIG_FILE"
}

check_dependencies() {
    check_command "uv"
    check_command "gcloud"
    check_command "terraform"
    check_command "yq"
}

setup_terraform() {
    if [ -f "$TF_VARS_FILE" ]; then
        echo "✅ Terraform variables file already exists at $TF_VARS_FILE."
    else
        echo "🚀 Creating local $TF_VARS_FILE from config..."
        
        if [ ! -d "$PROJECT_ROOT/infrastructure" ]; then
            echo "Error: 'infrastructure' directory not found at $PROJECT_ROOT/infrastructure." >&2
            exit 1
        fi

        yq e 'to_entries | .[] | select(.value | (tag != "!!map" and tag != "!!seq")) | .key + " = \"" + .value + "\""' "$CONFIG_FILE" > "$TF_VARS_FILE"
        
        echo "✅ Created $TF_VARS_FILE."
        echo "IMPORTANT: Make sure '$TF_VARS_FILE' is in your .gitignore file!"
    fi
}

setup_python() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "🚀 Creating Python virtual environment with uv at $VENV_DIR..."
        # Run uv venv from the project root
        (cd "$PROJECT_ROOT" && uv venv)
    fi
    
    echo "📦 Installing service dependencies..."

    local venv_python="$VENV_DIR/bin/python"

    echo "  -> Discovering and installing services in '$SERVICES_DIR'..."
    for service_dir in "$SERVICES_DIR"/*/; do
        if [ -d "$service_dir" ] && [ -f "$service_dir/pyproject.toml" ]; then
            local service_name
            service_name=$(basename "$service_dir")
            echo "    -> Installing '$service_name'"
            uv pip install -e "$service_dir" --python "$venv_python" --quiet
        fi
    done
    echo "✅ Python environment is ready."
}

check_gcloud_auth() {
    local project_id
    project_id=$(get_config "gcp_project_id")
    
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
}

main() {
    check_dependencies >&2
    setup_terraform >&2
    setup_python >&2
    check_gcloud_auth >&2
    
    echo "source $VENV_DIR/bin/activate"

    echo "🎉 Local environment is configured!" >&2
    echo "Python virtual env is active and GCP auth is verified." >&2
    echo "Your application code will now fetch secrets directly." >&2
    echo "You can now run your services using the 'functions-framework' command." >&2
}

# Run the main function
main