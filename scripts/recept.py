#!/usr/bin/env python3
import sys
import base64
import requests

def send_event(input_text):
    url = "http://localhost:8080"

    # Create the encoded payload
    data_bytes = input_text.encode("utf-8")
    encoded_data = base64.b64encode(data_bytes).decode("utf-8")

    payload = {"message": {"data": encoded_data, "attributes": {}}}

    headers = {
        "Content-Type": "application/json",
        "Ce-Id": "123456789",
        "Ce-Specversion": "1.0",
        "Ce-Type": "google.cloud.pubsub.topic.v1.messagePublished",
        "Ce-Source": "//pubsub.googleapis.com/projects/synapse/topics/local",
    }

    print(f"🚀 Sending: '{input_text}'...")

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"✅ Success: {response.status_code}")
        print(response.text)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 send_event.py <text>")
        sys.exit(1)

    send_event(" ".join(sys.argv[1:]))
