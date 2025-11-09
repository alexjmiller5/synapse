import functions_framework
import base64
import json

@functions_framework.http
def report(request):
    """
    Event-triggered function.
    This service is triggered by Eventarc when a message hits your Pub/Sub topic.
    """
    print("📊 Synapse reporter service is running!")

    # The Pub/Sub message is sent by Eventarc in the HTTP request body
    try:
        data = request.get_json()
        
        # The actual data is base64-encoded inside the 'message' object
        message_data_str = base64.b64decode(data['message']['data']).decode('utf-8')
        message_json = json.loads(message_data_str)
        
        print(f"Received Pub/Sub message: {message_json}")
        
        # Your real reporter logic will go here
        # (e.g., query databases, send email)

    except Exception as e:
        print(f"Error processing message: {e}")
        # Return a 500 so Eventarc/Pub/Sub can retry if configured
        return "An error occurred", 500

    return "Report complete! I'll go back to sleep now. 😴", 200