# Canonical secrets manifest — 1Password secret references only, SAFE to commit.
# Local dev:       op run --env-file=.env.tpl -- <cmd>   (see justfile)
# Push to Modal:   just sync-secrets
#
# These are the APP's own provider keys. A user's Soma hub credentials and
# config overlay are workspace data (src/core/workspace.py), set with
# `just workspace set-secrets <id>` / `just workspace push <id> <dir>`.

SPOTIFY_CLIENT_ID=op://Synapse/Synapse ENV/SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET=op://Synapse/Synapse ENV/SPOTIFY_CLIENT_SECRET
GOOGLE_YOUTUBE_API_KEY=op://Synapse/Synapse ENV/GOOGLE_YOUTUBE_API_KEY
TMDB_API_KEY=op://Synapse/Synapse ENV/TMDB_API_KEY

AI_PROVIDER=op://Synapse/Synapse ENV/AI_PROVIDER
CLOUDFLARE_API_TOKEN=op://Synapse/Synapse ENV/CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID=op://Synapse/Synapse ENV/CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_MODEL=op://Synapse/Synapse ENV/CLOUDFLARE_MODEL
