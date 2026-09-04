# Canonical secrets manifest — 1Password secret references only, SAFE to commit.
# Local dev:       op run --env-file=.env.tpl -- <cmd>   (see justfile)
# Push to Modal:   just sync-secrets

GEMINI_API_KEY=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/GEMINI_API_KEY
NOTION_INTEGRATION_TOKEN=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/NOTION_INTEGRATION_TOKEN
SPOTIFY_CLIENT_ID=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/SPOTIFY_CLIENT_SECRET
GOOGLE_PLACES_API_KEY=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/GOOGLE_PLACES_API_KEY
GOOGLE_YOUTUBE_API_KEY=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/GOOGLE_YOUTUBE_API_KEY

# Notion DB ids are committed config (see src/core/databases.yaml), not secrets.
# A NOTION_<X>_DB_ID env var still overrides the config value if you set one.
TMDB_API_KEY=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/TMDB_API_KEY

# Personal config (not a secret): comma-separated Location allowlist for
# fun-activities — the committed databases.yaml carries generic example cities.
NOTION_FUN_ACTIVITIES_LOCATIONS=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/NOTION_FUN_ACTIVITIES_LOCATIONS

# Personal config (not a secret): comma-separated Tasks Tags options naming
# personal places — joins the Tags allowlist; tasks tagged with one get no
# fallback Due Date (done whenever the user is next at that place).
NOTION_TASKS_PLACE_TAGS=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/NOTION_TASKS_PLACE_TAGS

# life-data hub - movies and tv-shows are life-data tables, not Notion DBs.
# The token is the hub's `synapse` client token (tables:write scope).
LIFE_HUB_URL=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/LIFE_HUB_URL
LIFE_HUB_TOKEN=op://skkfhuuqegdpyzuobf6h6dyoly/4klx766p6v7noej5226raa6pby/LIFE_HUB_TOKEN
