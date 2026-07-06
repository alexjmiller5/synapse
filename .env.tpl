# Canonical secrets manifest — 1Password secret references only, SAFE to commit.
# Local dev:       op run --env-file=.env.tpl -- <cmd>   (see justfile)
# Push to Modal:   just sync-secrets

GEMINI_API_KEY=op://Synapse/Synapse Env/GEMINI_API_KEY
NOTION_INTEGRATION_TOKEN=op://Synapse/Synapse Env/NOTION_INTEGRATION_TOKEN
SPOTIFY_CLIENT_ID=op://Synapse/Synapse Env/SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET=op://Synapse/Synapse Env/SPOTIFY_CLIENT_SECRET
GOOGLE_PLACES_API_KEY=op://Synapse/Synapse Env/GOOGLE_PLACES_API_KEY
GOOGLE_YOUTUBE_API_KEY=op://Synapse/Synapse Env/GOOGLE_YOUTUBE_API_KEY

# Notion DB ids are committed config (see src/core/databases.yaml), not secrets.
# A NOTION_<X>_DB_ID env var still overrides the config value if you set one.
