from google.cloud import secretmanager
from config import DATABASES, CONFIG

PROJECT_ID = CONFIG.get("gcp_project_id")
SECRETS = {}
sm_client = None


def get_secret(secret_id, version="latest"):
    global sm_client
    if sm_client is None:
        try:
            sm_client = secretmanager.SecretManagerServiceClient()
        except Exception:
            return None
    if secret_id in SECRETS:
        return SECRETS[secret_id]
    try:
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version}"
        response = sm_client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        SECRETS[secret_id] = payload
        return payload
    except Exception as e:
        print(f"Error fetching {secret_id}: {e}")
        return None


def get_db_id(category):
    """Fetches the Secret ID for a category based on convention."""
    return get_secret(f"notion-{category}-db-id")


# Pre-fetch Database IDs
DATABASE_IDS = {}
for cat in list(DATABASES.get("databases", {}).keys()) + [
    "logs",
    "youtube-channels",
    "trips",
]:
    val = get_secret(f"notion-{cat}-db-id")
    if val:
        DATABASE_IDS[cat] = val
