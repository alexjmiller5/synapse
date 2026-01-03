import os
import yaml

CONFIG = {}
DATABASES = {}
PROMPTS = {}

script_dir = os.path.dirname(os.path.abspath(__file__))

def load_yaml(filename, paths):
    for path in paths:
        full_path = os.path.abspath(path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r") as f:
                    return yaml.safe_load(f) or {} # Return empty dict if file is empty
            except Exception as e:
                print(f"⚠️ Error loading {filename} at {full_path}: {e}")
    return {}

DATABASES = load_yaml("databases.yaml", [os.path.join(script_dir, "databases.yaml")])
PROMPTS = load_yaml("prompts.yaml", [os.path.join(script_dir, "prompts.yaml")])

config_paths = [
    os.path.join(script_dir, "..", "..", "config.yaml"),
    os.path.join(script_dir, "config.yaml")
]
CONFIG = load_yaml("config.yaml", config_paths)

if not CONFIG:
    print("❌ Critical: config.yaml not found.")