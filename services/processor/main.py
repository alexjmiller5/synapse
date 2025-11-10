import functions_framework
import json

@functions_framework.http
def process(request):
    """
    HTTP-triggered function.
    This is the main entry point for your processor service.
    """
    print("🧠 Synapse processor service is awake!")
    
    # Try to parse the request body as JSON
    try:
        request_json = request.get_json(silent=True)
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return "Error: Invalid JSON payload.", 400

    # Check if JSON is valid and 'raw_text' field exists
    if request_json and 'raw_text' in request_json:
        raw_text = request_json['raw_text']
        print(f"Received raw_text: {raw_text}")
        
        # Your real processor logic will go here
        # (e.g., parsing the request, calling Gemini, updating Notion)
        
        return f"Successfully processed text: {raw_text}", 200
    else:
        # Handle missing field or no JSON
        error_msg = "Error: Request must be JSON and include a 'raw_text' field."
        print(error_msg)
        return error_msg, 400