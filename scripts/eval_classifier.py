#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "google-genai~=1.49.0",
#     "PyYAML~=6.0.3",
#     "tenacity>=9.1.2",
#     "notion-client~=2.7.0",
#     "spotipy~=2.25.1",
#     "googlemaps>=4.10.0",
#     "google-api-python-client>=2.187.0",
# ]
# ///
"""Eval the classification prompt against scripts/eval_cases.yaml with real Gemini calls.

Measures the PROMPT alone (the deterministic task-context pre-check in
pipeline.py is unit-tested separately). Each case runs --repeats times and
passes on majority vote. Exits nonzero if misclassified-set accuracy < 90%
or control-set accuracy < 95%.

Usage:
    op run --env-file=.env.tpl -- uv run scripts/eval_classifier.py
    ... --core-dir /path/to/old/src/core   # eval an alternate prompts/databases yaml pair
"""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY not set — run via `just eval-classifier` (op injects it).")

from google.genai import types  # noqa: E402

import core.ai_engine as ai_engine  # noqa: E402
from core.schemas import CATEGORY_SCHEMA_CLASSIFY  # noqa: E402


def classify_once(prompt: str, classify_input: str) -> str:
    response = ai_engine.generate_with_retry(
        model=ai_engine.GEMINI_MODEL,
        contents=[types.Content(parts=[types.Part(text=classify_input)], role="user")],
        config=types.GenerateContentConfig(
            system_instruction=prompt,
            response_mime_type="application/json",
            response_json_schema=CATEGORY_SCHEMA_CLASSIFY,
        ),
    )
    return json.loads(response.text).get("category", "tasks")


def eval_case(prompt: str, case: dict, repeats: int) -> tuple[dict, str, bool]:
    text, ctx = case["text"], case.get("context") or ""
    classify_input = f"{text}\n[Context: {ctx}]" if ctx else text
    votes = Counter(classify_once(prompt, classify_input) for _ in range(repeats))
    predicted, n = votes.most_common(1)[0]
    passed = predicted == case["expected"] and n * 2 > repeats  # majority must agree
    return case, predicted, passed


def run_set(prompt: str, name: str, cases: list[dict], repeats: int) -> float:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: eval_case(prompt, c, repeats), cases))

    confusion: dict[str, Counter] = {}
    passed = 0
    print(f"\n=== {name} ({len(cases)} cases, {repeats} repeats each) ===")
    for case, predicted, ok in results:
        passed += ok
        confusion.setdefault(case["expected"], Counter())[predicted] += 1
        if not ok:
            print(
                f"  FAIL: {case['text'][:60]!r} (ctx: {case.get('context', '')!r})"
                f" expected={case['expected']} got={predicted}"
            )

    print("  Confusion (expected -> predicted counts):")
    for expected, preds in sorted(confusion.items()):
        print(f"    {expected}: {dict(preds)}")
    accuracy = passed / len(cases)
    print(f"  Accuracy: {passed}/{len(cases)} = {accuracy:.1%}")
    return accuracy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-dir", help="Alternate dir containing prompts.yaml + databases.yaml")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    if args.core_dir:
        core_dir = Path(args.core_dir)
        # generate_classification_prompt reads its module globals — patch them.
        ai_engine.PROMPTS = yaml.safe_load((core_dir / "prompts.yaml").read_text())
        ai_engine.DATABASES = yaml.safe_load((core_dir / "databases.yaml").read_text())
        print(f"Using prompts/databases from: {core_dir}")

    prompt = ai_engine.generate_classification_prompt("None")
    cases = yaml.safe_load((ROOT / "scripts" / "eval_cases.yaml").read_text())

    misclass_acc = run_set(
        prompt, "Misclassified set (expected tasks)", cases["misclassified"], args.repeats
    )
    control_acc = run_set(prompt, "Control set", cases["controls"], args.repeats)

    ok = misclass_acc >= 0.90 and control_acc >= 0.95
    print(
        f"\n{'PASS' if ok else 'FAIL'}: misclass={misclass_acc:.1%} (need >=90%),"
        f" control={control_acc:.1%} (need >=95%)"
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
