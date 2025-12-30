import functions_framework
import json
import os
import yaml
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

        print(f"Received raw_text: {raw_text}")

    except Exception as e:
        error_msg = f"Error parsing request JSON: {e}"
        print(error_msg)
        return error_msg, 400

    try:
        # Publish the raw_text to Pub/Sub
        # Data must be bytestring
        data = raw_text.encode("utf-8")
        future = publisher.publish(topic_path, data)
        message_id = future.result()  # Wait for publish to complete

        print(f"Published message {message_id} to {topic_path}")

        # Return 202 Accepted to signal the client
        # that the request is accepted but not yet complete.
        return "Job accepted", 202

    except Exception as e:
        print(f"Error publishing to Pub/Sub: {e}")
        return "Internal server error", 500
