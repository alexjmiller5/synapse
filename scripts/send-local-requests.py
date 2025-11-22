import argparse
import base64
import json
import requests
import sys
import os

# ANSI Colors for terminal output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
NC = '\033[0m'

def process_request(content, url):
    # Skip empty/whitespace only strings
    if not content or not content.strip():
        return

    # Show preview (truncated to 50 chars)
    preview = content.strip().replace('\n', ' ')[:50]
    print(f"Processing: {preview}...", end="", flush=True)

    try:
        # 1. Base64 Encode
        encoded_data = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        # 2. Construct Payload
        payload = {
            "message": {
                "data": encoded_data,
                "attributes": {}
            }
        }

        # 3. Prepare Headers (CloudEvent Simulation)
        headers = {
            "Content-Type": "application/json",
            "Ce-Id": "123456789",
            "Ce-Specversion": "1.0",
            "Ce-Type": "google.cloud.pubsub.topic.v1.messagePublished",
            "Ce-Source": "//pubsub.googleapis.com/projects/synapse/topics/local",
        }

        # 4. Send Request
        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            print(f" {GREEN}✅ Success{NC}")
        else:
            print(f" {RED}❌ Failed ({response.status_code}){NC}")

    except Exception as e:
        print(f" {RED}❌ Error: {e}{NC}")

def main():
    parser = argparse.ArgumentParser(description="Batch process requests to local Synapse processor.")
    parser.add_argument("filename", help="Input file containing requests")
    parser.add_argument("--endpoint", "-e", default="http://localhost:8080", help="Target endpoint URL (default: http://localhost:8080)")
    parser.add_argument("--separator", "-s", default="\n", help="Separator between requests (default: newline)")
    
    args = parser.parse_args()

    if not os.path.exists(args.filename):
        print(f"{RED}Error: File {args.filename} not found.{NC}")
        sys.exit(1)

    print(f"⚡️ Starting batch process from {args.filename} to {args.endpoint}...")

    try:
        with open(args.filename, 'r', encoding='utf-8') as f:
            file_content = f.read()

        # Logic to split by separator
        if args.separator == "\\n" or args.separator == "\n":
            # Line by line mode
            requests_list = file_content.splitlines()
        else:
            # Custom separator mode
            requests_list = file_content.split(args.separator)

        for content in requests_list:
            process_request(content, args.endpoint)

    except KeyboardInterrupt:
        print("\n🛑 Process cancelled.")
        sys.exit(0)

    print("🎉 Batch complete.")

if __name__ == "__main__":
    main()