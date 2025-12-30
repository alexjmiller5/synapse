import os
import yaml

CONFIG = {}
DATABASES = {}
PROMPTS = {}

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "databases.yaml"), "r") as f:
        DATABASES = yaml.safe_load(f)
except Exception as e:
    print(f"❌ Critical Config Error: {e}")

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "prompts.yaml"), "r") as f:
        PROMPTS = yaml.safe_load(f)
except Exception:
    pass

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