import os
import yaml

CONFIG = {}
PROMPTS = {}

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "synapse_config.yaml"), "r") as f:
        CONFIG = yaml.safe_load(f)
except Exception as e:
    print(f"❌ Critical Config Error: {e}")

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "prompts.yaml"), "r") as f:
        PROMPTS = yaml.safe_load(f)
except Exception:
    pass
