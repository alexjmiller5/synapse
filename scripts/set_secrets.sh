#!/bin/bash
set -e

CONFIG_FILE="config.yaml"

check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "💥 Error: $1 is not installed." >&2
        echo "Please install $1 and try again." >&2
        exit 1
    fi
}

get_config_value() {
    yq e ".$1" "$CONFIG_FILE"
}

check_dependencies() {
    check_command "gcloud"
    check_command "yq"
}

update_secret() {
    local secret_name="$1"
    local project_id="$2"

    echo ""
    echo "Updating secret: $secret_name"

    read -sp "Enter new value for $secret_name: " secret_value
    echo ""

    if [ -z "$secret_value" ]; then
        echo "No value entered. Skipping."
        return
    fi

    echo -n "Adding new version to $secret_name in project $project_id..."

    echo -n "$secret_value" | gcloud secrets versions add "$secret_name" \
        --project="$project_id" \
        --data-file=- &> /dev/null

    echo "Done."
}

main() {
    echo "🚀 Starting Synapse Secret Setter..."
    check_dependencies

    local project_id
    project_id=$(get_config_value "gcp_project_id")

    if [ -z "$project_id" ] || [ "$project_id" == "null" ]; then
        echo "Error: Could not read 'gcp_project_id' from $CONFIG_FILE." >&2
        exit 1
    fi

    echo "Authenticated as: $(gcloud auth list --filter="status:ACTIVE" --format="value(account)")"
    echo "Targeting Project: $project_id"
    echo "🔄 Fetching secrets from GCP..."

    local secret_names=()
    while IFS= read -r secret; do
        secret_names+=("$secret")
    done < <(gcloud secrets list --project="$project_id" --format="value(name)")

    if [ ${#secret_names[@]} -eq 0 ]; then
        echo "Error: No secrets found in project $project_id." >&2
        echo "Please create secrets in Secret Manager first." >&2
        exit 1
    fi

    echo "✅ Secrets fetched successfully"
    echo ""

    local menu_options=("Update ALL Secrets" "Quit")
    local options=("${secret_names[@]}" "${menu_options[@]}")

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