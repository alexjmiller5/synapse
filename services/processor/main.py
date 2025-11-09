import functions_framework

@functions_framework.http
def process(request):
    """
    HTTP-triggered function.
    This is the main entry point for your processor service.
    """
    print("🧠 Synapse processor service is awake!")
    
    # Your real processor logic will go here
    # (e.g., parsing the request, calling Gemini, updating Notion)
    
    return "Hello from the processor service! I'm ready to learn something new. 🤓", 200