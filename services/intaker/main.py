import functions_framework
import hashlib
import json
import os
import time
import yaml
from collections import OrderedDict
from google.cloud import pubsub_v1

# --- Global Config ---

CONFIG = {}

## try two directories up for config.yaml in addition to current directory
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(
        os.path.join(script_dir, "..", "..", "config.yaml"), "r"
    ) as f:
        CONFIG = yaml.safe_load(f)
except Exception as e:
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(script_dir, "config.yaml"), "r") as f:
            CONFIG = yaml.safe_load(f)
    except Exception as e2:
        print(f"❌ Critical Config Error: {e} | {e2}")

PROJECT_ID = CONFIG.get("gcp_project_id")
TOPIC_ID = CONFIG.get("processor_topic_name")

# --- Dedup Cache (resets on cold start by design) ---
DEDUP_CACHE = OrderedDict()
DEDUP_WINDOW_SECONDS = 300  # 5 minutes
DEDUP_MAX_SIZE = 100


def _make_text_key(raw_text):
    """Create a normalized hash key from raw text for dedup."""
    normalized = raw_text.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _evict_expired(cache, window_seconds):
    """Remove entries older than window_seconds from the front of the cache."""
    now = time.time()
    while cache:
        oldest_key, oldest_time = next(iter(cache.items()))
        if now - oldest_time > window_seconds:
            cache.pop(oldest_key)
        else:
            break


def _is_duplicate(thought_id, raw_text):
    """
    Check if this request is a duplicate.
    Primary: thought_id (if present) — catches iOS/macOS retries of the same thought.
    Fallback: text hash — catches non-Receptor clients or missing thought_id.
    Returns True if duplicate.
    """
    now = time.time()
    _evict_expired(DEDUP_CACHE, DEDUP_WINDOW_SECONDS)

    # Primary: check thought_id if provided
    if thought_id:
        key = f"tid:{thought_id}"
        if key in DEDUP_CACHE:
            print(f"⏭️ Duplicate thought_id: {thought_id}")
            return True
        DEDUP_CACHE[key] = now
    else:
        # Fallback: check text hash
        key = f"txt:{_make_text_key(raw_text)}"
        if key in DEDUP_CACHE:
            print(f"⏭️ Duplicate text detected: {repr(raw_text[:50])}")
            return True
        DEDUP_CACHE[key] = now

    # Evict oldest if over max size
    while len(DEDUP_CACHE) > DEDUP_MAX_SIZE:
        DEDUP_CACHE.popitem(last=False)

    return False


# Initialize client on first request (cold start)
try:
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
    print(f"✅ Global Publisher initialized for: {topic_path}")
except Exception as e:
    print(f"⚠️ Global Client init failed: {e}")
    publisher = None
    topic_path = None


@functions_framework.http
def intaker(request):
    """
    HTTP-triggered function to receive raw_text and publish it
    to a Pub/Sub topic for asynchronous processing.
    """
    if not publisher or not topic_path:
        print("Error: Publisher client is not ready.")
        return "Internal Server Error", 500

    try:
        request_json = request.get_json(silent=True)
        if not request_json or "raw_text" not in request_json:
            error_msg = "Error: Request must be JSON and include a 'raw_text' field."
            print(error_msg)
            return error_msg, 400

        raw_text = request_json["raw_text"]
        if not raw_text:
            error_msg = "Error: 'raw_text' field cannot be empty."
            print(error_msg)
            return error_msg, 400

        thought_id = request_json.get("thought_id")

        print(f"Received raw_text: {raw_text}" + (f" thought_id: {thought_id}" if thought_id else ""))

        if _is_duplicate(thought_id, raw_text):
            return "Duplicate suppressed", 200

    except Exception as e:
        error_msg = f"Error parsing request JSON: {e}"
        print(error_msg)
        return error_msg, 400

    try:
        # Publish the raw_text to Pub/Sub
        # Data must be bytestring
        data = raw_text.encode("utf-8")
        # Pass thought_id as attribute for downstream traceability
        attrs = {}
        if thought_id:
            attrs["thought_id"] = thought_id
        future = publisher.publish(topic_path, data, **attrs)
        message_id = future.result()  # Wait for publish to complete

        print(f"Published message {message_id} to {topic_path}")

        # Return 202 Accepted to signal the client
        # that the request is accepted but not yet complete.
        return "Job accepted", 202

    except Exception as e:
        print(f"Error publishing to Pub/Sub: {e}")
        return "Internal server error", 500
