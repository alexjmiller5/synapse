"""Integration tests — real Gemini AI calls, mock Notion writes.

These tests send actual user inputs through the real Gemini parsing,
classification, and extraction pipeline, then verify the final Notion
payloads are correct (right database, right properties, right values).

Notion writes are mocked — no real pages are created/modified/deleted.
Uses direct REST calls to the same model as production (bypasses conftest SDK mocks).

Test fixtures are drawn from real Synapse Executions DB entries.

Run with: just test-integration
Requires: GEMINI_API_KEY env var or 1Password access
"""

import json
import os
import subprocess
from datetime import date

import time

import pytest
import requests as _requests

# ---------------------------------------------------------------------------
# Resolve Gemini API key
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("SYNAPSE_GEMINI_API_KEY")

if GEMINI_API_KEY and GEMINI_API_KEY.startswith("fake-"):
    GEMINI_API_KEY = None  # conftest sentinel, not a real key — fall through to op

if not GEMINI_API_KEY:
    try:
        result = subprocess.run(
            ["op", "read", "op://OpenClaw/Gemini Free Tier API Key/credential"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            GEMINI_API_KEY = result.stdout.strip()
    except Exception:
        pass

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not GEMINI_API_KEY,
        reason="GEMINI_API_KEY not available — skipping integration tests",
    ),
]

# ---------------------------------------------------------------------------
# Import core modules (conftest handles mock setup)
# ---------------------------------------------------------------------------
from core.config import PROMPTS  # noqa: E402
from core.ai_engine import (  # noqa: E402
    GEMINI_MODEL,
    generate_classification_prompt,
    generate_extraction_prompt,
    get_gemini_schema,
)
from core.business_logic import apply_business_logic  # noqa: E402
from core.schemas import CATEGORY_SCHEMA_CLASSIFY  # noqa: E402

