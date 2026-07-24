# Hermes Mind Your Now

`hermes-mind-your-now` gives [Hermes](https://github.com/NousResearch/hermes-agent) agents access to Mind Your Now (MYN): tasks, debriefs, calendars, habits, lists, search, timers, Kaia memories, household data, projects, planning, YNAB, and A2A pairing.

It targets Hermes 0.18+ on Python 3.11–3.13 and uses the existing MYN REST API with an `AGENT_FULL` API key. No MYN backend changes are required.

## Hermes vs. OpenClaw

| | Hermes plugin | OpenClaw plugin |
|---|---|---|
| Package | `hermes-mind-your-now` | `@mind-your-now/myn` |
| Runtime | Python 3.11–3.13 | Node.js 20+ / TypeScript |
| Plugin registration | `hermes_agent.plugins` entry point or directory drop | OpenClaw extension manifest |
| Memory injection | `pre_llm_call`, inserted into the user message | `before_prompt_build`, inserted into prompt context |
| Tool names | The same 14 `myn_*` names | The same 14 `myn_*` names |

The matching names and action vocabulary let prompts and examples move between Hermes and OpenClaw without translation.

## Install

### pip package

```bash
pip install hermes-mind-your-now
hermes plugins enable mind-your-now
hermes plugins list
```

### Directory drop

Copy `plugin.yaml` and the `mind_your_now/` package to:

```text
~/.hermes/plugins/mind-your-now/
```

Then enable `mind-your-now` in Hermes. Directory-drop installs do **not** install dependencies: the general Hermes plugin loader ignores a `pip_dependencies` manifest key. `httpx` must already be importable in the Hermes environment. A stock Hermes installation already includes it; custom environments must install it separately.

## Configuration

Create an MYN API key in **Settings → API Keys** with scope `AGENT_FULL`, then set it as an environment variable:

```bash
export MYN_API_KEY='myn_...'
export MYN_BASE_URL='https://api.mindyournow.com' # optional
export MYN_AGENT_NAME='Hermes/hermes-eltmon'       # optional
export MYN_CHANNEL='telegram:eltmon'               # optional
```

You can also use `~/.hermes/mind-your-now.json`:

```json
{
  "api_key": "myn_...",
  "base_url": "https://api.mindyournow.com",
  "agent_name": "Hermes/hermes-eltmon",
  "channel": "telegram:eltmon"
}
```

Precedence, from lowest to highest, is built-in defaults → `~/.hermes/mind-your-now.json` → environment variables. Secrets do not belong in Hermes's global `config.yaml`.

Defaults:

- `base_url`: `https://api.mindyournow.com`
- `agent_name`: `Hermes`
- `channel`: `hermes`

Without `MYN_API_KEY`, the plugin still loads, but all 14 tools are hidden from the model and independently reject direct dispatch.

## Tool reference

All tools use the Hermes toolset `mind-your-now`.

| Tool | Actions |
|---|---|
| `myn_tasks` | `list`, `get`, `create`, `update`, `complete`, `archive`, `search` |
| `myn_debrief` | `status`, `generate`, `get`, `apply_correction`, `complete_session` |
| `myn_calendar` | `list_calendars`, `list_events`, `get_event`, `create_event`, `update_event`, `delete_event`, `move_event`, `meetings` |
| `myn_habits` | `streaks`, `skip`, `chains`, `schedule`, `reminders` |
| `myn_lists` | `get`, `add`, `toggle`, `bulk_add`, `update`, `delete`, `delete_checked`, `convert_to_tasks` |
| `myn_search` | `search` |
| `myn_timers` | `create_countdown`, `create_alarm`, `list`, `cancel`, `snooze`, `pomodoro` |
| `myn_memory` | `remember`, `recall`, `forget`, `search` |
| `myn_profile` | `get_info`, `get_goals`, `update_goals`, `preferences` |
| `myn_household` | `members`, `invite`, `chores`, `chore_schedule`, `chore_complete` |
| `myn_projects` | `list`, `get`, `create`, `move_task` |
| `myn_planning` | `plan`, `schedule_all`, `reschedule` |
| `myn_a2a_pairing` | `pair`, `status`, `unpair`, `redeem_invite`, `ping`, `send_message`, `get_agent_card` |
| `myn_ynab` | Budget, transaction, scheduled-transaction, analytics, connection, split, and category-management actions matching `@mind-your-now/myn` |

Timers created through `myn_timers` include `sourceAgentName` and `sourceChannel`, so MYN can attribute reminders to the Hermes agent that created them.

## Kaia memory injection

On each Hermes turn, the plugin queries:

```text
GET /api/v1/agent/memories/context?query=<user-message>&limit=10
```

Relevant memories are added through Hermes's `pre_llm_call` hook under:

```markdown
## What you know about this user (from Kaia Memory)
```

Hermes inserts this context into the user message, not the system prompt, which preserves Hermes's prompt-cache prefix. API failures log a warning and never block the turn.

## `/myn` command

```text
/myn status
/myn pair ABC-12345
/myn unpair
```

- `status` reports `api_key_present`, `base_url`, and `paired_a2a`.
- `pair` redeems the invite through `POST /api/v1/agent/redeem-invite` and stores the returned credential at `~/.hermes/mind-your-now/a2a.json` with mode `0600`.
- `unpair` removes that local credential.

Create the invite in the MYN UI first. Invite codes use three uppercase letters, a dash, and five digits, such as `ABC-12345`.

## Security posture

The Python port carries the OpenClaw plugin's security fixes forward and adds the Hermes-specific dispatch guard:

- **S1 — TLS enforcement:** API URLs must use HTTPS; plain HTTP is accepted only for `localhost` and `127.0.0.1`. Config loading and the HTTP client both enforce this.
- **S2 — No mass assignment:** task updates pass through an explicit field allowlist, and rejected fields are named in the result.
- **S3 — Safe query parameters:** all query strings use `httpx` `params=` rather than string concatenation.
- **S4 — A2A scheme guard:** configured, outbound, and returned A2A endpoints receive the same HTTPS check.
- **S5 — Data minimization:** memory search uses the server-side context endpoint instead of downloading every memory.
- **S6 — Visible degradation:** recoverable failures log warnings; no degraded path silently disappears.
- **S7 — Handler self-guarding:** each tool checks configuration inside its handler because Hermes's `check_fn` controls visibility but does not gate dispatch.
- **S8 — Credential permissions:** MYN config and A2A credential writers enforce mode `0600`.

## Deploy to Fly.io

For `hermes-eltmon`, build and upload a wheel to the persistent volume:

```bash
python -m build
fly sftp put -a hermes-eltmon dist/hermes_mind_your_now-0.1.0-py3-none-any.whl /opt/data/plugins/hermes_mind_your_now-0.1.0-py3-none-any.whl
fly ssh console -a hermes-eltmon -s -C "pip install --target /opt/data/python-pkgs /opt/data/plugins/hermes_mind_your_now-0.1.0-py3-none-any.whl"
fly secrets set -a hermes-eltmon MYN_API_KEY='myn_...'
```

Ensure `/opt/data/python-pkgs` is on `PYTHONPATH`, enable `mind-your-now` in Hermes, restart the Hermes process, and verify with:

```bash
hermes plugins list
hermes tools mind-your-now
```

End-to-end verification should cover a real schedule query, a Kaia memory hit, a reminder whose MYN provenance begins with `Hermes/`, and `/myn status`.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e . pytest build
.venv/bin/pytest -q
```

The tests use `httpx.MockTransport`; they make no real network requests and contain no sleeps.

## License

MIT
