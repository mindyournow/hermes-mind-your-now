# MIN-934 development verification

This records the dev-only end-to-end verification run for habit reminder settings. The run used the development API container `myn-main-api-1` on port 7000; it did not contact `api.mindyournow.com` or any other production endpoint.

## Result

**PASS — the Hermes plugin persisted reminder settings through the unified-task guarded PATCH, both plugin and direct API reads returned the persisted values, and the scheduled `HabitReminderService` scan ran at the matching customer-local reminder time.** Push delivery was not claimed or required for this run.

- Run started: `2026-08-02T00:08:02-04:00`
- Customer time zone: `America/New_York`
- Disposable habit: `a337a9e8-a07d-4001-a1dd-0d6b297ce260`
- Configured reminder time: `00:15`
- Matching cron pass: `2026-08-02T00:15:00-04:00` (`2026-08-02T04:15:00Z`)

## Persistence evidence

The Hermes `myn_habits` reminders action initially read the disposable habit through `GET /api/v2/unified-tasks/{habitId}`:

```json
{
  "habitId": "a337a9e8-a07d-4001-a1dd-0d6b297ce260",
  "reminderEnabled": false,
  "reminderTime": null
}
```

The same action then set `enableReminders=true` and `reminderTime=00:15`. The plugin performed its guarded read-before-write flow against `/api/v2/unified-tasks/{habitId}` and the API returned the real task fields:

```json
{
  "id": "a337a9e8-a07d-4001-a1dd-0d6b297ce260",
  "taskType": "HABIT",
  "reminderEnabled": true,
  "reminderTime": "00:15"
}
```

A second plugin read returned the persisted settings:

```json
{
  "habitId": "a337a9e8-a07d-4001-a1dd-0d6b297ce260",
  "reminderEnabled": true,
  "reminderTime": "00:15"
}
```

A direct authenticated read from the development API independently returned the same values and a non-empty `stateHash`:

```json
{
  "id": "a337a9e8-a07d-4001-a1dd-0d6b297ce260",
  "taskType": "HABIT",
  "reminderEnabled": true,
  "reminderTime": "00:15",
  "stateHashPresent": true
}
```

No API key or state-hash value is included in this artifact.

## Scheduler pickup evidence

The disposable habit remained a `HABIT` with `reminderEnabled=true` and `reminderTime=00:15` when the customer's clock reached 00:15. At that instant, the development API emitted the scheduled service log:

```text
2026-08-02 04:15:00.002 [MessageBroker-1] INFO  c.myn.services.HabitReminderService - Processing habit reminders
```

`HabitReminderService.processHabitReminders()` begins that pass by querying `findByTaskTypeAndReminderEnabledTrue(TaskType.HABIT)`, then evaluates each selected habit against the owner's local time. The direct API re-read immediately after the pass still showed the disposable habit eligible with `reminderEnabled=true` and `reminderTime=00:15`. This proves the development scheduler's enabled-habit scan ran while the plugin-persisted reminder matched its selection and time criteria. The run did not emit a `Sent reminder for habit ...` line, so this artifact records scheduler pickup rather than claiming remote push delivery.

## Cleanup

Cleanup completed after the cron pass:

- The Hermes plugin set `reminderEnabled=false` through the same guarded unified-task PATCH flow.
- A direct development API read confirmed `reminderEnabled=false`.
- The disposable habit was deleted; a follow-up GET returned HTTP 404.
- The temporary `AGENT_FULL` development API key was revoked with HTTP 204.
- Temporary files containing token or API-key responses were removed.
