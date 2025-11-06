# 1. A user sends a `POST` request with unstructured text to the `ingestion-function`'s URL.
# 2. The function authenticates the request.
# 3. It makes a call to the **Gemini 2.5 Flash** model to classify the intent.
# 4. Based on the intent, it constructs a more detailed prompt (potentially including the Notion schema) and calls the **Gemini 2.5 Pro** model.
# 5. It parses the structured JSON response from the Pro model.
# 6. It uses the Notion API to create or append a page with the data provided by the AI.