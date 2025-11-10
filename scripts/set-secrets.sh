#!/bin/bash
set -e

#
# Synapse Project - Interactive Secret Setter
#
# This script interactively updates the values of your GCP secrets.
# It reads the project ID from config.yml and prompts you
# to update the specific secrets defined in the 'SECRET_NAMES' array.
#
# USAGE:
# 1. Make it executable: chmod +x set_secrets.sh
# 2. Run it: ./set_secrets.sh
# Run me from the root directory of the project.

CONFIG_FILE="config.yml"

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

# --- Main Functions ---

# 1. Check all required dependencies
check_dependencies() {
    check_command "gcloud"
    check_command "yq"
}

# 2. Function to update a single secret
# $1: Secret Name
# $2: Project ID
update_secret() {
    local secret_name="$1"
    local project_id="$2"
    
    echo ""
    echo "Updating secret: $secret_name"
    
    # Prompt for the secret value securely (no echo)
    read -sp "Enter new value for $secret_name: " secret_value
    echo "" # Newline after prompt
    
    if [ -z "$secret_value" ]; then
        echo "No value entered. Skipping."
        return
    fi
    
    echo -n "Adding new version to $secret_name in project $project_id..."
    
    # Add a new version of the secret
    # We pipe the value to --data-file=- to handle special characters
    echo -n "$secret_value" | gcloud secrets versions add "$secret_name" \
        --project="$project_id" \
        --data-file=- &> /dev/null
    
    echo "Done."
}

# --- Main Execution ---

main() {
    echo "🚀 Starting Synapse Secret Setter..."
    check_dependencies
    
    local project_id
    project_id=$(get_config "gcp_project_id")
    
    if [ -z "$project_id" ] || [ "$project_id" == "null" ]; then
        echo "Error: Could not read 'gcp_project_id' from $CONFIG_FILE." >&2
        exit 1
    fi
    
    echo "Authenticated as: $(gcloud auth list --filter="status:ACTIVE" --format="value(account)")"
    echo "Targeting Project: $project_id"
    echo ""
    
    # Secrets to manage (from your README)
    local secret_names=(
        "gemini-api-key"
        "notion-api-token"
        "notion-tasks-db-id"
        "notion-quick-notes-last-block-id"
        "gmail-app-password"
    )
    
    # Add menu options
    local menu_options=("Update ALL Secrets" "Quit")
    local options=("${secret_names[@]}" "${menu_options[@]}")

    # Main menu loop
    PS3="Select a secret to update (or 'Quit'): "
    while true; do
        select opt in "${options[@]}"; do
            case "$opt" in
                "Quit")
                    echo "👋 Exiting."
                    exit 0
                    ;;
                "Update ALL Secrets")
                    echo "🔄 Updating all secrets..."
                    for secret in "${secret_names[@]}"; do
                        update_secret "$secret" "$project_id"
                    done
                    echo "✅ All secrets updated."
                    echo ""
                    break # Break from select, show menu again
                    ;;
                "")
                    echo "Invalid option. Please try again."
                    break # Break from select, show menu again
                    ;;
                *)
                    # This handles selection of any individual secret
                    update_secret "$opt" "$project_id"
                    echo ""
                    break # Break from select, show menu again
                    ;;
            esac
        done
    done
}

main