# ---------------------------------------------------------------------------
# Direct Gemini REST API (bypasses conftest SDK mocks)
# Uses the same model constant as production (core.ai_engine.GEMINI_MODEL)
# ---------------------------------------------------------------------------
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def _gemini_call(system_instruction, user_text, response_schema=None, _retries=5):
    """Call Gemini REST API directly with retry on rate limit."""
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }
    if response_schema:
        body["generationConfig"]["responseSchema"] = response_schema

    for attempt in range(_retries):
        resp = _requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=body,
            timeout=30,
        )
        if resp.status_code == 429:
            wait = min(2**attempt * 2, 30)
            print(f"  ⏳ Rate limited, waiting {wait}s (attempt {attempt + 1}/{_retries})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)

    resp.raise_for_status()  # Will raise on last 429


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _rate_limit_pause():
    """Small pause between tests to avoid Gemini free tier rate limits."""
    yield
    time.sleep(1)


ACTIVE_PROJECTS = [
    "Synapse",
    "Blueprint",
    "Social Pipe",
    "OpenClaw",
    "My Media Center",
    "Hotkey Creation & Optimization",
    "Filling & Fixing Notion Databases",
]


def classify(raw_text, context=""):
    """Run real Gemini classification."""
    proj_str = ", ".join(ACTIVE_PROJECTS)
    prompt = generate_classification_prompt(proj_str)
    user_input = f"{raw_text}\n[Context: {context}]" if context else raw_text
    return _gemini_call(prompt, user_input, CATEGORY_SCHEMA_CLASSIFY)


def extract(category, raw_text, user_context=None):
    """Run real Gemini extraction."""
    prompt = generate_extraction_prompt(category, raw_text, user_context=user_context)
    schema = get_gemini_schema(category)
    return _gemini_call(prompt, raw_text, schema)


def full_pipeline(raw_text, context=""):
    """Classify + extract + apply business logic. Returns (category, data, classified)."""
    classified = classify(raw_text, context)
    category = classified.get("category", "tasks")
    project = classified.get("related_project")

    extracted = extract(category, raw_text, user_context=context or None)

    if category == "tasks":
        extracted["Name"] = raw_text

    extracted = apply_business_logic(category, extracted, project)
    return category, extracted, classified


def parse(raw_text):
    """Run real Gemini parsing via REST API."""
    system_instruction = PROMPTS.get("parser_instruction", "")
    return _gemini_call(system_instruction, raw_text, CATEGORY_SCHEMA_CLASSIFY)


# ======================================================================
# TASK CLASSIFICATION TESTS
# ======================================================================
class TestTaskClassification:
    def test_simple_chore(self):
        cat, data, _ = full_pipeline("Update dating profile")
        assert cat == "tasks"
        assert data["Status"] == "To Do"
        assert "Chore" in data.get("Tags", [])

    def test_learning_task(self):
        cat, data, _ = full_pipeline(
            "learn the difference between salsa, bachata and merengue music",
            "medium priority task",
        )
        assert cat == "tasks"
        assert "Learning" in data.get("Tags", [])
        assert data.get("Priority") == "Medium"

    def test_shopping_task_with_future_date(self):
        cat, data, _ = full_pipeline(
            "Buy hazeover app on mac store",
            "high priority task 2.5 months from now",
        )
        assert cat == "tasks"
        assert data.get("Priority") == "High"
        assert "Shopping" in data.get("Tags", [])
        due = data.get("Due Date", "")
        assert due != date.today().isoformat()

    def test_errand_tag(self):
        cat, data, _ = full_pipeline("Pick up dry cleaning from the store", "high priority")
        assert cat == "tasks"
        assert "Errand" in data.get("Tags", [])

    def test_social_planning_tag(self):
        cat, data, _ = full_pipeline(
            "Have dinner with my parents plus Emily plus Derek",
            "high priority task 3 weeks social planning",
        )
        assert cat == "tasks"
        assert "Social Planning" in data.get("Tags", [])

    def test_career_tag(self):
        cat, data, _ = full_pipeline("Update my resume for the spring job search", "career")
        assert cat == "tasks"
        assert "Career" in data.get("Tags", [])

    def test_work_tag(self):
        cat, data, _ = full_pipeline("Submit PTO request for next Friday", "work task")
        assert cat == "tasks"
        assert "Work" in data.get("Tags", [])

    def test_long_task_with_url_not_bookmark(self):
        cat, data, _ = full_pipeline(
            "learn more about design and how i can integrate it with ai",
            "low priority task",
        )
        assert cat == "tasks"
        assert data.get("Priority") == "Low"

    def test_decision_tag(self):
        cat, data, _ = full_pipeline(
            "Decide whether to renew gym membership or switch gyms", "decision"
        )
        assert cat == "tasks"
        assert "Decision" in data.get("Tags", [])


# ======================================================================
# PROJECT TASK TESTS
# ======================================================================
class TestProjectClassification:
    def test_synapse_project_task(self):
        classified = classify(
            "make sure the description doesn't end with a period for bookmarks",
            "synapse project high priority task",
        )
        assert classified["category"] == "tasks"
        assert classified.get("related_project") == "Synapse"

    def test_hotkey_project_task(self):
        classified = classify(
            "fix the show bookmarks bar and bookmark hotkey conflict in chrome",
            "hotkey optimization project high priroity task",
        )
        assert classified["category"] == "tasks"
        project = classified.get("related_project", "")
        assert "Hotkey" in project

    def test_notion_project_task(self):
        classified = classify(
            "prune bucket list for things that belong in my trips db",
            "notion fixing and filling db high prior task",
        )
        assert classified["category"] == "tasks"
        assert classified.get("related_project"), "Should identify a related project"

    def test_note_like_thought_becomes_project_task(self):
        """Project notes are removed — a note-like thought with a project context
        is now a normal task linked to that project (no project_action)."""
        classified = classify(
            "Decided to use Redis for caching instead of memcached",
            "Synapse",
        )
        assert classified["category"] == "tasks"
        assert classified.get("related_project") == "Synapse"
        assert "project_action" not in classified

    def test_no_project_without_context(self):
        classified = classify("Update dating profile")
        proj = classified.get("related_project")
        assert not proj or proj == ""


# ======================================================================
# GROCERY TESTS
# ======================================================================
class TestGroceryClassification:
    def test_go_get_rice(self):
        cat, data, _ = full_pipeline("Go get rice")
        assert cat == "groceries"
        assert data["Name"] == "Rice"
        assert data["Status"] == "On List"

    def test_get_bread(self):
        cat, data, _ = full_pipeline("Get bread")
        assert cat == "groceries"
        assert data["Name"] == "Bread"

    def test_specific_instructions_is_task(self):
        cat, _, _ = full_pipeline("Buy a 10 pound bag of rice if the 2 pound is too small")
        assert cat == "tasks"


# ======================================================================
# MOVIE TESTS
# ======================================================================
class TestMovieClassification:
    def test_movie_with_context(self):
        cat, data, _ = full_pipeline("The fog", "movie")
        assert cat == "movies"
        assert data["Title"] == "The Fog"
        assert data["Status"] == "Not Started"

    def test_movie_priority(self):
        cat, data, _ = full_pipeline("project hail mary", "priority movies")
        assert cat == "movies"
        assert data["Status"] == "Priority"

    def test_movie_neutral_mention(self):
        cat, data, _ = full_pipeline("The traitor")
        assert cat == "movies"
        assert data["Status"] == "Not Started"

    def test_movie_watched_all_time_favorite(self):
        cat, data, _ = full_pipeline("Marty supreme", "watched all time favorite")
        assert cat == "movies"
        assert "All Time Favorite" in data.get("Tags", [])

    def test_analyze_this_is_movie(self):
        cat, _, _ = full_pipeline("Analyze This", "movie")
        assert cat == "movies"

    def test_waynes_world_is_movie(self):
        cat, data, _ = full_pipeline("Wayne's world", "priority movie")
        assert cat == "movies"
        assert data.get("Status") == "Priority"


# ======================================================================
# TV SHOW TESTS
# ======================================================================
class TestTvShowClassification:
    def test_tv_show_priority(self):
        cat, data, _ = full_pipeline("rooster", "High priority tv show")
        assert cat == "tv-shows"
        assert data.get("Status") == "Priority"

    def test_tv_show_typo_correction(self):
        cat, data, _ = full_pipeline("A Night of the Seven Kingdoms", "tv show prirority")
        assert cat == "tv-shows"
        assert "Knight" in data.get("Title", "")

    def test_all_is_fair_is_tv_show(self):
        cat, _, _ = full_pipeline("All is fair", "Kim kardashian tv show")
        assert cat == "tv-shows"


# ======================================================================
# BOOKMARK TESTS
# ======================================================================
class TestBookmarkClassification:
    def test_bare_url(self):
        cat, data, _ = full_pipeline("https://crontab.guru/")
        assert cat == "bookmarks"
        assert data.get("URL") == "https://crontab.guru/"

    def test_github_url_tagged(self):
        cat, data, _ = full_pipeline("https://github.com/runmedev/runme")
        assert cat == "bookmarks"
        assert "Github" in data.get("Tags", [])

    def test_action_with_url_is_task(self):
        cat, _, _ = full_pipeline(
            "Check out https://xmok.me as a blog for a solo entrepreneur",
            "high priority task",
        )
        assert cat == "tasks"

    def test_notion_url_is_bookmark(self):
        cat, _, _ = full_pipeline(
            "https://www.notion.so/Implement-user-accounts-31203953a8af8176b601fbe280ee297a"
        )
        assert cat == "bookmarks"


# ======================================================================
# YOUTUBE TESTS
# ======================================================================
class TestYoutubeClassification:
    def test_youtube_url(self):
        cat, data, _ = full_pipeline("https://www.youtube.com/watch?v=gODZzSOelss")
        assert cat == "youtube-videos"
        assert data.get("Status") == "Watched"

    def test_youtube_no_link_is_task(self):
        cat, _, _ = full_pipeline(
            "Add poop water video to favorite yt videos", "medium priority task"
        )
        assert cat == "tasks"


# ======================================================================
# IDEA TESTS
# ======================================================================
class TestIdeaClassification:
    def test_app_idea(self):
        cat, data, _ = full_pipeline(
            "dating profile ai curator - analyzes your photo album and finds the best pics",
            "Idea",
        )
        assert cat == "ideas"
        assert data.get("Status") == "Ideated"

    def test_good_idea(self):
        cat, data, _ = full_pipeline(
            "media notifier app - notifies you when new seasons are announced",
            "idea good idea",
        )
        assert cat == "ideas"
        assert data.get("Status") == "Good Idea"

    def test_quick_sms_idea(self):
        cat, _, _ = full_pipeline("quick sms for sms abroad", "Idea")
        assert cat == "ideas"


# ======================================================================
# FUN ACTIVITIES TESTS
# ======================================================================
class TestFunActivitiesClassification:
    def test_fun_activity_nyc(self):
        cat, data, _ = full_pipeline("get drunk at Applebees with company", "Fun activities nyc")
        assert cat == "fun-activities"
        assert data.get("Location") == "NYC"

    def test_union_square_infers_nyc(self):
        cat, data, _ = full_pipeline("Walk around Union Square", "fun")
        assert cat == "fun-activities"
        assert data.get("Location") == "NYC"


# ======================================================================
# BUCKET LIST TESTS
# ======================================================================
class TestBucketListClassification:
    def test_ice_climbing(self):
        cat, data, _ = full_pipeline("go ice climbing", "Bucket list")
        assert cat == "bucket-list"
        assert data.get("Item") == "go ice climbing"
        assert "Adventure" in data.get("Tags", [])


# ======================================================================
# PODCAST TESTS
# ======================================================================
class TestPodcastClassification:
    def test_this_american_life_watched(self):
        cat, data, _ = full_pipeline(
            "https://www.thisamericanlife.org/115/first-day/act-two-0", "watched"
        )
        assert cat == "podcasts"
        assert data.get("Status") == "Finished"

    def test_podcast_no_link_is_task(self):
        cat, _, _ = full_pipeline("Listen to that podcast about economics", "chore")
        assert cat == "tasks"


# ======================================================================
# PEOPLE TESTS
# ======================================================================
class TestPeopleClassification:
    def test_person_with_context(self):
        cat, data, _ = full_pipeline("Arun Vantage senior associate")
        assert cat == "people"
        assert "Arun" in data.get("Name", "")

    def test_action_with_person_is_task(self):
        cat, _, _ = full_pipeline("Call Will about the dinner plans")
        assert cat == "tasks"


# ======================================================================
# PLACES TESTS
# ======================================================================
class TestPlacesClassification:
    def test_google_maps_url(self):
        cat, _, _ = full_pipeline("https://maps.app.goo.gl/eVBrk4NxWUzTeAYR9")
        assert cat == "places"

    def test_place_without_url_is_not_places(self):
        cat, _, _ = full_pipeline("Go to Central Park")
        assert cat != "places"


# ======================================================================
# PARSER TESTS
# ======================================================================
class TestParser:
    def _parse(self, text):
        """Parse using real Gemini via REST API."""
        system_instruction = PROMPTS.get("parser_instruction", "")
        from core.schemas import PARSER_SCHEMA

        return _gemini_call(system_instruction, text, PARSER_SCHEMA)

    def test_single_item(self):
        result = self._parse("Buy eggs")
        assert len(result) >= 1
        assert result[0]["core_text"].strip() != ""

    def test_at_delimiter_splits(self):
        result = self._parse("Buy eggs @ Call John @ Update resume")
        assert len(result) == 3

    def test_dollar_context_separator(self):
        result = self._parse("Cancel Uber One $ Jan 1 high priority")
        assert len(result) == 1
        item = result[0]
        assert item["core_text"].strip() != ""
        assert item["context_notes"].strip() != ""

    def test_complex_batch(self):
        result = self._parse(
            "Buy eggs $ groceries @ Update resume $ Career @ https://youtube.com/watch?v=abc"
        )
        assert len(result) == 3

    def test_dollar_in_price_not_split(self):
        result = self._parse("Buy the $50 headphones")
        assert len(result) == 1


# ======================================================================
# REGRESSION TESTS — previously failed extractions
# ======================================================================
class TestFailedExtractionRegression:
    def test_swapped_context_core_text(self):
        cat, data, _ = full_pipeline(
            "standardize the name of all relations so that the name is just the name of the related db",
            "notion fixing and filling db high priority task",
        )
        assert cat == "tasks"
        assert data.get("Priority") == "High"
        assert "standardize" in data.get("Name", "").lower()

    def test_chinatown_movie_not_task(self):
        cat, _, _ = full_pipeline("Chinatown", "movie priority")
        assert cat == "movies"

    def test_gift_idea_is_task(self):
        cat, data, _ = full_pipeline(
            "a good gift from my parents would be ticket to Miami ultra",
            "September task",
        )
        assert cat == "tasks"

    def test_plan_trip_is_task(self):
        cat, _, _ = full_pipeline("Plan a trip to Boston")
        assert cat == "tasks"

    def test_add_to_trips_db_is_task(self):
        cat, _, _ = full_pipeline("add Asheville to trips db under to plan", "Medium priority")
        assert cat == "tasks"

    def test_instagram_url_is_bookmark(self):
        cat, _, _ = full_pipeline("https://www.instagram.com/thirstygallerina/?hl=en")
        assert cat == "bookmarks"

    def test_add_ghostbusters_is_task(self):
        cat, _, _ = full_pipeline("Add ghostbusters movies to movies db to watch")
        assert cat == "tasks"

    def test_google_maps_url_is_places(self):
        cat, _, _ = full_pipeline("https://maps.app.goo.gl/KkxRGMgTDstVggj8A")
        assert cat == "places"

    def test_swapped_context_core_text_regression(self):
        """Regression: AI swapped core_text and context for parenthetical input.
        Input: 'notion fixing and filling db high priority task (Context: standardize
        the name of all relations so that the name is just the name of the
        related db, it shouldn't be anything else)'
        Expected: The specific instruction is the real task, not the generic prefix.
        """
        cat, data, _ = full_pipeline(
            "notion fixing and filling db high priority task (Context: standardize "
            "the name of all relations so that the name is just the name of the "
            "related db, it shouldn't be anything else)"
        )
        assert cat == "tasks"
        name = data.get("Name", "").lower()
        # The task name should reference the specific instruction, not the generic prefix
        assert "standardize" in name or "relation" in name, (
            f"Task name should reference the specific instruction about standardizing "
            f"relation names, got: {data.get('Name')}"
        )

    def test_invalid_date_produces_valid_iso(self):
        cat, data, _ = full_pipeline(
            "Have dinner with my parents plus Emily plus Derek",
            "high priority task 3 weeks",
        )
        due = data.get("Due Date", "")
        if due:
            from datetime import datetime

            try:
                datetime.strptime(due, "%Y-%m-%d")
            except ValueError:
                pytest.fail(f"Invalid date produced: {due}")
