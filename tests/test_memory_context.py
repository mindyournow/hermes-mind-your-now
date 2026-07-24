import logging

from mind_your_now.behavioral_guidelines import BEHAVIORAL_GUIDELINES
from mind_your_now.memory_context import (
    build_pre_llm_call_hook,
    fetch_memory_context,
    format_memories_for_prompt,
)


EXPECTED_GUIDELINES = """## Mind Your Now — Agent Behavioral Guidelines

### Calendar Intelligence
- When creating events involving household members, use `calendarName: "Family"` or let the plugin auto-detect the family/shared calendar. Do NOT default to the personal "primary" calendar for shared events.
- When the user mentions someone by name in the context of a shared activity (e.g. "church with Martha", "dinner with family"), infer they should be included as attendees. However, if the context is about discussing someone (e.g. "prepare notes about John's review"), do NOT add them — use judgment about whether the person is a participant or a subject.
- Use `list_calendars` to discover available calendars if unsure which one to use.

### Task + Calendar Event Linking
- When creating a calendar event, ALSO create a linked task (via myn_tasks) unless the user explicitly says not to. Use the same calendarId for both.
- When creating a task for a specific date/time activity, ALSO create a calendar event for it.

### Scheduling Defaults
- ALWAYS set `isAutoScheduled: true` when creating tasks unless the user says otherwise.
- Pick appropriate `scheduleNames` based on when the task should happen. Common schedules: "Morning", "Afternoon", "Evening", "Daytime", "Weekdays", "Weekends". If no specific time is indicated, the system will apply the user's default schedule(s) automatically.
- Think about WHEN the task should happen: church on Sunday morning → ["Morning"], work meeting → ["Weekdays"], general errand → let the default apply.

### Household Awareness
- When a user mentions a family member by first name, recognize them as a household member.
- For shared activities, prefer the family calendar and include relevant household members as attendees.

### Timers & Alarms
- Timers and alarms are delivered as push notifications to the USER's phone/device. They are NOT notes-to-self for agents.
- Only create a timer or alarm when the user explicitly requests a reminder, timer, or alarm.
- Do NOT create timers to schedule your own future actions or as internal bookkeeping.
- Timer names should be short, user-friendly descriptions (e.g. "Laundry", "Take medicine"), NOT instructions to yourself.
"""


class StubClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if self.error:
            raise self.error
        return self.response


def memory_item():
    return {
        "id": "memory-1",
        "type": "preference",
        "content": "Prefers morning appointments",
        "confidence": 0.87,
        "topics": ["scheduling"],
    }


def test_fetch_parses_response():
    client = StubClient({"items": [memory_item()], "total": 1})

    result = fetch_memory_context(client, "schedule today")

    assert result == [memory_item()]
    assert client.calls == [
        (
            "/api/v1/agent/memories/context",
            {"limit": 10, "query": "schedule today"},
        )
    ]


def test_format_block_is_self_introducing():
    block = format_memories_for_prompt([memory_item()])

    assert block.startswith("## What you know about this user (from Kaia Memory)")
    assert "- [preference] Prefers morning appointments (confidence: 87%)" in block


def test_hook_returns_none_when_no_key():
    client = StubClient({"items": [memory_item()]})
    hook = build_pre_llm_call_hook(client, None)

    assert hook(user_message="hello") is None
    assert client.calls == []


def test_hook_returns_none_when_memories_are_empty():
    client = StubClient({"items": [], "total": 0})
    hook = build_pre_llm_call_hook(client, "myn-key")

    assert hook(user_message="hello") is None


def test_hook_returns_none_and_warns_on_api_error(caplog):
    client = StubClient(error=RuntimeError("backend unavailable"))
    hook = build_pre_llm_call_hook(client, "myn-key")

    with caplog.at_level(logging.WARNING, logger="mind_your_now.memory_context"):
        result = hook(user_message="hello")

    assert result is None
    assert "[myn] memory injection failed: backend unavailable" in caplog.text


def test_hook_returns_context_dict():
    client = StubClient({"items": [memory_item()], "total": 1})
    hook = build_pre_llm_call_hook(client, "myn-key")

    result = hook(user_message="what do you know about me?")

    assert result["context"].startswith(BEHAVIORAL_GUIDELINES)
    assert "## What you know about this user (from Kaia Memory)" in result["context"]


def test_behavioral_guidelines_match_openclaw_verbatim():
    assert BEHAVIORAL_GUIDELINES == EXPECTED_GUIDELINES
