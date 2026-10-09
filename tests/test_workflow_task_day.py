"""Extraction observes the selected task calendar, including durable retries."""

import copy
from datetime import date, datetime, timezone

import pytest

from core import ai_engine, workflow, workspace


@pytest.fixture
def calendar_workspace(monkeypatch):
    config = copy.deepcopy(workspace.current())
    config.config["workflow"] = {
        "tasks": {
            "table": "work_items",
            "columns": {"Name": "title"},
            "calendar": {"timeZone": "America/Chicago", "dayStartMinutes": 180},
        }
    }
    config.config["categories"]["tasks"]["properties"]["Name"]["instruction"] = (
        "TASK_DAY={current_date}"
    )
    monkeypatch.setattr(ai_engine, "today_eastern", lambda: date(2040, 1, 1))
    with workspace.use(config):
        yield config


@pytest.mark.parametrize(
    "instant,expected",
    [
        ("2030-01-02T08:59:00+00:00", "2030-01-01"),
        ("2030-01-02T09:00:00+00:00", "2030-01-02"),
        ("2030-03-10T07:59:00+00:00", "2030-03-09"),
        ("2030-03-10T08:00:00+00:00", "2030-03-10"),
        ("2030-11-03T07:30:00+00:00", "2030-11-02"),
        ("2030-11-03T09:00:00+00:00", "2030-11-03"),
    ],
)
def test_actual_extraction_prompt_uses_configured_day(
    calendar_workspace, monkeypatch, instant, expected
):
    monkeypatch.setattr(workflow, "utc_now", lambda: datetime.fromisoformat(instant), raising=False)
    assert f"TASK_DAY={expected}" in ai_engine.generate_extraction_prompt("tasks", "A task")


def test_calendar_date_is_frozen_before_first_extraction_and_across_retry(
    calendar_workspace, monkeypatch
):
    payload = workflow.accepted_capture(
        {"raw_text": "A task", "capture_id": "50d4ad4d-3499-41d5-a5cb-c3dbbd316ea6"},
        calendar_workspace.id,
    )
    store = {}
    clock = [datetime(2030, 1, 2, 8, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(workflow, "utc_now", lambda: clock[0], raising=False)
    with workflow.capture_scope(store, payload):
        clock[0] = datetime(2030, 1, 2, 9, 1, tzinfo=timezone.utc)
        first = ai_engine.generate_extraction_prompt("tasks", "A task")
    calendar_workspace.config["workflow"]["tasks"]["calendar"]["dayStartMinutes"] = 0
    with workflow.capture_scope(store, payload):
        assert ai_engine.generate_extraction_prompt("tasks", "A task") == first
    assert "TASK_DAY=2030-01-01" in first


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {},
        {"timeZone": "invalid"},
        {"timeZone": "UTC", "dayStartMinutes": True},
        {"timeZone": "UTC", "dayStartMinutes": -1},
        {"timeZone": "UTC", "dayStartMinutes": 1440},
    ],
)
def test_malformed_selected_calendar_fails_before_extraction(calendar_workspace, policy):
    calendar_workspace.config["workflow"]["tasks"]["calendar"] = policy
    with pytest.raises(ValueError, match="calendar"):
        ai_engine.generate_extraction_prompt("tasks", "A task")


def test_unconfigured_calendar_keeps_legacy_behavior(calendar_workspace):
    del calendar_workspace.config["workflow"]["tasks"]["calendar"]
    assert "TASK_DAY=2040-01-01" in ai_engine.generate_extraction_prompt("tasks", "A task")


def test_other_categories_do_not_inherit_task_day(calendar_workspace):
    calendar_workspace.config["categories"]["bucket-list"]["properties"]["Item"]["instruction"] = (
        "OTHER_DAY={current_date}"
    )
    assert "OTHER_DAY=2040-01-01" in ai_engine.generate_extraction_prompt("bucket-list", "A goal")


@pytest.mark.parametrize("name", ["create_cleanup_task", "create_high_priority_task"])
def test_generated_followup_tasks_share_calendar(calendar_workspace, monkeypatch, name):
    monkeypatch.setattr(
        workflow, "utc_now", lambda: datetime(2030, 1, 2, 8, 59, tzinfo=timezone.utc)
    )
    sent = []
    monkeypatch.setattr(workflow, "create_task", lambda values, **kw: sent.append(values))
    getattr(workflow, name)("A followup")
    assert sent[0]["Due Date"] == "2030-01-01"
