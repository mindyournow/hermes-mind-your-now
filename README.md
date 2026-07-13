# Hermes Mind Your Now plugin

Python plugin that brings Mind Your Now (MYN) into [Hermes](https://github.com/NousResearch/hermes-agent).

This is the initial scaffold. See the MIN-850 PRD for the full implementation plan.

## Install

```bash
pip install hermes-mind-your-now
```

Then enable it:

```bash
hermes plugins enable mind-your-now
```

## Configuration

Set `MYN_API_KEY` (required). Optionally set `MYN_BASE_URL`, `MYN_AGENT_NAME`, and `MYN_CHANNEL`.
