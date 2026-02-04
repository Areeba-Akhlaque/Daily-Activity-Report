# Agent Instructions

> This file is mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same instructions load in any AI environment.

You operate within a 3-layer architecture that separates concerns to maximize reliability. LLMs are probabilistic, whereas most business logic is deterministic and requires consistency. This system fixes that mismatch.

## The 3-Layer Architecture

**Layer 1: Directive (What to do)**
- SOPs written in Markdown, live in `directives/`
- Define the goals, inputs, tools/scripts to use, outputs, and edge cases
- Natural language instructions, like you'd give a mid-level employee

**Layer 2: Orchestration (Decision making)**
- This is you. Your job: intelligent routing.
- Read directives, call execution tools in the right order, handle errors, ask for clarification, update directives with learnings
- You're the glue between intent and execution

**Layer 3: Execution (Doing the work)**
- Deterministic Python scripts in `execution/`
- Environment variables, API tokens, etc are stored in `.env`
- Handle API calls, data processing, file operations, database interactions
- Reliable, testable, fast

## Project Overview: Pvragon Activity Tracker

This project tracks team activity across 5 platforms:
1. **ClickUp** - Task management (created, updated, completed, comments, chats)
2. **GitHub** - Code activity (pushes, PRs, issues, comments)
3. **Google Workspace** - Drive edits and Gmail received
4. **Figma** - Design file updates
5. **Backendless** - Console activity (deployments, table edits, etc.)

Data flows to a Google Sheet with these tabs:
- `Backendless_Activity` - Raw Backendless events
- `Clickup_Activity` - Raw ClickUp events
- `Figma_Activity` - Raw Figma events
- `GitHub_Activity` - Raw GitHub events
- `GoogleWorkspace_Activity` - Raw Google Workspace events
- `Daily Audit` - Unified matrix of all activity
- `Activity Time Analysis` - Work hours and break analysis
- `Event Types Reference` - Glossary of all event types

## Operating Principles

**1. Check for tools first**
Before writing a script, check `execution/` per your directive. Only create new scripts if none exist.

**2. Self-anneal when things break**
- Read error message and stack trace
- Fix the script and test it again
- Update the directive with what you learned (API limits, timing, edge cases)

**3. Update directives as you learn**
Directives are living documents. When you discover API constraints, better approaches, common errors, or timing expectations—update the directive.

**4. NEVER modify Google Sheet data directly**
The sheets are the source of truth. Only the execution scripts should write to them. Do not manually edit sheet data.

## File Organization

```
├── .env                          # API keys and configuration
├── .tmp/                         # Temporary files (not committed)
├── credentials.json              # Google OAuth (not committed)
├── token.json                    # Google OAuth token (not committed)
│
├── directives/                   # Layer 1: SOPs
│   ├── fetch_clickup_activity.md
│   ├── fetch_github_activity.md
│   ├── fetch_google_workspace_activity.md
│   ├── fetch_figma_activity.md
│   ├── fetch_backendless_activity.md
│   ├── generate_daily_audit.md
│   ├── refresh_dashboard.md
│   ├── send_daily_email.md
│   └── daily_workflow.md
│
├── execution/                    # Layer 3: Scripts
│   ├── fetch_clickup.py
│   ├── fetch_github.py
│   ├── fetch_google_workspace.py
│   ├── fetch_figma.py
│   ├── fetch_backendless.py
│   ├── refresh_dashboard.py
│   ├── send_daily_email.py
│   └── run_daily_workflow.py
│
└── dashboard/                    # Interactive dashboard
    ├── index.html
    ├── data.json
    └── Logo.png
```

## Daily Workflow

The system runs daily at **7:00 PM PST** via GitHub Actions:

1. Fetch all platform data → Update source tabs
2. Generate Daily Audit → Unified matrix
3. Refresh Dashboard → Export to JSON
4. Send Email Summary → To stakeholders

## Key Contacts

- **areeba@pvragon.com** - Receives daily summary
- **jaime@pvragon.com** - Receives daily summary

## Summary

You sit between human intent (directives) and deterministic execution (Python scripts). Read instructions, make decisions, call tools, handle errors, continuously improve the system.

Be pragmatic. Be reliable. Self-anneal.
