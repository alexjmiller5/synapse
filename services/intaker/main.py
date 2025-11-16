import functions_framework
import json
import os
import yaml
from google.cloud import pubsub_v1

# --- Global Config ---
# TODO: remove hardcoded configuration and have it point back to conifg.yml. This will be complicated given that the deployment only really looks at main.py. Also not sure what the best practices are here in general but I want the SSOT method -- something where I can somehow load the config.yaml into this build, maybe similar to how i generate the requirements.txt at deploy time
PROJECT_ID = "synapse-477401"
TOPIC_ID = "processor-jobs"  # As defined in your main.tf TODO: move to to config.yaml which will go hand in hand with moving hardcoded project_id. Also I need to move the terraform to use variables for topic_id just like project_id
publisher = None
topic_path = None

@functions_framework.http
def intaker(request):
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