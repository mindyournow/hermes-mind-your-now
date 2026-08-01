# Agent Safety Rules for Mind Your Now Plugin

This document describes the five agent-safety rules enforced by the hermes-mind-your-now plugin to ensure reliable and secure agent interactions with the MYN API.

## Rule 1: Complete Function-Object Schemas

All 14 MYN tools emit complete OpenAI function objects with `name`, `description`, and `parameters` keys. The schema wrapping happens at registration time in `register_myn_tool()`, ensuring Hermes and other Hermes-compatible agents receive fully-formed function schemas that match their dispatch expectations.

**Files:** `src/mind_your_now/tools/__init__.py` (register_myn_tool)  
**Status:** ✓ Implemented across all 14 tools

## Rule 2: Read-Before-Write State-Hash Guards

Writes to the MYN API use the state-hash protocol (MIN-740): read the current state, send the write with an `X-MYN-State-Hash` header, and retry once on 409 conflict using the hash from the error body. This prevents concurrent-write anomalies where a stale value overwrites a fresher one.

Guarded endpoints:
- Tasks: `update`, `complete`, `archive`
- Timers: `cancel`, `snooze`
- Debrief: `apply_correction`, `complete_session`
- Household: `chore_complete`
- Calendar: implicit (operations use /standalone-events which gates internally)

**Files:**
- `src/mind_your_now/client.py` (guarded_write, _write_with_hash, _hash_from_conflict)
- Test coverage: `tests/test_client.py` (18 tests), per-tool tests in `tests/test_tools_*.py`

**Status:** ✓ Implemented with comprehensive test coverage

## Rule 3: No Advertised-But-Ignored Parameters

Every parameter in a tool's schema must be honored in the handler. No silent dropping of inputs. The planning tool was refactored to strip all ignored parameters (goal, constraints, tasks, date, etc.) from its schema, keeping only `action` and `spreadOverDays`.

**Files:**
- `src/mind_your_now/tools/planning.py` (PLANNING_SCHEMA)
- Module docstring records that scoped planning is blocked on MIN-932

**Status:** ✓ Implemented with docstring note

## Rule 4: DryRun on Bulk Actions

Bulk actions (`delete_checked`, `convert_to_tasks` in lists; `schedule_all`, `reschedule` in planning) support `dryRun: true` to preview changes without applying them. The response includes the affected task/item set and a count.

Caveats:
- Planning `dryRun` cannot preview the engine's scheduling decisions (blocked by MIN-932); only the task set affected is shown.
- dryRun is not item-scoped (no `itemIds` filter) — it applies to the full user set. Item scoping is tracked as MIN-932.

**Files:**
- `src/mind_your_now/tools/lists.py` (delete_checked, convert_to_tasks)
- `src/mind_your_now/tools/planning.py` (schedule_all, reschedule)
- Test coverage: `tests/test_ac_verification.py`

**Status:** ✓ Implemented with caveats documented

## Rule 5: Redacted Tool Output

All tool output is redacted at the `tool_result()` chokepoint. Secret-shaped keys (`token`, `apikey`, `secret`, `password`, `refreshtoken`, etc., case-insensitive) have their values replaced with `[REDACTED]`, recursively through nested dicts and lists. Non-secret keys (e.g., `email`, `username`) are left intact.

**Files:**
- `src/mind_your_now/tools/__init__.py` (_redact_secrets, tool_result)
- Test coverage: `tests/test_ac_verification.py` (test_wi9b_ac1_tool_result_redacts_secrets)

**Status:** ✓ Implemented with comprehensive test

## Cross-Tool Consistency

These rules are applied uniformly across all tools:
- Calendar events use ISO 8601 `startTime`/`endTime` (not deprecated `startDateTime`)
- Tasks, projects, memory, and other list operations support client-side `limit` with truncate markers
- Memory `search` is renamed to `recall_relevant` with `search` as a deprecated alias (semantic recall, not exact match)
- Deprecated functions are documented in module docstrings with tracking issue references (MIN-932, MIN-933)

## Dependencies and Blocked Items

The following improvements are blocked by MIN-932 (scoped planning and search):
- Filtered-search capability in memory (`min-932` item 2)
- Scoped planning with per-task/per-date control
- Item-scoped dryRun for bulk list operations
- Planning engine decision preview for dryRun

The habits `reminders` action was removed; restoration is blocked on MIN-932 and cross-references MIN-883.

Project graph scoping is tracked as MIN-933.
