# HEARTBEAT.md

> This file is the **single source of truth** for all reminders, alarms, scheduled tasks, and recurring tasks. When a heartbeat event triggers, the agent reads this file and performs any pending tasks.

---

<!--
### 📋 Instructions for the Agent
- ALL reminders, alarms, one-time tasks, and recurring tasks go in this file.
- **STATE TRACKING**: When you complete a **Recurring Task**, you MUST append a `[LAST COMPLETED: YYYY-MM-DD]` tag to the end of that task line (or update the existing tag if one is present).
- Before running a recurring task, check for a completion tag for the current time period. If present, skip it.
- After completing a one-time reminder, remove it from the `One-time Reminders` section.
- Do NOT track task progress here — use this file ONLY for definitions and final completion tags.
- Do NOT modify this file unless the user asks you to add, modify, or remove tasks, or when adding completion tags.
- When adding a new task, clarify with the user: What exactly? When? Recurring or one-time?
-->

### Recurring Tasks
Add recurring daily, hourly, or periodic tasks below. Only remove them if the user asks you to.

<!--
  Example:
  - Between 9am and 10am EST, post a meme in the #general channel
  - Every evening at 5:30pm EST, check in on the Discord chat
-->

### One-time Reminders
Add one-time reminders and tasks in this section. Remove them after they are completed.

<!--
  Example:
  - Remind Dan to call the dentist at 2:00pm EST on 2026-02-20
  - DM sabrina to clean her desk at 3:30pm EST
-->
