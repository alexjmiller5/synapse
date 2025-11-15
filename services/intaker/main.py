import functions_framework
import json
import os
import yaml
from google.cloud import pubsub_v1

# --- Load Config ---
def load_config():
    """Loads gcp_project_id from root config.yml."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(os.path.dirname(script_dir))
        config_path = os.path.join(root_dir, 'config.yml')
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config.get("gcp_project_id", "synapse-477401")
    except Exception as e:
        print(f"Warning: Could not load config.yml: {e}. Using default project ID.")
        return os.environ.get("GCP_PROJECT", "synapse-477401")

# --- Global Config ---
PROJECT_ID = load_config()
TOPIC_ID = "processor-jobs"  # As defined in your main.tf
publisher = None
topic_path = None

@functions_framework.http
def intake(request):
    """
    HTTP-triggered function to receive raw_text and publish it
    to a Pub/Sub topic for asynchronous processing.
    """
    global publisher, topic_path
    
    # Initialize client on first request (cold start)
    if not publisher:
        try:
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
            print(f"Publisher initialized for topic: {topic_path}")
        except Exception as e:
            print(f"FATAL: Could not initialize Pub/Sub client: {e}")
            return "Internal server error: Pub/Sub client", 500

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
        message_id = future.result() # Wait for publish to complete
        
        print(f"Published message {message_id} to {topic_path}")
        
        # Return 202 Accepted to signal the client
        # that the request is accepted but not yet complete.
        return "Job accepted", 202

    except Exception as e:
        print(f"Error publishing to Pub/Sub: {e}")
        return "Internal server error", 500