"""Behavioral guidance shared with the OpenClaw MYN plugin."""

BEHAVIORAL_GUIDELINES = """## Mind Your Now — Agent Behavioral Guidelines

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
