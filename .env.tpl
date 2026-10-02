# Canonical secrets manifest — 1Password secret references only, SAFE to commit.
# Local dev:       op run --env-file=.env.tpl -- <cmd>   (see justfile)
# Push to Modal:   just sync-secrets
#
# These are the APP's own provider keys. A user's Notion connection, life-data
# hub and Notion ids are workspace data (src/core/workspace.py), set with
# `just workspace set-secrets <id>` / `just workspace push <id> <dir>`.

GEMINI_API_KEY=op://Synapse/Synapse ENV/GEMINI_API_KEY
SPOTIFY_CLIENT_ID=op://Synapse/Synapse ENV/SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET=op://Synapse/Synapse ENV/SPOTIFY_CLIENT_SECRET
GOOGLE_YOUTUBE_API_KEY=op://Synapse/Synapse ENV/GOOGLE_YOUTUBE_API_KEY
TMDB_API_KEY=op://Synapse/Synapse ENV/TMDB_API_KEY
